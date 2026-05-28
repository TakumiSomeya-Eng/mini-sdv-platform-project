# M14 Architecture Review & Study Guide
## Kubernetes Migration with k3s on WSL2

---

## 1. What We Built (3-Line Summary)

M14 migrated the mini-SDV platform from Docker Compose to Kubernetes using k3s — a lightweight, production-compatible K8s distribution. All 11 services now run as K8s Deployments in the `sdv` namespace, with TLS certificates managed as Secrets, environment variables as ConfigMaps, and InfluxDB data persisted via hostPath. The migration preserves `hostNetwork: true` semantics from Docker Compose, making services accessible at the same `localhost:<port>` addresses.

---

## 2. Why Kubernetes?

```
Docker Compose (M1–M13):
  docker compose up -d        ← manual start
  docker compose down         ← manual stop
  No health-based restarts    ← if ai-monitor crashes, stays down
  No rolling updates          ← config changes require full restart

Kubernetes (M14+):
  kubectl apply -f k8s/       ← declarative desired state
  Automatic restart on crash  ← kubelet monitors container health
  Rolling updates             ← kubectl rollout restart (zero downtime)
  Secrets management          ← TLS certs / API keys decoupled from code
```

**Production context**: Cloud SDV backends (Bosch SDV, Cariad, Motional) deploy microservices on Kubernetes. K8s is the de facto standard for cloud-native vehicle service orchestration.

---

## 3. k3s: Lightweight Kubernetes for This Environment

### 3.1 Why k3s (not kind / minikube / kubeadm)

| Distribution | Why rejected |
|-------------|--------------|
| **kind** | Requires Docker bridge networking (`bridge=none` on this PC → fails) |
| **k3d** | Same — k3s inside Docker containers, needs bridge |
| **minikube** | Docker driver needs bridge; VM driver adds complexity |
| **kubeadm** | Requires full infrastructure setup |
| **k3s** ✅ | Native Linux process, own containerd, no Docker dependency |

### 3.2 Install Flags Explained

```bash
curl -sfL https://get.k3s.io | INSTALL_K3S_EXEC="\
  --disable=traefik \        # No ingress controller needed (hostNetwork)
  --disable=servicelb \      # No LoadBalancer needed (hostNetwork)
  --disable-kube-proxy \     # No iptables needed (all pods use hostNetwork)
  --disable-network-policy"  # No network policy controller needed
```

**Key lesson: `--flannel-backend=none` breaks the cluster.**
Setting `--flannel-backend=none` disables flannel entirely. Without any CNI plugin, kubelet reports `NetworkPluginNotReady` and the node never becomes `Ready`. The fix: use the default flannel backend (VXLAN). VXLAN does not require `ip_tables.ko` — it uses kernel VXLAN encapsulation which works on this custom kernel.

### 3.3 `--disable-kube-proxy` Trade-off

| With kube-proxy | Without kube-proxy |
|----------------|-------------------|
| ClusterIP Services route to pods | ClusterIP is unreachable |
| inter-pod communication via DNS | N/A — all pods on host network |
| local-path provisioner works | local-path provisioner fails (needs ClusterIP API) |
| CoreDNS reachable (ClusterIP) | CoreDNS unreachable → external DNS fails |

**Impact in M14**: All pods use `hostNetwork: true`, so Services are never needed for inter-pod communication. However, disabling kube-proxy has two side effects:

1. **PVC provisioner fails**: local-path provisioner tries to reach the K8s API server at its ClusterIP (`10.43.0.1:443`). Fix: use `hostPath` volumes instead of PVCs.

2. **External DNS fails**: CoreDNS runs as a K8s Service with ClusterIP `10.43.0.10`. With `dnsPolicy: ClusterFirstWithHostNet`, pods use `10.43.0.10` as nameserver — but ClusterIP is unreachable without kube-proxy. Result: `Temporary failure in name resolution` for any external hostname (api.anthropic.com, grafana.com, etc.). Fix: use `dnsPolicy: Default` so pods inherit the host's `/etc/resolv.conf` (WSL2's working DNS) instead.

---

## 4. Key Kubernetes Concepts Applied

### 4.1 Deployment vs Docker Compose Service

