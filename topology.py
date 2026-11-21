#!/usr/bin/env python3
"""Mininet topology and automated UDP health-monitor demo.

Usage:
    sudo python3 topology.py --servers 10
    sudo python3 topology.py --servers 100
    sudo python3 topology.py            # asks how many servers you want

Creates N server hosts (h1..hN, agents named server-1..server-N) plus one
monitor host ("mon") that receives and logs the reports from all of them.

Scaling notes (what makes 100 servers work):
  * waits until the switch is really connected to the Ryu controller
  * turns IPv6 off on every host (removes a flood of neighbour-discovery packets)
  * gives every server a static ARP entry for the monitor (no ARP storm at start-up)
  * lets the monitor announce itself so the controller learns its MAC first
  * starts agents in small batches, at low CPU priority, so Ryu stays responsive
"""
import argparse
import json
import os
import re
import resource
import socket
import subprocess
import sys
import time
from mininet.net import Mininet
from mininet.node import RemoteController, OVSSwitch
from mininet.cli import CLI
from mininet.log import setLogLevel, info

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join(BASE_DIR, "health_reports.csv")
INFO_PATH = os.path.join(BASE_DIR, "monitor_info.json")
MAX_SERVERS = 100
BATCH_SIZE = 10          # agents started per batch
BATCH_PAUSE = 1.0        # seconds between batches
CONTROLLER_IP = "127.0.0.1"
CONTROLLER_PORT = 6653


def ask_server_count(default=3):
    while True:
        raw = input(f"How many servers do you want? (1-{MAX_SERVERS}) [{default}]: ").strip()
        if raw == "":
            return default
        if raw.isdigit() and 1 <= int(raw) <= MAX_SERVERS:
            return int(raw)
        print(f"Please enter a whole number between 1 and {MAX_SERVERS}.")


def _sh(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True).stdout


def safe_cleanup():
    """Like `sudo mn -c`, but it does NOT kill ryu-manager.

    (Mininet's own cleanup() runs `killall ... ryu-manager`, which kills the
    controller you started in the other terminal.)
    """
    info("*** Cleaning up leftovers from earlier runs (leaving Ryu running)\n")
    _sh("killall -9 mnexec 2>/dev/null")
    _sh("pkill -9 -f 'mininet:' 2>/dev/null")
    for bridge in _sh("ovs-vsctl --timeout=1 list-br").split():
        _sh(f"ovs-vsctl --timeout=1 --if-exists del-br {bridge}")
    for link in set(re.findall(r"([-_.A-Za-z0-9]+-eth[0-9]+)", _sh("ip link show"))):
        _sh(f"ip link del {link} 2>/dev/null")


def wait_for_controller(switch_name="s1", timeout=15.0, poll=0.2):
    """Fast readiness check.

    Mininet's waitConnected() polls slowly (two ovs-vsctl calls every 0.5 s, which
    gets slow with ~90 ports). Instead we ask the switch directly for its flow
    table every 0.2 s: once Ryu has connected it installs the table-miss rule
    (output to CONTROLLER), and that is the moment the network is ready.
    Returns the seconds it took, or None on timeout.
    """
    start = time.time()
    last_report = start
    while time.time() - start < timeout:
        out = _sh(f"ovs-ofctl -O OpenFlow13 dump-flows {switch_name} 2>/dev/null")
        if "CONTROLLER" in out:
            return time.time() - start
        if time.time() - last_report >= 3.0:
            last_report = time.time()
            info(f"    ...still waiting ({time.time() - start:.0f}s), "
                 f"OVS is_connected={controller_state()}\n")
        time.sleep(poll)
    return None


def controller_listening(port, ip=CONTROLLER_IP):
    try:
        with socket.create_connection((ip, port), timeout=1):
            return True
    except OSError:
        return False


def preflight_controller():
    """Fail fast (in 1 second) if Ryu isn't up, instead of building 90 hosts first."""
    if controller_listening(CONTROLLER_PORT):
        return
    if controller_listening(6633):
        sys.exit(f"Ryu is listening on 6633, but this script uses {CONTROLLER_PORT}.\n"
                 f"Restart it with:  ryu-manager --ofp-tcp-listen-port {CONTROLLER_PORT} "
                 f"sdn_controller.py")
    sys.exit(f"No controller is listening on {CONTROLLER_IP}:{CONTROLLER_PORT}.\n"
             "Start Ryu first, in another terminal, and wait for it to finish starting:\n"
             "    ryu-manager sdn_controller.py")


def controller_state():
    """What Open vSwitch itself says about its controller connection."""
    return _sh("ovs-vsctl --timeout=2 --bare --columns=is_connected list Controller").strip() or "?"


def print_controller_diagnostics():
    info("\n================ DIAGNOSTICS (send me this) ================\n")
    info("--- who is listening on the OpenFlow ports:\n")
    info((_sh("ss -ltnp | grep -E ':6653|:6633'") or "(nothing listening)\n"))
    info("--- ryu-manager processes:\n")
    info((_sh("pgrep -af ryu-manager") or "(no ryu-manager process)\n"))
    info("--- Open vSwitch view of its controller:\n")
    info((_sh("ovs-vsctl --timeout=2 --columns=target,is_connected,status list Controller")
          or "(no controller configured)\n"))
    info("--- Open vSwitch view of the switch:\n")
    info((_sh("ovs-ofctl -O OpenFlow13 show s1 2>&1 | head -4") or "(no answer)\n"))
    info("============================================================\n")
    info("ALSO look at the Ryu terminal: it should print 'Installed table-miss rule on\n"
         "switch 1'. If it printed a traceback instead, that traceback is the answer.\n\n")


