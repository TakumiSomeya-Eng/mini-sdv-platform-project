# M7 TRD — 時系列 DB + Grafana 可視化

## L5 Implementation Hypothesis

---

## 1. 新規サービス構成

```
services/
  influxdb-writer/
    main.py          ← 新規: Kuksa → InfluxDB 書き込みエージェント
    requirements.txt
    Dockerfile

config/
  grafana/
    grafana.ini                                   ← 新規: Anonymous Access 設定
    provisioning/
      datasources/influxdb.yaml                   ← 新規: InfluxDB データソース定義
      dashboards/dashboards.yaml                  ← 新規: ダッシュボードプロビジョニング設定
      dashboards/vehicle_signals.json             ← 新規: ダッシュボード定義 (JSON)
```

**docker-compose.yml 追加サービス:**
- `influxdb`         — InfluxDB 2.7 (時系列DB)
- `influxdb-writer`  — 新規 Python サービス
- `grafana`          — Grafana 10.4.x

---

## 2. influxdb-writer 設計

### パターン
`mqtt-bridge` と同じ Kuksa gRPC Subscribe パターン。MQTT publish の代わりに InfluxDB write。

### データスキーマ
```
measurement : vehicle_signals
tags        : vehicle_id="vehicle-001", signal="Speed"|"BatterySoC"|"CabinTemp"
field       : value (float64)
timestamp   : InfluxDB が自動付与 (server-side)
```

### Flux クエリ例（Grafana パネル）
```flux
from(bucket: "sdv")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r["_measurement"] == "vehicle_signals")
  |> filter(fn: (r) => r["signal"] == "Speed")
  |> filter(fn: (r) => r["_field"] == "value")
```

### 依存ライブラリ
```
influxdb-client==1.43.0
kuksa-client==0.4.3
```

---

## 3. InfluxDB 初期化（環境変数による自動セットアップ）

| 環境変数 | 値 |
|----------|-----|
| DOCKER_INFLUXDB_INIT_MODE | setup |
| DOCKER_INFLUXDB_INIT_USERNAME | admin |
| DOCKER_INFLUXDB_INIT_PASSWORD | sdv-password |
| DOCKER_INFLUXDB_INIT_ORG | sdv-org |
| DOCKER_INFLUXDB_INIT_BUCKET | sdv |
| DOCKER_INFLUXDB_INIT_ADMIN_TOKEN | sdv-token-local |

ポート: 8086（network_mode: host）

---

## 4. Grafana 設定

### grafana.ini（Anonymous Access）
```ini
[auth.anonymous]
enabled = true
org_role = Viewer
```

### datasources/influxdb.yaml
- type: influxdb
- query language: Flux
- url: http://localhost:8086
- token: sdv-token-local

### ダッシュボード
- 3パネル（Speed / BatterySoC / CabinTemp）
- 表示種別: Time Series（折れ線グラフ）
- デフォルト範囲: Last 30 minutes
- 自動更新: 5s
- ポート: 3000（network_mode: host）

---

## 5. docker-compose.yml 追加ブロック

```yaml
influxdb:
  image: influxdb:2.7
  environment:
    DOCKER_INFLUXDB_INIT_MODE: setup
    DOCKER_INFLUXDB_INIT_USERNAME: admin
    DOCKER_INFLUXDB_INIT_PASSWORD: sdv-password
    DOCKER_INFLUXDB_INIT_ORG: sdv-org
    DOCKER_INFLUXDB_INIT_BUCKET: sdv
    DOCKER_INFLUXDB_INIT_ADMIN_TOKEN: sdv-token-local
  network_mode: host
  restart: on-failure

influxdb-writer:
  build:
    context: ./services/influxdb-writer
  environment:
    DATABROKER_HOST: localhost
    INFLUXDB_URL: http://localhost:8086
    INFLUXDB_TOKEN: sdv-token-local
    INFLUXDB_ORG: sdv-org
    INFLUXDB_BUCKET: sdv
    VEHICLE_ID: vehicle-001
  depends_on:
    - databroker
    - influxdb
  network_mode: host
  restart: on-failure

grafana:
  image: grafana/grafana:10.4.3
  volumes:
    - ./config/grafana/grafana.ini:/etc/grafana/grafana.ini:ro
    - ./config/grafana/provisioning:/etc/grafana/provisioning:ro
  network_mode: host
  restart: on-failure
```

---

## 6. 実装ステップ（順序）

1. `config/grafana/` ディレクトリ構造と設定ファイル作成
2. `services/influxdb-writer/` 作成（main.py / requirements.txt / Dockerfile）
3. `docker-compose.yml` に3サービス追加
4. WSL2 で `docker compose up -d influxdb grafana` → InfluxDB 起動確認
5. `docker compose up -d influxdb-writer` → 書き込み確認
6. http://localhost:3000 でグラフ表示確認

---

## 7. 非機能要件

| 項目 | 値 |
|------|----|
| 書き込みレイテンシ | < 1秒（Kuksa イベント駆動） |
| データ保持期間 | 無制限（シミュレーション用途） |
| セキュリティ | トークン固定値（開発環境専用） |
| 再現性 | `docker compose down -v && up` で完全リセット可能 |
