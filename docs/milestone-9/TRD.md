# M9 TRD — Grafana Alerting + InfluxDB Tasks

## L5 Implementation Hypothesis

---

## 1. 新規ファイル構成

```
services/
  webhook-receiver/
    main.py          ← 新規: Grafana Webhook 受信 + ログ出力
    Dockerfile

config/
  grafana/
    provisioning/
      alerting/
        contact_points.yaml        ← 新規: Webhook 宛先定義
        notification_policies.yaml ← 新規: デフォルトルーティング
        alert_rules.yaml           ← 新規: Speed / SoC アラートルール

scripts/
  setup-influxdb-tasks.sh          ← 新規: sdv_1m バケット + Task 作成
```

**docker-compose.yml 追加:**
- `webhook-receiver` サービス（ポート 9000）

---

## 2. webhook-receiver 設計

Python 標準ライブラリの `http.server` のみで実装。依存ライブラリなし。

```python
class WebhookHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode()
        data = json.loads(body)
        # アラート名・状態・件数をログ出力
        log.info(f"[ALERT RECEIVED] {data.get('title','?')} "
                 f"state={data.get('state','?')} "
                 f"alerts={len(data.get('alerts', []))}")
        self.send_response(200)
        self.end_headers()

HTTPServer(("0.0.0.0", 9000), WebhookHandler).serve_forever()
```

---

## 3. Grafana Alerting プロビジョニング

### contact_points.yaml
```yaml
apiVersion: 1
contactPoints:
  - orgId: 1
    name: Webhook
    receivers:
      - uid: webhook-sdv
        type: webhook
        settings:
          url: http://localhost:9000/webhook
          httpMethod: POST
```

### notification_policies.yaml
```yaml
apiVersion: 1
policies:
  - orgId: 1
    receiver: Webhook
    group_by: ['grafana_folder', 'alertname']
    group_wait: 10s
    group_interval: 10s
    repeat_interval: 1h
```

### alert_rules.yaml — 2ルール

| ルール | クエリ | 閾値 | 評価間隔 |
|--------|--------|------|---------|
| High Speed Alert | signal=Speed の last() | > 130 | 10s |
| Low Battery Alert | signal=BatterySoC の last() | < 20 | 10s |

**発火タイミングの目安:**
- Speed > 130: vehicle-001 が OTA v1.1.0 適用後（最大速度 150 km/h）に
  ランダムウォークが 130 を超えたとき（数分以内に自然発生）
- SoC < 20: fleet-simulator の vehicle-002/003 が放電して 20% を割ったとき
  （起動後 30〜60 分、充電シミュレーションで一時的に 20% 以下になる瞬間）

---

## 4. InfluxDB Tasks セットアップスクリプト

`scripts/setup-influxdb-tasks.sh` が行うこと:
1. InfluxDB API で org ID を取得
2. `sdv_1m` バケットを作成（保持期間 30 日）
3. Downsample Task を作成（every 1m、mean 集計）

**Task の Flux スクリプト:**
```flux
option task = {name: "Downsample vehicle_signals 1m", every: 1m}

from(bucket: "sdv")
  |> range(start: -task.every)
  |> filter(fn: (r) => r["_measurement"] == "vehicle_signals")
  |> filter(fn: (r) => r["_field"] == "value")
  |> aggregateWindow(every: task.every, fn: mean, createEmpty: false)
  |> to(bucket: "sdv_1m", org: "sdv-org")
```

---

## 5. docker-compose.yml 追加ブロック

```yaml
webhook-receiver:
  build:
    context: ./services/webhook-receiver
  network_mode: host
  restart: on-failure
```

---

## 6. 実装ステップ（順序）

1. `services/webhook-receiver/` 作成
2. `config/grafana/provisioning/alerting/` 3ファイル作成
3. `scripts/setup-influxdb-tasks.sh` 作成（chmod +x）
4. `docker-compose.yml` に webhook-receiver 追加
5. WSL2: `docker compose build webhook-receiver && docker compose up -d webhook-receiver`
6. WSL2: `docker compose restart grafana`（アラートプロビジョニング再読み込み）
7. WSL2: `bash scripts/setup-influxdb-tasks.sh`（バケット + Task 作成）
8. 確認: Grafana Alerting → Alert rules で 2 ルールが表示されるか
9. 確認: `docker compose logs -f webhook-receiver` で通知を待つ
10. 確認: InfluxDB UI → Tasks で実行履歴を確認
