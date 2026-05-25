#!/usr/bin/env bash
# scripts/setup-wsl2.sh — WSL2 session bootstrap for mini-SDV platform (M4)
#
# Run once per WSL2 session (modules reset on wsl --shutdown):
#   bash scripts/setup-wsl2.sh
#
# What this script does:
#   1. Loads SocketCAN kernel modules (vcan, can, can_raw)
#   2. Creates and brings up the vcan0 virtual CAN interface
#   3. Loads netfilter modules required by Docker Engine
#   4. Starts dockerd in the background (--iptables=false mode)
#
# Prerequisite: custom 6.18 WSL2 kernel with vcan + nf_tables support
#   (built during M4 setup; activated via C:\Users\takum\.wslconfig)

set -e

echo "=== mini-SDV Platform — WSL2 Bootstrap (M4) ==="

# ── 1. SocketCAN modules ─────────────────────────────────────────────────────
echo "[1/4] Loading CAN kernel modules..."
sudo modprobe can
sudo modprobe can_raw
sudo modprobe vcan
echo "      CAN modules loaded."

# ── 2. vcan0 interface ───────────────────────────────────────────────────────
echo "[2/4] Setting up vcan0..."
if ip link show vcan0 >/dev/null 2>&1; then
    echo "      vcan0 already exists."
else
    sudo ip link add dev vcan0 type vcan
fi
sudo ip link set vcan0 up
echo "      vcan0 is UP."

# ── 3. Netfilter modules for Docker ─────────────────────────────────────────
echo "[3/4] Loading netfilter modules for Docker..."
sudo modprobe nf_tables       2>/dev/null || true
sudo modprobe xt_addrtype     2>/dev/null || true
sudo modprobe nf_nat          2>/dev/null || true
sudo modprobe nft_nat         2>/dev/null || true
sudo modprobe nft_chain_nat   2>/dev/null || true
sudo modprobe nft_masq        2>/dev/null || true
sudo modprobe br_netfilter    2>/dev/null || true
sudo modprobe overlay         2>/dev/null || true
# Switch to nft iptables backend (legacy ip_tables.ko not installed in custom kernel)
sudo update-alternatives --set iptables  /usr/sbin/iptables-nft  2>/dev/null || true
sudo update-alternatives --set ip6tables /usr/sbin/ip6tables-nft 2>/dev/null || true
echo "      Netfilter modules loaded."

# ── 4. Docker Engine ─────────────────────────────────────────────────────────
echo "[4/4] Starting Docker Engine..."
if docker ps >/dev/null 2>&1; then
    echo "      Docker is already running."
else
    # Clean up stale pid/socket if present
    sudo rm -f /var/run/docker.pid /var/run/docker.sock
    # Start dockerd without iptables NAT (uses nftables bridge routing instead)
    sudo dockerd --iptables=false > /tmp/dockerd.log 2>&1 &
    echo "      Waiting for Docker socket..."
    for i in $(seq 1 20); do
        if docker ps >/dev/null 2>&1; then
            echo "      Docker Engine is ready."
            break
        fi
        sleep 2
    done
    if ! docker ps >/dev/null 2>&1; then
        echo "ERROR: Docker failed to start. Check /tmp/dockerd.log"
        tail -20 /tmp/dockerd.log
        exit 1
    fi
fi

echo ""
echo "=== Bootstrap complete. ==="
echo ""
echo "Next steps (all in WSL2 terminal):"
echo "  docker compose up -d                                          # start all services"
echo "  ~/sdv-venv/bin/python services/can-gateway/main.py          # CAN → Databroker"
echo "  ~/sdv-venv/bin/python services/ecu-simulator/main.py        # ECU → CAN"
echo "  candump vcan0                                                 # monitor CAN frames"
echo "  Dashboard: http://localhost:8501"
