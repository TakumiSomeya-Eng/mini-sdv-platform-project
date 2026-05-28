#!/bin/bash
# M14: Build custom service images and push to local registry (localhost:5000)
# Run from the project root directory.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"

echo "=== [1/2] Starting local registry ==="
docker run -d --network host --name sdv-registry registry:2 2>/dev/null \
  && echo "Registry started." \
  || echo "Registry already running."

echo "=== [2/2] Building and pushing images ==="
SERVICES=(mqtt-bridge ai-monitor ota-manager ota-server influxdb-writer webhook-receiver dashboard)

for SERVICE in "${SERVICES[@]}"; do
  echo "--- Building: $SERVICE ---"
  docker build -t "localhost:5000/sdv/${SERVICE}:latest" \
    "${PROJECT_DIR}/services/${SERVICE}"
  docker push "localhost:5000/sdv/${SERVICE}:latest"
  echo "    Pushed: localhost:5000/sdv/${SERVICE}:latest"
done

echo ""
echo "All images pushed to localhost:5000:"
docker images | grep "localhost:5000/sdv"
