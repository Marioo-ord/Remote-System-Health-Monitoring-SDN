# Remote System Health Monitoring using SDN

## Overview

This project implements a UDP-based distributed health monitoring system integrated with Software Defined Networking (SDN).

Multiple simulated servers send CPU, memory, battery, and network statistics to a centralized monitoring server using UDP sockets. The monitoring server analyzes incoming reports, generates alerts, and logs health information for visualization.

A Ryu SDN controller monitors network traffic and prioritizes monitoring packets using OpenFlow rules. The complete system is deployed and tested using Mininet.

## Technologies Used

- Python
- UDP Socket Programming
- Mininet
- Ryu Controller
- OpenFlow 1.3
- Open vSwitch
- Tkinter
- Ubuntu 22.04

## Files

- health_agent.py
- monitor_server.py
- sdn_controller.py
- topology.py
- dashboard_ui.py
- monitor_info.json

## Features

- UDP-based monitoring
- CPU monitoring
- Memory monitoring
- Battery monitoring
- Node failure detection
- SDN traffic prioritization
- Dashboard visualization
- CSV logging
- Scalable Mininet topology

## How to Run

Terminal 1:

source ~/ryu-env/bin/activate
ryu-manager sdn_controller.py

Terminal 2:

sudo python3 topology.py --servers 3

Terminal 3:

python3 dashboard_ui.py

## Dynamic SDN Functionality

The SDN controller identifies monitoring traffic on UDP Port 9999 and installs higher-priority OpenFlow rules. Monitoring traffic receives higher priority than normal network traffic.

## Team Members

- Rudraksh (PES1UG25CS430)
- Pruthviraj (PES1UG25CS397)
- Rachit (PES1UG25CS404)
  
## Conclusion

This project demonstrates the integration of UDP socket programming with Software Defined Networking. It provides centralized monitoring, real-time visualization, dynamic traffic prioritization, and scalable deployment in a Mininet environment.
