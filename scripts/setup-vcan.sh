#!/usr/bin/env bash
# setup-vcan.sh — Initialise virtual CAN bus for mini-sdv-platform M4
#
# Run this script in WSL2 Ubuntu before starting the CAN services.
# It is idempotent — safe to run multiple times.
#
# Usage:
#   bash scripts/setup-vcan.sh
#
# What it does:
#   1. Loads the vcan, can, and can_raw kernel modules
#   2. Creates the vcan0 virtual CAN interface (if not already present)
#   3. Brings the interface UP
#
# After running this script:
#   candump vcan0              — monitor all CAN frames in real time
#   cansend vcan0 100#DEADBEEF — manually send a test frame

set -e

echo "=== mini-SDV Platform — vcan0 Setup ==="

# Load kernel modules
echo "Loading CAN kernel modules..."
sudo modprobe vcan
sudo modprobe can
sudo modprobe can_raw
echo "  vcan, can, can_raw loaded."

# Create vcan0 if it does not exist
if ip link show vcan0 &>/dev/null; then
    echo "  vcan0 already exists — skipping creation."
else
    sudo ip link add dev vcan0 type vcan
    echo "  vcan0 created."
fi

# Bring the interface UP
sudo ip link set up vcan0
echo "  vcan0 is UP."

echo ""
echo "=== vcan0 ready ==="
ip link show vcan0
echo ""
echo "Next steps:"
echo "  1. docker compose up -d"
echo "  2. cd services/can-gateway && python main.py   (WSL2 terminal 1)"
echo "  3. cd services/ecu-simulator && python main.py (WSL2 terminal 2)"
echo "  4. candump vcan0                               (WSL2 terminal 3)"