def raise_fd_limit():
    """100 hosts need far more than the default 1024 open files."""
    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        target = 65536 if hard == resource.RLIM_INFINITY else min(hard, 65536)
        if soft < target:
            resource.setrlimit(resource.RLIMIT_NOFILE, (target, hard))
    except (ValueError, OSError):
        pass


def main():
    parser = argparse.ArgumentParser(description="Mininet health-monitoring demo")
    parser.add_argument("--servers", type=int, help="Number of monitored servers")
    parser.add_argument("--interval", type=float, default=2.0,
                        help="Seconds between agent reports")
    parser.add_argument("--spike-period", type=float, default=60.0,
                        help="Each agent has one CPU/memory spike per this many seconds")
    parser.add_argument("--battery-drain-seconds", type=float, default=4.0,
                        help="Each agent's battery loses 1%% every this many seconds")
    args = parser.parse_args()

    n = args.servers if args.servers is not None else ask_server_count()
    if not 1 <= n <= MAX_SERVERS:
        sys.exit(f"--servers must be between 1 and {MAX_SERVERS}")

    preflight_controller()
    setLogLevel("info")
    raise_fd_limit()

    # Remove leftover interfaces/processes from any earlier crashed run
    safe_cleanup()

    # Start every demo with a clean log so old runs don't show up in the dashboard
    if os.path.exists(CSV_PATH):
        os.remove(CSV_PATH)

    monitor_ip = f"10.0.0.{n + 1}"
    with open(INFO_PATH, "w") as f:
        json.dump({"servers": n, "monitor_name": "mon", "monitor_ip": monitor_ip}, f)

    net = Mininet(controller=None, switch=OVSSwitch, autoSetMacs=True,
                  build=False, waitConnected=False)
    c0 = net.addController("c0", controller=RemoteController,
                           ip=CONTROLLER_IP, port=CONTROLLER_PORT)
    servers = [net.addHost(f"h{i}", ip=f"10.0.0.{i}/24") for i in range(1, n + 1)]
    mon = net.addHost("mon", ip=f"{monitor_ip}/24")
    s1 = net.addSwitch("s1", protocols="OpenFlow13")
    for host in servers + [mon]:
        net.addLink(host, s1)

    info(f"*** Building topology: {n} servers + 1 monitor ({monitor_ip})\n")
    net.build()
    c0.start()
    s1.start([c0])

    info(f"*** Waiting for switch s1 to connect to the Ryu controller "
         f"({CONTROLLER_IP}:{CONTROLLER_PORT})\n")
    waited = wait_for_controller("s1", timeout=20.0)
    if waited is not None:
        info(f"    switch connected after {waited:.1f}s\n")
    else:
        info("\n*** ERROR: the switch never connected to the controller (20s).\n"
             "    - Is `ryu-manager sdn_controller.py` running in another terminal?\n"
             f"    - Is it listening on port {CONTROLLER_PORT}?  Check: ss -ltnp | grep {CONTROLLER_PORT}\n"
             "    - Did Ryu print a traceback when it started?\n")
        print_controller_diagnostics()
        net.stop()
        sys.exit(1)

    info("*** Tuning hosts for scale (IPv6 off, static ARP to the monitor)\n")
    for h in servers + [mon]:
        h.cmd("sysctl -q -w net.ipv6.conf.all.disable_ipv6=1 "
              "net.ipv6.conf.default.disable_ipv6=1")
    for h in servers:
        h.setARP(mon.IP(), mon.MAC())

    info("*** Starting central monitor on mon\n")
    monitor_log = open("/tmp/health_monitor.log", "w")
    monitor = mon.popen([sys.executable, os.path.join(BASE_DIR, "monitor_server.py"),
                         "--host", monitor_ip, "--port", "9999",
                         "--timeout", "8", "--log", CSV_PATH],
                        stdout=monitor_log, stderr=monitor_log)
    time.sleep(1)

    info("*** Announcing the monitor to the controller\n")
    mon.cmd("ping -c 1 -W 2 10.0.0.1")

    info(f"*** Starting {n} health agents in batches of {BATCH_SIZE}\n")
    agent_logs = []
    agents = []
    for i, host in enumerate(servers, start=1):
        name = f"server-{i}"
        log_file = open(f"/tmp/{name}_agent.log", "w")
        agent_logs.append(log_file)
        proc = host.popen(["nice", "-n", "10", sys.executable,
                           os.path.join(BASE_DIR, "health_agent.py"),
                           "--name", name, "--server", monitor_ip,
                           "--port", "9999", "--interval", str(args.interval),
                           "--spike-period", str(args.spike_period),
                           "--battery-drain-seconds", str(args.battery_drain_seconds)],
                          stdout=log_file, stderr=log_file)
        agents.append(proc)
        if i % BATCH_SIZE == 0 or i == n:
            info(f"    started {i}/{n}\n")
            time.sleep(BATCH_PAUSE)

    info("*** Demo is running. Open dashboard_ui.py in another terminal.\n")
    if n > 20:
        info("*** Tip: don't use 'pingall' with this many hosts (it takes ages). "
             "Try:  mon ping -c 3 10.0.0.1   or   h1 ping -c 3 mon\n")
    try:
        CLI(net)
    finally:
        info("*** Stopping agents and monitor\n")
        for proc in agents:
            try:
                proc.terminate()
            except Exception:
                pass
        try:
            monitor.terminate()
        except Exception:
            pass
        for f in agent_logs:
            f.close()
        monitor_log.close()
        net.stop()


if __name__ == "__main__":
    main()
