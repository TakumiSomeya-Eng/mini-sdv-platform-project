#!/bin/bash
# fleet-ota-trigger.sh — M8: 全車両 OTA 一括トリガー
#
# 使用方法:
#   ./scripts/fleet-ota-trigger.sh [version]
#
# 例:
#   ./scripts/fleet-ota-trigger.sh 1.1.0
#
# 仕組み:
#   OTA サーバーのマニフェストは全車両共有 (latest_version を更新するだけ)。
#   各車両の ota-manager が次のポーリング時にマニフェストを取得し、
#   独立して CHECK→DOWNLOAD→VERIFY→APPLY→REPORT を実行する。

set -e

VERSION="${1:-1.1.0}"
OTA_SERVER="${OTA_SERVER_URL:-http://localhost:8080}"

echo "=========================================="
echo "  Fleet OTA Trigger"
echo "  Version : ${VERSION}"
echo "  Server  : ${OTA_SERVER}"
echo "=========================================="

# マニフェストの latest_version を更新
echo ""
echo "[1/2] Promoting ${VERSION} on OTA server..."
RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "${OTA_SERVER}/release/${VERSION}")
BODY=$(echo "$RESPONSE" | head -n1)
CODE=$(echo "$RESPONSE" | tail -n1)

if [ "$CODE" = "200" ]; then
    echo "      OK: ${BODY}"
else
    echo "      ERROR (HTTP ${CODE}): ${BODY}"
    exit 1
fi

# MQTT で全車両の OTA ステータスを監視（10秒間）
echo ""
echo "[2/2] Monitoring OTA status (10s)..."
echo "      Topic: sdv/+/ota/status"
echo ""

if command -v mosquitto_sub &> /dev/null; then
    timeout 10 mosquitto_sub -h localhost -p 1883 -t "sdv/+/ota/status" -v 2>/dev/null || true
else
    echo "      (mosquitto_sub not found — monitor manually)"
    echo "      mosquitto_sub -h localhost -p 1883 -t 'sdv/+/ota/status' -v"
fi

echo ""
echo "=========================================="
echo "  Done. Each ota-manager will apply on"
echo "  its next poll (POLL_INTERVAL_SEC=30)."
echo "=========================================="
