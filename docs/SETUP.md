# Setup Guide — mini-sdv-platform

Step-by-step instructions for running the full 18-milestone SDV platform from
scratch on Windows 11 + WSL2. Target time: approximately 30 minutes for a clean
machine, excluding the optional Phi-4-mini model download.

> **Path note**: commands below use the owner's path
> `/mnt/c/Users/takum/OneDrive/デスクトップ/Personal-Project/02_mini-sdv-platform project`.
> Replace `takum` with your own Windows username throughout.

---

## 1. Prerequisites Checklist

Verify each item before starting. Items marked (one-time) only need to be done
once per machine.

| Requirement | How to verify |
|---|---|
| Windows 11 (any edition) | `winver` in PowerShell |
| WSL2 enabled | `wsl --status` — should show "Default Version: 2" |
| Ubuntu 24.04 distro installed | `wsl -l -v` — STATUS: Running, VERSION: 2 |
| Custom kernel 6.18 with SocketCAN | `uname -r` inside WSL2 — should contain `6.18` |
| Python 3.10 inside WSL2 | `python3.10 --version` |
| kubectl installed inside WSL2 | `kubectl version --client` |

### Install missing prerequisites

**WSL2 + Ubuntu 24.04** (Windows PowerShell, run as Administrator):
```powershell
wsl --install -d Ubuntu-24.04
```

**Custom kernel (SocketCAN support, one-time)**

The standard Microsoft WSL2 kernel does not include `vcan`, `can`, or
`can_raw` modules. You must build a kernel that includes them. Refer to
`docs/milestone-4/TRD.md` for the full procedure. The short version:

```bash
# In WSL2 Ubuntu — takes ~20 minutes
git clone --depth=1 --branch linux-msft-wsl-6.6.y \
    https://github.com/microsoft/WSL2-Linux-Kernel.git
cd WSL2-Linux-Kernel
make KCONFIG_CONFIG=Microsoft/config-wsl menuconfig
# Enable: Networking → CAN bus → CAN device drivers → Virtual CAN
make -j$(nproc) KCONFIG_CONFIG=Microsoft/config-wsl
cp arch/x86_64/boot/bzImage /mnt/c/Users/<your-username>/wsl-kernel-6.18
```

Then in `C:\Users\<your-username>\.wslconfig`:
```ini
[wsl2]
kernel=C:\\Users\\<your-username>\\wsl-kernel-6.18
```

Restart WSL2: `wsl --shutdown`, then reopen Ubuntu.

**Python 3.10 venv** (inside WSL2):
```bash
sudo apt update && sudo apt install -y python3.10 python3.10-venv python3-pip
python3.10 -m venv ~/sdv-venv
~/sdv-venv/bin/pip install --upgrade pip
~/sdv-venv/bin/pip install \
    kuksa-client highway-env lancedb sentence-transformers \
    paho-mqtt flask influxdb-client opentelemetry-sdk \
    opentelemetry-exporter-otlp-proto-http python-can requests numpy
```

**kubectl** (inside WSL2):
```bash
curl -LO "https://dl.k8s.io/release/$(curl -sL https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl"
sudo install -m 0755 kubectl /usr/local/bin/kubectl
```

---

## 2. One-Time Setup

All commands run in a **WSL2 Ubuntu terminal** unless otherwise noted.

### 2a. Docker Engine inside WSL2

Docker Desktop is NOT used here. Docker Engine runs directly inside WSL2 because
the custom kernel lacks the `ip_tables.ko` module required by Docker Desktop's
networking. Use the nftables-compatible daemon configuration instead.

```bash
# Install Docker Engine (standard method)
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
# Re-login or run: newgrp docker

# Configure daemon to use nftables bridge mode
echo '{"iptables": false, "bridge": "none"}' \
    | sudo tee /etc/docker/daemon.json
```

### 2b. Install k3s

k3s is installed with kube-proxy and Flannel CNI disabled because all pods use
`hostNetwork: true` (the custom kernel lacks bridge networking modules).

```bash
cd "/mnt/c/Users/takum/OneDrive/デスクトップ/Personal-Project/02_mini-sdv-platform project"
bash k8s/scripts/setup-k3s.sh
```

This script:
1. Installs k3s with `--disable-kube-proxy --flannel-backend=none`
2. Copies the kubeconfig to `~/.kube/config`
3. Configures `localhost:5000` as the local image registry
4. Waits until the node is `Ready`

Verify:
```bash
kubectl get nodes   # → NAME   STATUS   ROLES
```

### 2c. Generate TLS certificates

The platform uses mTLS (mutual TLS) between services. Generate a self-signed CA
and per-service certificates:

```bash
bash scripts/generate-certs.sh
# Certificates are written to config/certs/
ls config/certs/    # → ca.crt  ca.key  ai.crt  ai.key  client.crt ...
```