```yaml
# Docker Compose
services:
  ai-monitor:
    image: localhost:5000/sdv/ai-monitor:latest
    environment:
      OTEL_ENABLED: "true"
    restart: on-failure

# Kubernetes equivalent
apiVersion: apps/v1
kind: Deployment
metadata:
  name: ai-monitor
  namespace: sdv
spec:
  replicas: 1
  selector:
    matchLabels:
      app: ai-monitor
  template:
    spec:
      containers:
        - name: ai-monitor
          image: localhost:5000/sdv/ai-monitor:latest
          # Restart is automatic — kubelet monitors container health
```

The Deployment controller ensures `replicas: 1` pod is always running. If the container crashes, kubelet restarts it automatically — no `restart: on-failure` flag needed.

### 4.2 `command` vs `args` — Critical Distinction

```
Docker Compose `command:` → replaces Docker CMD (appended to ENTRYPOINT)
Kubernetes `command:`     → replaces ENTRYPOINT entirely
Kubernetes `args:`        → replaces CMD (appended to ENTRYPOINT)
```

**M14 bug**: `command: ["--vss", "/vss/vss_mini_covesa.json", "--insecure"]` caused `exec: "--vss": executable file not found` because K8s tried to execute `--vss` as the binary. Fix: change to `args:`.

```yaml
# Wrong (K8s tries to execute "--vss" as a binary)
command: ["--vss", "/vss/vss_mini_covesa.json", "--insecure"]

# Correct (appended to the image's ENTRYPOINT /app/databroker)
args: ["--vss", "/vss/vss_mini_covesa.json", "--insecure"]
```

### 4.3 hostNetwork: true and dnsPolicy

```yaml
spec:
  hostNetwork: true      # Pod shares WSL2 network namespace
  dnsPolicy: Default     # Use host /etc/resolv.conf directly
```

With `hostNetwork: true`, the pod's network is identical to the host's. Services bind to `localhost:<port>` and are accessible from Windows via WSL2 port forwarding — exactly the same as Docker Compose `network_mode: host`.

**dnsPolicy options with hostNetwork:**

| dnsPolicy | Nameserver used | Works in M14? |
|-----------|----------------|---------------|
| `ClusterFirstWithHostNet` | CoreDNS ClusterIP (`10.43.0.10`) | ❌ ClusterIP unreachable (kube-proxy disabled) |
| `Default` | Host `/etc/resolv.conf` (WSL2 DNS) | ✅ External DNS works |
| `None` | Explicit `dnsConfig.nameservers` | ✅ (manual config needed) |

**Standard advice** says `ClusterFirstWithHostNet` is required with `hostNetwork: true` to preserve cluster DNS. But that advice assumes kube-proxy is running. In this environment (kube-proxy disabled, all pods on hostNetwork, no Service-based inter-pod communication), `Default` is the correct choice.

### 4.4 ConfigMap vs Secret

```yaml
# ConfigMap — non-sensitive environment variables
apiVersion: v1
kind: ConfigMap
metadata:
  name: mqtt-bridge-config
  namespace: sdv
data:
  MQTT_HOST: "localhost"
  OTEL_ENABLED: "true"

# Secret — sensitive values (base64 encoded internally)
kubectl create secret generic tls-certs --from-file=config/certs/
kubectl create secret generic ai-secrets --from-literal=ANTHROPIC_API_KEY=$KEY
```

```yaml
# Consuming in a Pod
containers:
  - envFrom:
      - configMapRef:
          name: mqtt-bridge-config   # All keys become env vars
    env:
      - name: ANTHROPIC_API_KEY
        valueFrom:
          secretKeyRef:
            name: ai-secrets
            key: ANTHROPIC_API_KEY   # Single key from Secret
    volumeMounts:
      - name: tls-certs
        mountPath: /certs            # All cert files mounted as directory
volumes:
  - name: tls-certs
    secret:
      secretName: tls-certs
```

### 4.5 hostPath Volume Patterns

```yaml
# Mount a directory
- name: vss-config
  hostPath:
    path: /var/lib/sdv/config/vss
    type: Directory       # Must exist before pod starts

# Mount a single file
- name: tempo-config
  hostPath:
    path: /var/lib/sdv/config/tempo/tempo.yaml
    type: File            # Must exist before pod starts

# Create directory if not present
- name: shared
  hostPath:
    path: /tmp/sdv-ota
    type: DirectoryOrCreate  # Creates if missing
```

