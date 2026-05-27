#!/bin/bash
# M14: Copy config files to /var/lib/sdv/ and create K8s Secrets.
# Run from the project root directory after setup-k3s.sh.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
CONFIG_DST="/var/lib/sdv/config"

echo "=== [1/4] Copying config files to ${CONFIG_DST} ==="
sudo mkdir -p "${CONFIG_DST}"
sudo cp -r "${PROJECT_DIR}/config/"* "${CONFIG_DST}/"
# Transfer ownership to current user so kubectl can read the files
sudo chown -R "$(id -u):$(id -g)" "${CONFIG_DST}"
chmod 600 "${CONFIG_DST}/certs/"*.key 2>/dev/null || true
chmod 644 "${CONFIG_DST}/certs/"*.crt 2>/dev/null || true

echo "=== [2/4] Creating OTA shared directory ==="
sudo mkdir -p /tmp/sdv-ota

echo "=== [3/4] Applying namespace ==="
kubectl apply -f "${PROJECT_DIR}/k8s/namespace.yaml"

echo "=== [4/4] Creating Secrets ==="

# TLS certificates (all files in config/certs/)
kubectl create secret generic tls-certs \
  --from-file="${CONFIG_DST}/certs/" \
  -n sdv --dry-run=client -o yaml | kubectl apply -f -
echo "  Secret 'tls-certs' applied."

# Anthropic API key
if [ -z "${ANTHROPIC_API_KEY:-}" ]; then
  echo "  WARNING: ANTHROPIC_API_KEY is not set in environment."
  echo "  Set it before running: export ANTHROPIC_API_KEY=sk-ant-..."
  ANTHROPIC_API_KEY=""
fi
kubectl create secret generic ai-secrets \
  --from-literal=ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY}" \
  -n sdv --dry-run=client -o yaml | kubectl apply -f -
echo "  Secret 'ai-secrets' applied."

echo ""
echo "Init complete. Deploy services:"
echo "  kubectl apply -f ${PROJECT_DIR}/k8s/deployments/"
echo ""
echo "Verify:"
echo "  kubectl get pods -n sdv"