---

## 3. Deploy the Platform

### 3a. Build and push service images

```bash
cd "/mnt/c/Users/takum/OneDrive/デスクトップ/Personal-Project/02_mini-sdv-platform project"

# Start the session bootstrap (loads SocketCAN modules, starts Docker)
bash scripts/setup-wsl2.sh

# Build all service images and push to local registry (localhost:5000)
bash k8s/scripts/build-push.sh
```

Expected output: 11 images pushed (`mqtt-bridge`, `ai-monitor`, `ota-manager`,
`ota-server`, `influxdb-writer`, `webhook-receiver`, `dashboard`,
`highway-env-bridge`, `training-dispatcher`, `alpa-sim`, `ai-monitor-edge`,
`scene-search`).

### 3b. Initialize configuration and secrets

```bash
# Set your Anthropic API key (required for ai-monitor / M5)
export ANTHROPIC_API_KEY="sk-ant-..."

# Optionally set Runpod credentials (training-dispatcher uses dry-run without them)
export RUNPOD_API_KEY=""
export RUNPOD_ENDPOINT_ID=""

bash k8s/scripts/init-config.sh
```

This script copies `config/` to `/var/lib/sdv/config/`, creates the `sdv`
namespace, and creates three Kubernetes Secrets: `tls-certs`, `ai-secrets`,
`runpod-secrets`.

### 3c. Apply all manifests

```bash
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/deployments/
kubectl get pods -n sdv --watch
```

Wait until all pods show `Running`. This typically takes 60–90 seconds on first
start (InfluxDB initialization, Grafana provisioning). Expected pod count: 18+.

---

## 4. Start the Signal Pipeline

The signal pipeline runs outside k3s — these processes communicate with
Databroker (port 55555) and Mosquitto (port 8883) that are running as k8s pods
using hostNetwork.

Open three separate WSL2 terminal tabs:

**Terminal A — CAN Gateway** (reads vcan0, writes to Databroker):
```bash
~/sdv-venv/bin/python \
    "/mnt/c/Users/takum/OneDrive/デスクトップ/Personal-Project/02_mini-sdv-platform project/services/can-gateway/main.py"
```

**Terminal B — ECU Simulator** (writes synthetic CAN frames to vcan0):
```bash
ECU_CONFIG_PATH=/tmp/sdv-ota/ecu_config.json \
    ~/sdv-venv/bin/python \
    "/mnt/c/Users/takum/OneDrive/デスクトップ/Personal-Project/02_mini-sdv-platform project/services/ecu-simulator/main.py"
```

**Terminal C — highway-env Bridge** (M15: physics-based driving simulation):
```bash
~/sdv-venv/bin/python \
    "/mnt/c/Users/takum/OneDrive/デスクトップ/Personal-Project/02_mini-sdv-platform project/services/highway-env-bridge/main.py"
```

The highway-env-bridge replaces the ECU simulator's sinusoidal signals with
physics-based kinematics from the `highway-v0` Gymnasium environment. Run both
or either; they write to the same Databroker VSS paths.

---

## 5. Verify Everything Works

### 5a. Check pod health
```bash
kubectl get pods -n sdv
# All pods should show STATUS=Running, RESTARTS=0 (or low)
```

### 5b. Open dashboards in a Windows browser

| URL | Service | Notes |
|-----|---------|-------|
| `http://localhost:3000` | Grafana | admin / admin (change on first login) |
| `http://localhost:8501` | Streamlit Vehicle Dashboard | Live VSS signals |
| `http://localhost:4040` | Pyroscope | CPU flame graphs (M18) |
| `http://localhost:8086` | InfluxDB | admin / sdv-password |
| `http://localhost:8092/health` | alpa-sim | `{"status":"ok"}` |
| `http://localhost:8093/health` | scene-search | `{"status":"ok","scenes":0,...}` |
| `http://localhost:8090/health` | training-dispatcher | Shows runpod_configured status |
| `http://localhost:8080` | ota-server | OTA package registry |

### 5c. Quick smoke tests

```bash
# Verify live MQTT telemetry (mTLS)
mosquitto_sub -h localhost -p 8883 \
  --cafile config/certs/ca.crt \
  --cert config/certs/dashboard.crt \
  --key config/certs/dashboard.key \
  -t "sdv/vehicle-001/#" -v

# Run AlpaSim evaluation (IDM baseline, 5 episodes)
curl -s -X POST http://localhost:8092/evaluate \
  -H "Content-Type: application/json" \
  -d '{"episodes": 5, "model_tag": "idm-baseline"}' | python3 -m json.tool

# Semantic scene search (after highway-env-bridge has run at least one episode)
curl -s "http://localhost:8093/scenes/search?q=highway+collision&k=3" | python3 -m json.tool

# Submit a dry-run training job (no Runpod key required)
curl -s -X POST http://localhost:8090/jobs \
  -H "Content-Type: application/json" \
  -d '{"algorithm": "ppo", "env_id": "highway-v0", "num_steps": 50000}' | python3 -m json.tool

# Trigger an OTA config release
curl -s -X POST http://localhost:8080/release/1.1.0
```

