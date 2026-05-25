# M8 TRD — マルチビークル・フリート管理

## L5 Implementation Hypothesis

---

## 1. 新規ファイル構成

```
services/
  fleet-simulator/
    main.py          ← 新規: vehicle-002/003 並行テレメトリシミュレーター
    requirements.txt
    Dockerfile

config/
  grafana/
    provisioning/
      dashboards/
        fleet_overview.json   ← 新規: 全3台オーバーレイダッシュボード

scripts/
  fleet-ota-trigger.sh        ← 新規: 全車両 OTA 一括トリガー
```

**docker-compose.yml 追加サービス:**
- `fleet-simulator`   — vehicle-002/003 のテレメトリ生成
- `ota-manager-v002`  — ota-manager イメージ再利用、VEHICLE_ID=vehicle-002
- `ota-manager-v003`  — ota-manager イメージ再利用、VEHICLE_ID=vehicle-003

**既存ファイルの変更:**
- `services/ota-manager/main.py` — `STATE_FILE` を環境変数で上書き可能にする（1行変更）

---

## 2. fleet-simulator 設計

### パターン
1 プロセス内で `threading.Thread` を使い vehicle-002/003 を並行実行。
各スレッドは独立した `VehicleState` を持ち、別々のランダム値を生成。

### 書き込み先（DR-81）
- MQTT: `sdv/{vehicle_id}/telemetry`（JSON ペイロード）
- InfluxDB: measurement=`vehicle_signals`、tag=`vehicle_id`

### 依存ライブラリ
```
paho-mqtt==1.6.1
influxdb-client==1.43.0
```

### 疑似コード
```python
VEHICLES = ["vehicle-002", "vehicle-003"]

def simulate_vehicle(vehicle_id, mqtt, influx_write_api):
    state = VehicleState(vehicle_id)
    while True:
        state.step()
        # MQTT publish
        mqtt.publish(f"sdv/{vehicle_id}/telemetry", json.dumps(state.to_dict()))
        # InfluxDB write (3 signals)
        for signal, value in state.signals().items():
            point = Point("vehicle_signals").tag("vehicle_id", vehicle_id)
                         .tag("signal", signal).field("value", value)
            influx_write_api.write(bucket="sdv", record=point)
        time.sleep(INTERVAL)

# 各車両をスレッドで起動
for vid in VEHICLES:
    t = threading.Thread(target=simulate_vehicle, args=(vid, mqtt, write_api))
    t.daemon = True
    t.start()
```

---

## 3. ota-manager の変更（最小限）

`STATE_FILE` を環境変数で上書きできるように 1 行変更:

```python
# 変更前
STATE_FILE = "/tmp/ota_state.json"

# 変更後
STATE_FILE = os.environ.get("OTA_STATE_FILE", "/tmp/ota_state.json")
```

これにより vehicle-002/003 の ota-manager が独立した状態ファイルを持てる。

---

## 4. docker-compose.yml 追加ブロック

```yaml
fleet-simulator:
  build:
    context: ./services/fleet-simulator
  environment:
    MQTT_HOST: localhost
    MQTT_PORT: "1883"
    INFLUXDB_URL: http://localhost:8086
    INFLUXDB_TOKEN: sdv-token-local
    INFLUXDB_ORG: sdv-org
    INFLUXDB_BUCKET: sdv
    INTERVAL_SEC: "2"
  depends_on:
    - mosquitto
    - influxdb
  network_mode: host
  restart: on-failure

ota-manager-v002:
  build:
    context: ./services/ota-manager
  environment:
    OTA_SERVER_URL: http://localhost:8080
    MQTT_HOST: localhost
    MQTT_PORT: "1883"
    VEHICLE_ID: "vehicle-002"
    POLL_INTERVAL_SEC: "30"
    ECU_CONFIG_PATH: /tmp/ecu_config_v002.json
    OTA_STATE_FILE: /tmp/ota_state_v002.json
  depends_on:
    - ota-server
    - mosquitto
  network_mode: host
  restart: on-failure

ota-manager-v003:
  build:
    context: ./services/ota-manager
  environment:
    OTA_SERVER_URL: http://localhost:8080
    MQTT_HOST: localhost
    MQTT_PORT: "1883"
    VEHICLE_ID: "vehicle-003"
    POLL_INTERVAL_SEC: "30"
    ECU_CONFIG_PATH: /tmp/ecu_config_v003.json
    OTA_STATE_FILE: /tmp/ota_state_v003.json
  depends_on:
    - ota-server
    - mosquitto
  network_mode: host
  restart: on-failure
```

---

## 5. Grafana Fleet ダッシュボード（Flux クエリ）

全車両を 1 パネルに重ねるための Flux:

```flux
from(bucket: "sdv")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r["_measurement"] == "vehicle_signals")
  |> filter(fn: (r) => r["signal"] == "Speed")
  |> filter(fn: (r) => r["_field"] == "value")
  |> aggregateWindow(every: v.windowPeriod, fn: mean, createEmpty: false)
  |> yield(name: "mean")
```

`vehicle_id` タグが異なる系列として自動的に分離され、
Grafana が凡例に `vehicle-001`, `vehicle-002`, `vehicle-003` を表示する。

---

## 6. fleet-ota-trigger.sh

```bash
#!/bin/bash
# 使用方法: ./scripts/fleet-ota-trigger.sh [version]
VERSION=${1:-1.1.0}
curl -s -X POST "http://localhost:8080/release/${VERSION}"
echo "Fleet OTA triggered: all vehicles → ${VERSION}"
echo "Monitor: mosquitto_sub -h localhost -p 1883 -t 'sdv/+/ota/status' -v"
```

OTA サーバーのマニフェストは全車両共有のため、
`/release/{version}` を 1 回呼ぶだけで全ota-managerがpickupする。

---

## 7. 実装ステップ（順序）

1. `services/ota-manager/main.py` の STATE_FILE 1 行変更
2. `services/fleet-simulator/` 作成（main.py / requirements.txt / Dockerfile）
3. `config/grafana/provisioning/dashboards/fleet_overview.json` 作成
4. `scripts/fleet-ota-trigger.sh` 作成（chmod +x）
5. `docker-compose.yml` に fleet-simulator / ota-manager-v002/v003 追加
6. WSL2 で `docker compose build fleet-simulator && docker compose up -d fleet-simulator ota-manager-v002 ota-manager-v003`
7. Grafana → Fleet Overview で 3 台の折れ線確認
8. `./scripts/fleet-ota-trigger.sh 1.1.0` で一括 OTA 確認
