#!/usr/bin/env python3
"""Central UDP health monitor for the Remote System Health Monitoring project."""
import argparse
import csv
import json
import os
import socket
import threading
import time
from datetime import datetime

PORT_DEFAULT = 9999
CPU_THRESHOLD_DEFAULT = 80.0
MEMORY_THRESHOLD_DEFAULT = 85.0
BATTERY_THRESHOLD_DEFAULT = 30.0
TIMEOUT_DEFAULT = 8.0

def now_text():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def main():
    parser = argparse.ArgumentParser(description="Central UDP health monitoring server")
    parser.add_argument("--host", default="0.0.0.0", help="IP address to bind to")
    parser.add_argument("--port", type=int, default=PORT_DEFAULT)
    parser.add_argument("--cpu-threshold", type=float, default=CPU_THRESHOLD_DEFAULT)
    parser.add_argument("--memory-threshold", type=float, default=MEMORY_THRESHOLD_DEFAULT)
    parser.add_argument("--battery-threshold", type=float, default=BATTERY_THRESHOLD_DEFAULT,
                        help="Battery percentage threshold below which to trigger an alert")
    parser.add_argument("--timeout", type=float, default=TIMEOUT_DEFAULT,
                        help="Seconds without a report before a host is marked unresponsive")
    parser.add_argument("--log", default="health_reports.csv")
    args = parser.parse_args()

    last_seen = {}
    seen_lock = threading.Lock()
    known_hosts = set()
    stop_event = threading.Event()

    # Write the header if the file is missing OR empty
    new_file = (not os.path.exists(args.log)) or os.path.getsize(args.log) == 0
    log_file = open(args.log, "a", newline="", encoding="utf-8")
    writer = csv.writer(log_file)
    if new_file:
        writer.writerow(["received_at", "host", "cpu_percent", "memory_percent", "battery_percent",
                         "processes", "packets_sent", "packets_received",
                         "report_latency_ms", "status"])
        log_file.flush()

    def timeout_watcher():
        alerted = set()
        while not stop_event.wait(1.0):
            current = time.time()
            with seen_lock:
                snapshot = dict(last_seen)
                hosts = set(known_hosts)
            for host in hosts:
                age = current - snapshot.get(host, 0)
                if age > args.timeout and host not in alerted:
                    print(f"[ALERT] {now_text()} {host} is UNRESPONSIVE "
                          f"(no report for {age:.1f}s)", flush=True)
                    alerted.add(host)
                elif age <= args.timeout and host in alerted:
                    print(f"[RECOVERED] {now_text()} {host} is reporting again", flush=True)
                    alerted.remove(host)

    watcher = threading.Thread(target=timeout_watcher, daemon=True)
    watcher.start()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((args.host, args.port))
    sock.settimeout(1.0)
    print(f"[STARTED] UDP monitor listening on {args.host}:{args.port}", flush=True)
    print(f"[CONFIG] CPU warning > {args.cpu_threshold:.0f}%, "
          f"memory warning > {args.memory_threshold:.0f}%, "
          f"battery warning < {args.battery_threshold:.0f}%, "
          f"unresponsive timeout = {args.timeout:.1f}s", flush=True)

    try:
        while True:
            try:
                data, address = sock.recvfrom(65535)
            except socket.timeout:
                continue
            received_at = time.time()
            try:
                report = json.loads(data.decode("utf-8"))
                host = str(report.get("host", address[0]))
                cpu = float(report["cpu_percent"])
                memory = float(report["memory_percent"])
                battery = float(report.get("battery_percent", 100.0))
                processes = str(report.get("processes", "unknown"))
                packets_sent = int(report.get("packets_sent", 0))
                packets_received = int(report.get("packets_received", 0))
                sent_at = float(report.get("sent_at", received_at))
                state = str(report.get("state", "running"))
            except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
                print(f"[INVALID] Bad report from {address}: {exc}", flush=True)
                continue

            latency_ms = max(0.0, (received_at - sent_at) * 1000.0)
            with seen_lock:
                last_seen[host] = received_at
                known_hosts.add(host)

            alerts = []
            if cpu > args.cpu_threshold:
                alerts.append(f"HIGH CPU {cpu:.1f}%")
            if memory > args.memory_threshold:
                alerts.append(f"HIGH MEMORY {memory:.1f}%")
            if battery < args.battery_threshold:
                alerts.append(f"LOW BATTERY {battery:.1f}%")

            status = "ALERT: " + ", ".join(alerts) if alerts else "HEALTHY"
            tag = "ALERT" if alerts else "OK"
            if state == "charging":
                # Battery ran out: the server is recharging (still reporting, CPU/memory = 0)
                status = f"CHARGING ({battery:.0f}%)"
                tag = "CHARGING"
            print(f"[{tag}] {now_text()} host={host} cpu={cpu:.1f}% "
                  f"memory={memory:.1f}% battery={battery:.1f}% processes={processes} "
                  f"packets_sent={packets_sent} packets_received={packets_received} "
                  f"latency={latency_ms:.2f}ms status={status}", flush=True)

            writer.writerow([now_text(), host, cpu, memory, battery, processes,
                             packets_sent, packets_received, f"{latency_ms:.2f}", status])
            log_file.flush()
    except KeyboardInterrupt:
        print("\n[STOPPING] Monitor stopped.", flush=True)
    finally:
        stop_event.set()
        sock.close()
        log_file.close()

if __name__ == "__main__":
    main()