### 5d. Run unit tests (no external deps required)
```bash
cd "/mnt/c/Users/takum/OneDrive/デスクトップ/Personal-Project/02_mini-sdv-platform project"
~/sdv-venv/bin/pytest -c pytest.ini -v
# Expected: 28 PASSED
```

---

## 6. Optional: Runpod + Phi-4-mini Setup

### 6a. Runpod GPU training (M15/M16)

1. Create a free account at https://runpod.io
2. Go to **Serverless** → **New Endpoint** → select a GPU template with PyTorch
   (A100 or RTX 4090, minimum 20 GB VRAM).
3. Note your **Endpoint ID** (format: `abc123def456`).
4. Generate an **API Key** under account settings.
5. Re-run init-config with your credentials:

```bash
export RUNPOD_API_KEY="your-api-key"
export RUNPOD_ENDPOINT_ID="your-endpoint-id"
bash k8s/scripts/init-config.sh

# Restart training-dispatcher to pick up the new secret
kubectl rollout restart deployment/training-dispatcher -n sdv
kubectl rollout status  deployment/training-dispatcher -n sdv
```

Once configured, `POST /jobs` on port 8090 dispatches real PPO training jobs.
Each 100k-step job on RTX 4090 takes ~8 minutes and costs approximately $0.80.

### 6b. Phi-4-mini ONNX model (M17)

The `ai-monitor-edge` service falls back to rule-based detection when
`/opt/sdv/models/phi4-mini-onnx` is empty. To enable on-device LLM inference:

```bash
# Download and convert Phi-4-mini to ONNX INT4
# Requires ~8 GB download and optimum[onnxruntime] installed
~/sdv-venv/bin/pip install optimum[onnxruntime] transformers

sudo mkdir -p /opt/sdv/models/phi4-mini-onnx
~/sdv-venv/bin/python scripts/onnx-convert.py \
    --output /opt/sdv/models/phi4-mini-onnx

# Restart the edge monitor to load the model
kubectl rollout restart deployment/ai-monitor-edge -n sdv
```

Expected startup log: `Model loaded in X.X s` (typically 10–30 s on first load).
Inference latency: 3–8 seconds per monitoring cycle on CPU (no GPU required).

---

## 7. Troubleshooting

### Docker does not start

Check the daemon log:
```bash
tail -50 /tmp/dockerd.log
```

Common cause: leftover socket from a previous session.
```bash
sudo rm -f /var/run/docker.pid /var/run/docker.sock
sudo dockerd --iptables=false > /tmp/dockerd.log 2>&1 &
```

If you see `iptables` errors, verify that `/etc/docker/daemon.json` contains
`{"iptables": false, "bridge": "none"}`.

### SocketCAN: `RTNETLINK answers: Operation not supported`

The custom kernel is not active. Verify:
```bash
uname -r          # should contain 6.18
modprobe vcan     # should succeed silently
ip link show vcan0
```

If `modprobe vcan` fails with `Module not found`, the kernel was not built with
`CONFIG_CAN_VCAN=m`. Rebuild the kernel with that option enabled (see Section 1).

### k3s: image pull fails (`localhost:5000/sdv/... not found`)

The local registry or build step was skipped. Re-run:
```bash
bash scripts/setup-wsl2.sh     # starts Docker
bash k8s/scripts/build-push.sh # rebuilds and pushes images
kubectl rollout restart deployment -n sdv --all
```

### k3s: pods stuck in `ContainerCreating`

```bash
kubectl describe pod <pod-name> -n sdv
```

Common causes:
- `tls-certs` secret not created — re-run `bash k8s/scripts/init-config.sh`
- VSS config not copied — check that `/var/lib/sdv/config/vss/` exists
- Model hostPath missing — run `sudo mkdir -p /opt/sdv/models/phi4-mini-onnx`

### Grafana shows no data

InfluxDB initialization can take 30–60 seconds. If data still does not appear:
```bash
kubectl logs -n sdv deployment/influxdb-writer --tail=30
kubectl logs -n sdv deployment/influxdb --tail=30
```

Ensure the signal pipeline (ECU Simulator or highway-env-bridge) is running and
writing to Databroker on port 55555.

### MQTT connection refused

Mosquitto uses port 8883 with mTLS. Plaintext connections on port 1883 are
disabled. Always pass the certificate flags when using `mosquitto_sub` or
`mosquitto_pub`. Inside k8s pods, the MQTT_TLS env var controls this.
