#!/usr/bin/env python3
"""UDP health-reporting agent. Default mode creates controlled simulated metrics.

- Battery starts at a random level and drains 1% every 4 seconds (configurable).
- When the battery reaches 0 the server goes into CHARGING mode: CPU and memory
  drop to 0, and for 30 seconds the battery climbs back up to 50%. It keeps
  sending reports the whole time (so it is never "unresponsive"), then it
  starts working again and the battery drains as before.
- Occasionally (default: once per 60 s window) CPU or memory spikes to unhealthy levels.
"""
import argparse
import json
import random
import socket
import time

BATTERY_DRAIN_PERCENT = 1.0   # percent lost...
BATTERY_DRAIN_SECONDS = 4.0   # ...every this many seconds (default; see --battery-drain-seconds)
CHARGE_SECONDS = 30.0         # how long a full recharge takes
CHARGE_TO_PERCENT = 50.0      # battery level the server comes back at


def main():
    parser = argparse.ArgumentParser(description="UDP health-reporting agent")
    parser.add_argument("--name", required=True, help="Unique simulated server name")
    parser.add_argument("--server", required=True, help="Monitoring server IP")
    parser.add_argument("--port", type=int, default=9999)
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument("--battery-drain-seconds", type=float, default=BATTERY_DRAIN_SECONDS,
                        help="Battery loses 1%% every this many seconds")
    parser.add_argument("--charge-seconds", type=float, default=CHARGE_SECONDS,
                        help="Seconds spent charging after the battery hits 0")
    parser.add_argument("--charge-to", type=float, default=CHARGE_TO_PERCENT,
                        help="Battery percent the server returns at after charging")
    parser.add_argument("--spike-period", type=float, default=60.0,
                        help="One random CPU-or-memory spike per window of this many seconds")
    parser.add_argument("--spike-duration", type=float, default=6.0,
                        help="How long each spike lasts, in seconds")
    parser.add_argument("--high-cpu", action="store_true",
                        help="Demo mode: generate CPU values above the alert threshold")
    parser.add_argument("--high-memory", action="store_true",
                        help="Demo mode: generate memory values above the alert threshold")
    args = parser.parse_args()

    spike_period = max(args.spike_period, 1.0)
    spike_duration = min(max(args.spike_duration, 0.0), spike_period)
    charge_seconds = max(args.charge_seconds, 0.1)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sequence = 0
    battery = None          # decided randomly on the first tick
    last_tick = None        # monotonic time of the previous tick
    state = "running"       # "running" or "charging"
    charge_start = None

    def plan_spike(window_start):
        start = window_start + random.uniform(0.0, spike_period - spike_duration)
        return start, random.choice(["cpu", "memory"])

    window_start = time.monotonic()
    spike_start, spike_metric = plan_spike(window_start)

    print(f"[AGENT] {args.name} sending UDP reports to {args.server}:{args.port}; "
          f"interval={args.interval}s, spike every ~{spike_period:.0f}s", flush=True)
    try:
        while True:
            sequence += 1
            now = time.monotonic()

            # --- battery: random start, steady drain, then charge back up ---
            if battery is None:
                battery = random.uniform(20.0, 100.0)
            elif state == "running":
                elapsed = now - last_tick
                battery -= (elapsed / max(args.battery_drain_seconds, 0.1)) * BATTERY_DRAIN_PERCENT
                if battery <= 0.0:
                    battery = 0.0
                    state = "charging"
                    charge_start = now
                    print(f"[CHARGING] {args.name} battery empty - charging for "
                          f"{charge_seconds:.0f}s", flush=True)
            else:  # charging
                progress = (now - charge_start) / charge_seconds
                if progress >= 1.0:
                    battery = args.charge_to
                    state = "running"
                    print(f"[BACK ONLINE] {args.name} charged to {battery:.0f}% - "
                          f"working again", flush=True)
                else:
                    battery = args.charge_to * progress
            last_tick = now
            charging = state == "charging"

            # --- spike scheduling (one per window) ---
            while now >= window_start + spike_period:
                window_start += spike_period
                spike_start, spike_metric = plan_spike(window_start)
            spiking = (not charging) and spike_start <= now < spike_start + spike_duration

            if charging:
                cpu = memory = 0.0
                processes = "charging"
                packets_sent = packets_received = 0
            else:
                cpu = (random.uniform(84, 98)
                       if (args.high_cpu or (spiking and spike_metric == "cpu"))
                       else random.uniform(10, 65))
                memory = (random.uniform(88, 97)
                          if (args.high_memory or (spiking and spike_metric == "memory"))
                          else random.uniform(25, 78))
                processes = random.choice(["web-service:running", "database:running",
                                           "worker:running", "scheduler:running"])
                packets_sent = random.randint(100, 1500)
                packets_received = random.randint(100, 1500)

            report = {
                "host": args.name,
                "sequence": sequence,
                "sent_at": time.time(),
                "state": state,
                "cpu_percent": round(cpu, 2),
                "memory_percent": round(memory, 2),
                "battery_percent": round(battery, 2),
                "processes": processes,
                "packets_sent": packets_sent,
                "packets_received": packets_received,
                "mode": "simulated"
            }
            try:
                sock.sendto(json.dumps(report).encode("utf-8"), (args.server, args.port))
                tag = ""
                if charging:
                    tag = " [CHARGING]"
                elif spiking:
                    tag = f" [SPIKE:{spike_metric.upper()}]"
                print(f"[SENT] {args.name} seq={sequence} CPU={cpu:.1f}% "
                      f"MEM={memory:.1f}% BATTERY={battery:.1f}%{tag}", flush=True)
            except OSError as exc:
                print(f"[ERROR] {args.name}: {exc}", flush=True)

            time.sleep(args.interval)
    except KeyboardInterrupt:
        print(f"\n[AGENT] {args.name} stopped.", flush=True)
    finally:
        sock.close()


if __name__ == "__main__":
    main()