**M14 lesson**: `subPath` cannot be used with hostPath `type: File`. subPath is for slicing a ConfigMap/Secret volume by key name. For hostPath File, mount directly without subPath.

---

## 5. Local Image Registry for k3s

k3s uses its own containerd runtime, separate from the Docker daemon. Images built with `docker build` are not automatically available to k3s.

### Solution: Local Docker Registry

```
docker build → localhost:5000 (registry:2 container) → k3s pull
```

```bash
# Start registry (host network)
docker run -d --network host --name sdv-registry registry:2

# Build and push
docker build -t localhost:5000/sdv/mqtt-bridge:latest services/mqtt-bridge
docker push localhost:5000/sdv/mqtt-bridge:latest
```

k3s registry config (`/etc/rancher/k3s/registries.yaml`):
```yaml
mirrors:
  "localhost:5000":
    endpoint:
      - "http://localhost:5000"   # Allow insecure (HTTP) for local dev
```

Public images (influxdb, grafana, tempo, mosquitto, kuksa-databroker) are pulled directly from their public registries — no local registry needed.

---

## 6. Rolling Updates — Zero Downtime Restarts

```bash
# Trigger rolling update (new pod starts before old pod stops)
kubectl rollout restart deployment/ai-monitor -n sdv

# Monitor progress
kubectl rollout status deployment/ai-monitor -n sdv
# → Waiting for deployment "ai-monitor" rollout to finish: 1 old replicas are pending termination...
# → deployment "ai-monitor" successfully rolled out
```

Kubernetes replaces the old pod with a new one following the `RollingUpdate` strategy (default). The old pod stays alive until the new pod is healthy, ensuring zero downtime.

In Docker Compose, the equivalent is:
```bash
docker compose up -d --force-recreate ai-monitor  # Full restart, brief downtime
```

---

## 7. Observed Errors and Fixes

| Error | Root Cause | Fix |
|-------|-----------|-----|
| Node never `Ready` | `--flannel-backend=none` removes CNI; kubelet requires CNI | Remove flag, use default VXLAN flannel |
| `exec: "--vss": not found` | K8s `command:` replaces ENTRYPOINT (not CMD) | Change to `args:` |
| `CreateContainerConfigError` (grafana/tempo) | `subPath` cannot be used with hostPath File type | Remove `subPath:` from volumeMount |
| Port conflict (ota-server/webhook-receiver) | Docker Compose and K8s both use `hostNetwork`, same ports | Run `docker compose down` before deploying to K8s |
| PVC `Pending` forever | `--disable-kube-proxy` makes ClusterIP unreachable; local-path provisioner can't reach API server | Replace PVC with `hostPath: DirectoryOrCreate` |
| `APIConnectionError: Connection error` (ai-monitor → Anthropic API) | `dnsPolicy: ClusterFirstWithHostNet` points to CoreDNS ClusterIP `10.43.0.10`; unreachable without kube-proxy → all external DNS fails | Change all deployments to `dnsPolicy: Default` |
| Dashboard `localhost:8501` not accessible | Streamlit dashboard deployment missing from M14 k3s migration | Create `k8s/deployments/dashboard.yaml`; add `dashboard` to `build-push.sh` |
| MQTT reconnects every second; AI alerts never appear in dashboard | Streamlit runs scripts via `exec()` per rerun — module-level globals reset each time; paho MQTT client created anew every second, old connection dropped | Replace module global with `st.cache_resource`; store messages in cached dict, copy to `session_state` in main thread |
| `docker build` fails: `error getting credentials` | `~/.docker/config.json` has `"credsStore": "desktop.exe"` from Docker Desktop; not available with Docker Engine | Set `~/.docker/config.json` to `{}` |

---

## 8. Streamlit + Background Threads — st.cache_resource Pattern

### 8.1 How Streamlit Runs Scripts

```
Browser connects
    ↓
Streamlit creates session
    ↓
exec(script_source, namespace)   ← fresh namespace each rerun
    ↓
time.sleep(1); st.rerun()
    ↓
exec(script_source, namespace)   ← NEW fresh namespace again
```

