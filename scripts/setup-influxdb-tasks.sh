#!/bin/bash
# setup-influxdb-tasks.sh — M9: InfluxDB sdv_1m バケット + Downsample Task 作成
#
# 使用方法 (WSL2 Ubuntu):
#   bash scripts/setup-influxdb-tasks.sh
#
# 前提条件:
#   - InfluxDB が http://localhost:8086 で起動済み
#   - curl, jq がインストール済み (sudo apt-get install -y jq)

set -e

INFLUX_URL="${INFLUXDB_URL:-http://localhost:8086}"
INFLUX_TOKEN="${INFLUXDB_TOKEN:-sdv-token-local}"
INFLUX_ORG="${INFLUXDB_ORG:-sdv-org}"
SRC_BUCKET="sdv"
DST_BUCKET="sdv_1m"
RETENTION_SEC=$((30 * 24 * 3600))  # 30 days

echo "=========================================="
echo "  InfluxDB Tasks Setup"
echo "  URL: ${INFLUX_URL}"
echo "  Org: ${INFLUX_ORG}"
echo "=========================================="

# ── 1. org ID を取得 ────────────────────────────────────────────────────────
echo ""
echo "[1/3] Fetching org ID..."
ORG_ID=$(curl -sf "${INFLUX_URL}/api/v2/orgs" \
  -H "Authorization: Token ${INFLUX_TOKEN}" \
  | jq -r ".orgs[] | select(.name == \"${INFLUX_ORG}\") | .id")

if [ -z "$ORG_ID" ]; then
  echo "ERROR: org '${INFLUX_ORG}' not found. Is InfluxDB running?"
  exit 1
fi
echo "      org_id=${ORG_ID}"

# ── 2. sdv_1m バケット作成 ────────────────────────────────────────────────────
echo ""
echo "[2/3] Creating bucket '${DST_BUCKET}' (retention: 30 days)..."

# 既存チェック
EXISTS=$(curl -sf "${INFLUX_URL}/api/v2/buckets?name=${DST_BUCKET}" \
  -H "Authorization: Token ${INFLUX_TOKEN}" \
  | jq -r '.buckets | length')

if [ "$EXISTS" -gt 0 ]; then
  echo "      Already exists — skipping."
else
  curl -sf -X POST "${INFLUX_URL}/api/v2/buckets" \
    -H "Authorization: Token ${INFLUX_TOKEN}" \
    -H "Content-Type: application/json" \
    -d "{
      \"name\": \"${DST_BUCKET}\",
      \"orgID\": \"${ORG_ID}\",
      \"retentionRules\": [{\"type\": \"expire\", \"everySeconds\": ${RETENTION_SEC}}]
    }" > /dev/null
  echo "      Created: ${DST_BUCKET}"
fi

# ── 3. Downsample Task 作成 ──────────────────────────────────────────────────
echo ""
echo "[3/3] Creating downsample Task (every 1m, mean)..."

TASK_NAME="Downsample vehicle_signals 1m"

# 既存チェック
TASK_EXISTS=$(curl -sf "${INFLUX_URL}/api/v2/tasks?org=${INFLUX_ORG}" \
  -H "Authorization: Token ${INFLUX_TOKEN}" \
  | jq -r --arg name "$TASK_NAME" '.tasks[] | select(.name == $name) | .id' | wc -l)

if [ "$TASK_EXISTS" -gt 0 ]; then
  echo "      Task '${TASK_NAME}' already exists — skipping."
else
  FLUX_SCRIPT="option task = {name: \"${TASK_NAME}\", every: 1m}

from(bucket: \"${SRC_BUCKET}\")
  |> range(start: -task.every)
  |> filter(fn: (r) => r[\"_measurement\"] == \"vehicle_signals\")
  |> filter(fn: (r) => r[\"_field\"] == \"value\")
  |> aggregateWindow(every: task.every, fn: mean, createEmpty: false)
  |> to(bucket: \"${DST_BUCKET}\", org: \"${INFLUX_ORG}\")"

  curl -sf -X POST "${INFLUX_URL}/api/v2/tasks" \
    -H "Authorization: Token ${INFLUX_TOKEN}" \
    -H "Content-Type: application/json" \
    -d "{
      \"name\": \"${TASK_NAME}\",
      \"orgID\": \"${ORG_ID}\",
      \"flux\": $(echo "$FLUX_SCRIPT" | jq -Rs .)
    }" > /dev/null
  echo "      Created: '${TASK_NAME}'"
fi

echo ""
echo "=========================================="
echo "  Done!"
echo ""
echo "  Verify:"
echo "    InfluxDB UI → http://localhost:8086 → Tasks"
echo "    Data Explorer → sdv_1m bucket (data appears after 1 min)"
echo "=========================================="
