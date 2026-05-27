#!/bin/bash
# M14: Install k3s on WSL2 (custom kernel — iptables/bridge unavailable)
# --disable-kube-proxy : no Services needed (all pods use hostNetwork: true)
# --flannel-backend=none: no CNI needed (host network, no pod-to-pod routing)
set -euo pipefail

echo "=== [1/4] Installing k3s ==="
curl -sfL https://get.k3s.io | INSTALL_K3S_EXEC="\
  --disable=traefik \
  --disable=servicelb \
  --disable-kube-proxy \
  --disable-network-policy" sh -

echo "=== [2/4] Setting up kubeconfig ==="
mkdir -p ~/.kube
sudo cp /etc/rancher/k3s/k3s.yaml ~/.kube/config
sudo chown "$(id -u):$(id -g)" ~/.kube/config
grep -q 'KUBECONFIG' ~/.bashrc || echo 'export KUBECONFIG=~/.kube/config' >> ~/.bashrc
export KUBECONFIG=~/.kube/config

echo "=== [3/4] Configuring local registry (localhost:5000) ==="
sudo mkdir -p /etc/rancher/k3s
cat <<'EOF' | sudo tee /etc/rancher/k3s/registries.yaml
mirrors:
  "localhost:5000":
    endpoint:
      - "http://localhost:5000"
EOF

echo "=== [4/4] Restarting k3s to apply registry config ==="
sudo systemctl restart k3s

echo "Waiting for k3s node to become Ready..."
until kubectl get nodes 2>/dev/null | grep -q " Ready"; do
  printf "."
  sleep 3
done
echo ""
kubectl get nodes

echo ""
echo "k3s is ready. Next steps:"
echo "  1. bash k8s/scripts/build-push.sh"
echo "  2. bash k8s/scripts/init-config.sh"
echo "  3. kubectl apply -f k8s/deployments/"