Streamlit re-executes the script file on every rerun using `exec()`. This means **module-level variables are re-initialized on every rerun** — they do not persist the way they would in a normally imported Python module.

### 8.2 The Wrong Pattern (reconnects every second)

```python
# ❌ Module-level global — reset by exec() each rerun
_mqtt_client = None

def init_mqtt():
    global _mqtt_client
    if _mqtt_client is not None:  # always None after exec()
        return
    _mqtt_client = paho.Client()
    _mqtt_client.connect(...)    # new connection every second!
```

### 8.3 The Correct Pattern (st.cache_resource)

```python
# ✅ st.cache_resource — persists across all reruns and sessions
@st.cache_resource
def _get_mqtt_state() -> dict:
    return {"alert": None, "ota": None, "lock": threading.Lock()}

@st.cache_resource
def _get_mqtt_client():
    state = _get_mqtt_state()
    client = paho.Client(client_id="sdv-dashboard")

    def on_message(_c, _u, msg):
        data = json.loads(msg.payload.decode())
        with state["lock"]:              # thread-safe write
            state["alert"] = data        # store in cached dict

    client.on_message = on_message
    client.connect(MQTT_HOST, MQTT_PORT)
    client.loop_start()
    return client                        # cached — called only once

def sync_mqtt_to_session():
    _get_mqtt_client()                   # no-op after first call
    state = _get_mqtt_state()
    with state["lock"]:                  # thread-safe read in main thread
        if state["alert"]:
            st.session_state.ai_alert = state["alert"]
```

**Key rules:**
- `st.cache_resource` → for connections, models, shared resources (persists per process)
- `st.cache_data` → for pure functions returning serialisable data (per arguments)
- Never write to `st.session_state` from a background thread — copy in the main thread

### 8.4 Production Equivalent

In a production SDV cloud backend, this pattern maps to:

| Streamlit Pattern | Production |
|---|---|
| `st.cache_resource` for MQTT client | Singleton connection pool (e.g., AWS IoT Core SDK) |
| Background thread + shared dict | Message queue (Kafka consumer → Redis) |
| Copy to session_state per rerun | WebSocket push to browser (Server-Sent Events) |

---

## 10. Production Comparison

| Aspect | M14 (Development) | Production |
|--------|------------------|------------|
| K8s distribution | k3s (single node) | EKS / GKE / AKS (managed, multi-node) |
| Image registry | localhost:5000 (insecure) | ECR / GCR / ACR (with auth) |
| Storage | hostPath (node-local) | PVC + CSI driver (EBS, GCS, Azure Disk) |
| Networking | hostNetwork: true | ClusterIP + Ingress + mTLS (Istio) |
| Secrets | kubectl create secret | AWS Secrets Manager / Vault |
| kube-proxy | Disabled (no iptables) | Enabled (or eBPF via Cilium) |
| Namespaces | single: sdv | per-environment: sdv-dev, sdv-staging, sdv-prod |

### What Production Looks Like

```
Vehicle fleet (1000 vehicles)
    ↓ MQTT over TLS
Cloud K8s Cluster (EKS)
├── namespace: sdv-prod
│   ├── Deployment: mqtt-gateway      (3 replicas, HPA)
│   ├── Deployment: ai-monitor        (2 replicas)
│   ├── StatefulSet: influxdb         (PVC on EBS)
│   └── Deployment: ota-manager       (per-vehicle DaemonSet)
├── namespace: sdv-observability
│   ├── Deployment: grafana
│   └── StatefulSet: tempo
└── namespace: sdv-security
    └── cert-manager (automatic cert rotation)
```

---

## 11. Next Milestone Candidates

| Candidate | Description | Technology |
|-----------|-------------|------------|
| A | SecOC simulation — HMAC-signed CAN frames | HMAC-SHA256, SocketCAN |
| B | OTA security hardening — UPTANE split repository | UPTANE spec |
| C | Log aggregation — Grafana Loki for Docker/K8s logs | Loki, Promtail |
| D | cert-manager — automatic TLS certificate rotation in K8s | cert-manager, ACME |
| E | Horizontal Pod Autoscaler — scale ai-monitor on CPU/latency | HPA, metrics-server |
