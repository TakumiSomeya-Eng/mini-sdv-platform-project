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

**Impact in M14**: All pods use `hostNetwork: true`, so Services are never needed for inter-pod communication. However, disabling kube-proxy also breaks the local-path PVC provisioner, which tries to reach the Kubernetes API server via its ClusterIP (`10.43.0.1:443`). Fix: use `hostPath` volumes instead of PVCs.

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

### 4.3 hostNetwork: true

```yaml
spec:
  hostNetwork: true                    # Pod shares WSL2 network namespace
  dnsPolicy: ClusterFirstWithHostNet   # Required companion setting
```

With `hostNetwork: true`, the pod's network is identical to the host's. Services bind to `localhost:<port>` and are accessible from Windows via WSL2 port forwarding — exactly the same as Docker Compose `network_mode: host`.

`dnsPolicy: ClusterFirstWithHostNet` is mandatory when `hostNetwork: true`. Without it, DNS falls back to the host's `/etc/resolv.conf`, breaking K8s cluster DNS.

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

---

## 8. Production Comparison

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

## 9. Next Milestone Candidates

| Candidate | Description | Technology |
|-----------|-------------|------------|
| A | SecOC simulation — HMAC-signed CAN frames | HMAC-SHA256, SocketCAN |
| B | OTA security hardening — UPTANE split repository | UPTANE spec |
| C | Log aggregation — Grafana Loki for Docker/K8s logs | Loki, Promtail |
| D | cert-manager — automatic TLS certificate rotation in K8s | cert-manager, ACME |
| E | Horizontal Pod Autoscaler — scale ai-monitor on CPU/latency | HPA, metrics-server |
