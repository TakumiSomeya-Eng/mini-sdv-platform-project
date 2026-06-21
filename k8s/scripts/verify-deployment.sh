#!/bin/bash
# Verify that all expected mini-sdv-platform pods are Running in the sdv namespace.
# Run after: kubectl apply -f k8s/deployments/
set -euo pipefail

NAMESPACE="sdv"

# All workload Deployments expected in the platform (M1-M18).
# Pyroscope and infrastructure (mosquitto, influxdb, grafana, tempo, databroker)
# are also included — they run as pods in the same namespace.
EXPECTED_DEPLOYMENTS=(
  # Infrastructure / M1-M14
  mosquitto
  databroker
  influxdb
  grafana
  tempo
  mqtt-bridge
  ai-monitor
  ota-manager
  ota-server
  influxdb-writer
  webhook-receiver
  dashboard
  # M15: RL training pipeline
  highway-env-bridge
  training-dispatcher
  # M16: Alpamayo simulation
  alpa-sim
  # M17: Edge AI + scene search
  ai-monitor-edge
  scene-search
  # M18: Continuous profiling
  pyroscope
)

echo "============================================="
echo " mini-sdv-platform — Deployment Verification"
echo " Namespace: ${NAMESPACE}"
echo "============================================="
echo ""

PASS=0
FAIL=0
PENDING=0

for DEP in "${EXPECTED_DEPLOYMENTS[@]}"; do
  # Get desired vs ready replica counts
  READY=$(kubectl get deployment "${DEP}" -n "${NAMESPACE}" \
    -o jsonpath='{.status.readyReplicas}' 2>/dev/null || echo "")
  DESIRED=$(kubectl get deployment "${DEP}" -n "${NAMESPACE}" \
    -o jsonpath='{.spec.replicas}' 2>/dev/null || echo "")

  if [ -z "${DESIRED}" ]; then
    printf "  %-30s  NOT FOUND\n" "${DEP}"
    FAIL=$((FAIL + 1))
  elif [ "${READY:-0}" = "${DESIRED}" ] && [ "${DESIRED}" -gt 0 ]; then
    printf "  %-30s  READY (%s/%s)\n" "${DEP}" "${READY}" "${DESIRED}"
    PASS=$((PASS + 1))
  else
    printf "  %-30s  NOT READY (%s/%s)\n" "${DEP}" "${READY:-0}" "${DESIRED}"
    PENDING=$((PENDING + 1))
  fi
done

echo ""
echo "---------------------------------------------"
echo " Summary: ${PASS} ready  |  ${PENDING} not ready  |  ${FAIL} not found"
echo "---------------------------------------------"

if [ "${FAIL}" -gt 0 ] || [ "${PENDING}" -gt 0 ]; then
  echo ""
  echo "Current pod status in namespace '${NAMESPACE}':"
  kubectl get pods -n "${NAMESPACE}" --no-headers \
    | awk '{printf "  %-50s %-20s %s\n", $1, $3, $5}'
  echo ""
  echo "For failing pods, check logs with:"
  echo "  kubectl logs -n ${NAMESPACE} <pod-name> --previous"
  echo "  kubectl describe pod -n ${NAMESPACE} <pod-name>"
  echo ""
  echo "Common issues:"
  echo "  - Image not found: run k8s/scripts/build-push.sh first"
  echo "  - Missing certs:   run k8s/scripts/init-config.sh first"
  echo "  - ONNX model path (/opt/sdv/models/phi4-mini-onnx): ai-monitor-edge"
  echo "    falls back to rule-based mode — pod still runs."
  exit 1
fi

echo ""
echo "All deployments are healthy."
