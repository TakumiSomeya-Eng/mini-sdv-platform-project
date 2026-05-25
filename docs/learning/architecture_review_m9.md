# Milestone 9 アーキテクチャレビュー & 学習ガイド
# Grafana Alerting + InfluxDB Tasks（自動ダウンサンプリング）

---

## 目次

1. [M9 が追加するもの — 監視・通知・データ管理層](#1)
2. [Grafana Unified Alerting のアーキテクチャ](#2)
3. [アラートのライフサイクル](#3)
4. [アラートルール プロビジョニング YAML の解剖](#4)
5. [DatasourceError バグとその修正](#5)
6. [Contact Point と Notification Policy](#6)
7. [webhook-receiver の設計](#7)
8. [InfluxDB Tasks — 自動ダウンサンプリング](#8)
9. [Flux Task スクリプトの構造](#9)
10. [エンドツーエンド フロー追跡](#10)
11. [本番 SDV での運用監視スタック比較](#11)
12. [既知の制約と本番との差分](#12)
13. [M1〜M9 アーキテクチャ完全俯瞰](#13)
14. [復習クイズ](#14)

---

<a name="1"></a>
## 1. M9 が追加するもの — 監視・通知・データ管理層

### M7/M8 の限界

M7/M8 までの Grafana は「見るだけ」のダッシュボードだった。
Speed が 150 km/h に跳ね上がっても、誰かが画面を見ていなければ気づけない。
また、2 秒ごとの生データを永続的に蓄積すると、ストレージが際限なく増加する。

### M9 が解決すること

```
M9 追加後:

Grafana Alerting エンジン（10 秒ごと評価）
  ├── Speed > 130 → Firing → Notification Policy → Webhook
  └── SoC < 20   → Firing → Notification Policy → Webhook
                                                        │
                                              webhook-receiver（ログ出力）

InfluxDB Task（1 分ごと自動実行）
  sdv バケット（生データ、2 秒間隔）
    → aggregateWindow(1m, mean)
    → sdv_1m バケット（圧縮データ、1 分間隔）
```

**M9 が実現すること:**
- 車両シグナル異常の自動検知と通知（監視自動化）
- 生データの自動圧縮によるストレージ効率化
- 「見るだけ Grafana」から「能動的な監視システム」への昇格

---

<a name="2"></a>
## 2. Grafana Unified Alerting のアーキテクチャ

### 2.1 コンポーネント構成

```
Grafana Unified Alerting
  │
  ├── Alert Rule Engine（評価ループ）
  │     └── 各ルールを interval ごとに評価
  │
  ├── Alertmanager（内蔵）
  │     ├── Routing（Notification Policy）
  │     ├── Grouping（group_by）
  │     ├── Silencing（アラートの一時停止）
  │     └── Inhibition（連鎖抑制）
  │
  └── Contact Points（通知先）
        ├── Webhook
        ├── Slack
        ├── PagerDuty
        └── Email etc.
```

Grafana 8.0 以降は「Grafana Managed Alerts」（Unified Alerting）を採用。
旧来の「Legacy Alerting」と異なり、Prometheus/Alertmanager と同じ概念体系を使う。

### 2.2 評価の仕組み

```
Alert Rule
  └── data: [クエリA, 式B]
       └── A: InfluxDB Flux クエリ（生の計測値）
       └── B: 閾値式（A の値と定数を比較）
  └── condition: B（Bが True なら Firing）
  └── interval: 10s（10 秒ごとに A→B を評価）
```

評価結果は `Normal` / `Pending` / `Alerting(Firing)` / `NoData` / `Error` のいずれか。

---

<a name="3"></a>
## 3. アラートのライフサイクル

```
状態遷移図:

  ┌──────────┐   条件成立      ┌──────────┐   for期間経過   ┌──────────┐
  │  Normal  │ ─────────────▶ │ Pending  │ ──────────────▶ │ Firing  │
  └──────────┘                └──────────┘                  └──────────┘
       ▲                           │                              │
       │        条件不成立          │          条件不成立           │
       └───────────────────────────┴──────────────────────────────┘
```

**for: 0s の意味（本プロジェクトの設定）:**
- `for: 0s` は Pending 期間ゼロ = 条件成立と同時に即 Firing
- 本番では `for: 5m` のように設定し、一時的なスパイクでの誤報を防ぐ

**各状態のアクション:**
| 状態 | Webhook 送信 | 色 |
|------|------------|-----|
| Normal | Resolved 通知（初回 Firing 後のみ） | 緑 |
| Pending | なし | 黄 |
| Firing | アラート通知 | 赤 |
| NoData | noDataState に従う | 紫 |
| Error | execErrState に従う | オレンジ |

---

<a name="4"></a>
## 4. アラートルール プロビジョニング YAML の解剖

### 4.1 全体構造

```yaml
apiVersion: 1
groups:
  - orgId: 1
    name: Vehicle Alerts      # グループ名
    folder: SDV Alerts        # Grafana フォルダ（存在しない場合は作成）
    interval: 10s             # 評価間隔
    rules:
      - uid: high-speed-alert # ルール固有 ID（変更不可）
        title: High Speed Alert
        condition: B          # 最終判定に使う refId
        data:
          - refId: A          # データクエリ
            ...
          - refId: B          # 閾値式
            ...
        noDataState: NoData   # データなし時の動作
        execErrState: Alerting # クエリエラー時の動作
        for: 0s               # Pending 期間
        annotations:
          summary: "..."      # 通知メッセージ
        labels:
          severity: warning   # ラベル（ルーティング・フィルタ用）
```

### 4.2 data 配列 — クエリと式の 2 段構成

```yaml
data:
  # ── A: データクエリ（InfluxDB Flux）──────────────────────────────────────
  - refId: A
    relativeTimeRange:
      from: 60    # 現在から 60 秒前まで
      to: 0       # 現在まで
    datasourceUid: influxdb-sdv   # M7 で設定したデータソース UID
    model:
      datasource:                  # ← M9 修正で追加（必須）
        type: influxdb
        uid: influxdb-sdv
      intervalMs: 1000             # ← M9 修正で追加（必須）
      maxDataPoints: 43200         # ← M9 修正で追加（必須）
      query: "from(bucket: \"sdv\")..."
      refId: A

  # ── B: 閾値式（組み込み Expression）────────────────────────────────────
  - refId: B
    datasourceUid: "-100"          # "-100" = Grafana 組み込み式エンジン
    model:
      type: threshold              # 閾値評価
      expression: A                # A の値を評価
      conditions:
        - evaluator:
            params: [130]
            type: gt               # greater than 130
          reducer:
            type: last             # A の最後の値を使う
```

### 4.3 datasourceUid: "-100" の意味

Grafana の組み込み式エンジン（`__expr__`）は特別な UID `-100` を持つ。
データソースへのネットワーク接続なしに、前のクエリ結果（refId: A）を
インメモリで処理して閾値比較・数学演算・集計を行う。

---

<a name="5"></a>
## 5. DatasourceError バグとその修正

### 5.1 最初のバグ

```
[ALERT] DatasourceError SDV Alerts
  alertname: DatasourceError
  values: None
```

`alertname: DatasourceError` は、Grafana がクエリ実行に失敗したときに
生成する**システムアラート**。実際の metric 値は取得できていない状態。

### 5.2 原因: model フィールドの不足

```yaml
# バグあり（不完全な model）
model:
  query: "from(bucket: \"sdv\")..."
  refId: A

# 修正後（Grafana 10.x が必要とするフィールドを追加）
model:
  datasource:          # ← 必須: datasource の type と uid
    type: influxdb
    uid: influxdb-sdv
  intervalMs: 1000     # ← 必須: クエリのサンプリング間隔（ms）
  maxDataPoints: 43200 # ← 必須: 最大データポイント数
  query: "from(bucket: \"sdv\")..."
  refId: A
```

Grafana 10.x の alerting エンジンは `datasource`、`intervalMs`、`maxDataPoints` を
model 内に要求する。これらが欠けるとプラグインに正しくリクエストが渡らず、
クエリが「実行前に失敗」して DatasourceError になる。

### 5.3 execErrState の変更

```yaml
# バグあり
execErrState: Error    # クエリエラー → "Error" 状態（DatasourceError アラートを生成）

# 修正後
execErrState: Alerting # クエリエラー → "Firing" 状態（ルール名のアラートを生成）
```

クエリエラーとメトリクス異常を区別したい場合は `Error` が適切だが、
開発環境では `Alerting` にして誤報のラベルを整理するほうが見やすい。

### 5.4 last() → max() / min() に変更した理由

```flux
# 変更前（複数シリーズを返す可能性）
|> last()

# 変更後（全シリーズを単一値に集約）
|> max()   # Speed: 3台のうち最高速度
|> min()   # SoC:   3台のうち最低残量
```

`last()` はシリーズごとに 1 行返すため、3 台分 = 3 行になる。
Grafana の threshold 式は複数行のうちどれを評価するか曖昧になる。
`max()` / `min()` で 1 行に集約することで、「フリート全体で最も危険な値」を確実に評価できる。

---

<a name="6"></a>
## 6. Contact Point と Notification Policy

### 6.1 Contact Point（通知先）

```yaml
# contact_points.yaml
contactPoints:
  - name: Webhook
    receivers:
      - uid: webhook-sdv
        type: webhook
        settings:
          url: http://localhost:9000/webhook
          httpMethod: POST
```

Grafana がサポートする Contact Point の種類:
- `webhook`: 任意の HTTP エンドポイント
- `slack`: Slack Incoming Webhook
- `pagerduty`: PagerDuty API
- `email`: SMTP メール送信
- `opsgenie`: OpsGenie API
- `teams`: Microsoft Teams

### 6.2 Notification Policy（ルーティング）

```yaml
# notification_policies.yaml
policies:
  - receiver: Webhook       # デフォルト送信先
    group_by:
      - grafana_folder
      - alertname
    group_wait: 10s          # グループ確定まで待機（複数アラートをまとめる）
    group_interval: 10s      # 同グループの再通知間隔
    repeat_interval: 1h      # 継続 Firing 時の再通知間隔
```

**group_by の意味:**
同じ `grafana_folder` + `alertname` を持つアラートを 1 つの通知にまとめる。
`group_wait: 10s` の間に届いた同グループのアラートを待ってから送信。

本プロジェクトの Webhook ログで `[FIRING:2]`（2件まとめて）が届いたのはこれが理由。

---

<a name="7"></a>
## 7. webhook-receiver の設計

### 7.1 Python 標準ライブラリのみで実装

```python
from http.server import BaseHTTPRequestHandler, HTTPServer

class WebhookHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8")
        data = json.loads(body)
        # アラート名・状態・件数をログ出力
        log.info(f"[ALERT] {data.get('title')}  state={data.get('state')}")
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

HTTPServer(("0.0.0.0", 9000), WebhookHandler).serve_forever()
```

`paho-mqtt` や `influxdb-client` など外部ライブラリが不要。
`requirements.txt` すら不要で Dockerfile は `COPY main.py .` だけ。

### 7.2 Grafana Webhook ペイロードの構造

```json
{
  "receiver": "Webhook",
  "status": "firing",
  "alerts": [
    {
      "status": "firing",
      "labels": {
        "alertname": "High Speed Alert",
        "grafana_folder": "SDV Alerts",
        "severity": "warning"
      },
      "annotations": {
        "summary": "Vehicle speed exceeded 130 km/h"
      },
      "fingerprint": "abc123...",
      "startsAt": "2026-05-25T20:11:50Z",
      "values": null
    }
  ],
  "groupLabels": {"alertname": "High Speed Alert"},
  "commonLabels": {"severity": "warning"},
  "title": "[FIRING:1] High Speed Alert SDV Alerts (warning)",
  "state": "alerting"
}
```

`values: null` は、threshold 式ベースのアラートでは Grafana が
実測値を Webhook ペイロードに含めない仕様によるもの（動作への影響なし）。

---

<a name="8"></a>
## 8. InfluxDB Tasks — 自動ダウンサンプリング

### 8.1 なぜダウンサンプリングが必要か

```
生データ（sdv バケット）:
  3 台 × 3 シグナル × 1 点/2 秒 = 4.5 点/秒 = 270 点/分

1 日のデータ量:  270 × 60 × 24 = 388,800 点/日
30 日のデータ量: 約 1,200 万点

sdv_1m バケット（1 分平均）:
  3 台 × 3 シグナル × 1 点/分 = 9 点/分

30 日のデータ量: 9 × 60 × 24 × 30 = 388,800 点（生データの 1/30）
```

長期トレンド分析に 2 秒単位の解像度は不要。
1 分平均で十分な精度を維持しながらストレージを 1/30 に圧縮できる。

### 8.2 InfluxDB Tasks の仕組み

```
InfluxDB Task エンジン
  └── スケジューラ（cron 相当）
        └── every: 1m → 1 分ごとに Flux スクリプトを実行
              └── 前の 1 分間のデータを集計 → sdv_1m に書き込み
```

Task は InfluxDB 内部で動作するため:
- 外部プロセス不要
- InfluxDB が再起動しても Task は保持される
- 実行履歴は InfluxDB UI → Tasks → Run History で確認可能

### 8.3 API を使った Task 作成

`scripts/setup-influxdb-tasks.sh` が行う手順:

```bash
# 1. org ID を取得（DOCKER_INFLUXDB_INIT_ORG の名前から ID を解決）
ORG_ID=$(curl -s http://localhost:8086/api/v2/orgs \
  -H "Authorization: Token sdv-token-local" \
  | jq -r '.orgs[] | select(.name == "sdv-org") | .id')

# 2. sdv_1m バケット作成（保持期間 30 日）
curl -X POST http://localhost:8086/api/v2/buckets \
  -H "Authorization: Token sdv-token-local" \
  -d '{"name": "sdv_1m", "orgID": "...", "retentionRules": [{"everySeconds": 2592000}]}'

# 3. Task 作成（Flux スクリプトを JSON 文字列として POST）
curl -X POST http://localhost:8086/api/v2/tasks \
  -H "Authorization: Token sdv-token-local" \
  -d '{"name": "...", "orgID": "...", "flux": "option task = ..."}'
```

`jq -Rs .` で複数行 Flux スクリプトを JSON 文字列にエスケープする技法が重要。

---

<a name="9"></a>
## 9. Flux Task スクリプトの構造

```flux
option task = {name: "Downsample vehicle_signals 1m", every: 1m}

from(bucket: "sdv")
  |> range(start: -task.every)          // 前の 1 分間のみを対象
  |> filter(fn: (r) =>
      r["_measurement"] == "vehicle_signals"
      and r["_field"] == "value"
  )
  |> aggregateWindow(                   // 1 分ウィンドウで集計
      every: task.every,
      fn: mean,
      createEmpty: false
  )
  |> to(bucket: "sdv_1m", org: "sdv-org")  // 集計結果を別バケットに書き込み
```

### option task の意味

```flux
option task = {
  name: "タスク名",
  every: 1m,      // 実行間隔（cron: "0 * * * *" 相当）
  offset: 0s,     // オフセット（任意）
}
```

`task.every` は `every` フィールドの値（1m）を参照する変数。
`range(start: -task.every)` = 「直前の 1 実行間隔分のデータ」を意味する。

### to() 関数

```flux
|> to(bucket: "sdv_1m", org: "sdv-org")
```

Flux の `to()` 関数は、パイプライン結果を別バケットに書き込む。
タグとフィールドはそのまま保持されるため、
`sdv_1m` のデータも `vehicle_id`、`signal`、`value` という
同じスキーマを持つ → Grafana クエリのバケット名を変えるだけで切り替えできる。

---

<a name="10"></a>
## 10. エンドツーエンド フロー追跡

vehicle-001 の Speed が 135 km/h を記録したときの完全なトレース:

```
① ECU Simulator（vehicle-001, OTA v1.1.0 適用後）
   Speed ランダムウォーク → 135.2 km/h

② CAN Gateway → Kuksa Databroker（SetCurrentValues）

③ influxdb-writer（Subscribe 受信）
   Point("vehicle_signals")
     .tag("vehicle_id", "vehicle-001")
     .tag("signal", "Speed")
     .field("value", 135.2)
   → sdv バケットに書き込み

④ Grafana Alerting エンジン（10 秒後の評価タイミング）
   Flux クエリ実行:
     from(bucket: "sdv")
       |> range(start: -1m)
       |> filter(signal == "Speed")
       |> max()     → 135.2（3台のうち最高値）

   閾値式 B:
     135.2 > 130 → True → Firing

⑤ Grafana 内蔵 Alertmanager
   group_wait: 10s 待機 → Notification Policy → Webhook Contact Point
   POST http://localhost:9000/webhook
   {"title": "[FIRING:1] High Speed Alert ...", "state": "alerting", ...}

⑥ webhook-receiver（コンテナログ）
   [ALERT] High Speed Alert SDV Alerts (warning)
     status=firing  severity=warning
     summary=Vehicle speed exceeded 130 km/h

⑦ InfluxDB Task（1 分ごとの独立したフロー）
   sdv バケット（直前 1 分間の全データ）
   → aggregateWindow(1m, mean)
   → sdv_1m バケットに書き込み
   （135.2 を含む 1 分平均が sdv_1m に記録）
```

---

<a name="11"></a>
## 11. 本番 SDV での運用監視スタック比較

| スタック | 構成 | 特徴 |
|---------|------|------|
| **本プロジェクト（M9）** | Grafana Alerting + Webhook | ローカル完結、学習用 |
| **Prometheus + Alertmanager** | Prometheus rules + Alertmanager | Kubernetes標準、Pull型 |
| **Grafana Cloud** | Grafana Alerting SaaS | フルマネージド、Oncall統合 |
| **AWS CloudWatch** | Metric Alarm + SNS | AWSネイティブ、Lambda連携 |
| **Azure Monitor** | Alert Rule + Action Group | Azure IoT Hub連携、OEM採用 |
| **PagerDuty** | 外部 On-call システム | エスカレーション、シフト管理 |
| **Datadog** | APM + Watchdog | AI異常検知、フルスタック |

**SDV 特有の監視要件:**
- 車両単位のアラート（vehicle_id タグでのフィルタ）
- 地理情報との連携（GPS + アラートのマッピング）
- 規制対応（SOTIF: 安全上重要な機能の継続的モニタリング）
- OTA との連携（アップデート後の異常増加を検知）

---

<a name="12"></a>
## 12. 既知の制約と本番との差分

| 制約 | 本プロジェクト（M9） | 本番 SDV |
|------|---------------------|---------|
| 通知先 | ローカル webhook-receiver | PagerDuty / Slack / SMS |
| アラートの誤報対策 | `for: 0s`（即 Firing） | `for: 5m`（5 分継続で Firing） |
| アラートの車両特定 | 全台合算（max/min） | vehicle_id ラベルで個別通知 |
| Webhook の認証 | なし | HMAC 署名検証 |
| ダウンサンプリング | 1 段階（1 分平均） | 多段階（1 分 → 1 時間 → 1 日） |
| Task の冪等性 | スクリプトで既存チェック | InfluxDB Task API の updateOrCreate |
| values=null | Webhook ペイロードに実測値なし | Grafana の外部アラートルール（Mimir）では含まれる |

---

<a name="13"></a>
## 13. M1〜M9 アーキテクチャ完全俯瞰

```
【車両内（WSL2 / Docker）】

ECU Simulator (M1/M4/M6)
  │ CAN フレーム (M4: vcan0)
  ▼
CAN Gateway (M4)
  │ gRPC SetCurrentValues
  ▼
Kuksa Databroker (M1)
  │ gRPC Subscribe（扇形配信）
  ├──────────────────────────────────────────────────────────────────┐
  │                                                                  │
  ▼                                                                  ▼
MQTT Bridge (M2)                                          InfluxDB Writer (M7)
  │ MQTT publish                                               │ write()
  ▼                                                            ▼
Mosquitto (M2)                                           InfluxDB (M7)
  │                                                            │
  ├── AI Monitor (M5) → Claude API → アラート                  ├── sdv バケット（生データ）
  │                                                            │     │
  └── Dashboard (M1/M5/M6) → Streamlit                        │     │ Task: every 1m (M9)
                                                               │     ▼
OTA Server (M6) ←── fleet-ota-trigger.sh (M8)                │   sdv_1m バケット（圧縮）
  │ マニフェスト共有                                             │
  ├── OTA Manager (M6, vehicle-001)                           Grafana (M7/M9)
  ├── OTA Manager v002 (M8)                                     ├── Vehicle Signals (M7)
  └── OTA Manager v003 (M8)                                     ├── Fleet Overview (M8)
                                                                 └── Alerting (M9)
Fleet Simulator (M8)                                                   │
  ├── vehicle-002 → MQTT + InfluxDB                                    │ Notification Policy
  └── vehicle-003 → MQTT + InfluxDB                                    ▼
                                                               webhook-receiver (M9)
ROS2 Bridge (M3) → DDS → ros2-subscriber                       └── ログ出力（通知受信確認）
```

**M9 が閉じたループ:**
- M7 でデータを蓄積 → M9 で異常を検知 → webhook で通知
- M7 の生データ → M9 Tasks で圧縮 → 長期トレンド分析に備える

---

<a name="14"></a>
## 14. 復習クイズ

**Q1.** `execErrState: Error` と `execErrState: Alerting` の違いを、M9 で発生したバグの文脈で説明せよ。

> A. `execErrState: Error` はクエリエラー時に Grafana が「Error」状態に設定し、`alertname: DatasourceError` というシステムアラートを生成する。`execErrState: Alerting` はクエリエラーでも「Firing」状態にしてルール名のアラートを通知する。M9 では model フィールド不足でクエリが失敗し、`Error` 設定のため `DatasourceError` アラートが届いていた。修正後は `Alerting` に変更し、正しいアラート名で通知が届くようになった。

---

**Q2.** Grafana alerting の `data` 配列で `datasourceUid: "-100"` を使うのはなぜか？

> A. `-100` は Grafana 組み込みの式エンジン（`__expr__`）の特別な UID。前のクエリ結果（refId: A）をインメモリで処理して閾値比較・演算を行う。外部データソース（InfluxDB）への追加クエリなしで評価できるため、レイテンシが低くシンプル。

---

**Q3.** `last()` から `max()` / `min()` に変更した技術的な理由を説明せよ。

> A. `last()` はシリーズごとに 1 行を返すため、3 台（vehicle-001/002/003）分で 3 行になる。Grafana の threshold 式はどの行を評価するか不明確になる。`max()` は全シリーズを 1 行に集約するため、「フリート全体で最も高い速度」を確実に評価できる。これにより「どれか 1 台でも閾値超過したら Firing」というフリート監視の意図を正確に実装できる。

---

**Q4.** `for: 0s` と `for: 5m` の使い分けを、車両速度アラートの文脈で説明せよ。

> A. `for: 0s` は条件成立と同時に即 Firing。一瞬の速度スパイクでも通知される。`for: 5m` は 5 分間継続して条件が成立し続けた場合のみ Firing。短時間の急加速（追い越しなど）では通知されず、長距離の高速走行や速度センサー異常を検知するのに適している。本番ではノイズを減らすために `for: 5m` 以上が推奨される。

---

**Q5.** InfluxDB Task で `range(start: -task.every)` とする意味を説明せよ。

> A. `task.every` は Task の実行間隔（1m）を参照する変数。`range(start: -task.every)` は「直前の 1 実行間隔分のデータ」を対象にする。これにより、Task が 1 分ごとに実行されるたびに「前の 1 分間のデータ」だけを処理し、重複や欠落なくダウンサンプリングできる。

---

**Q6.** InfluxDB の `to()` 関数を使うメリットは何か？

> A. `to()` はパイプライン結果を別バケットに書き込む Flux 関数。タグとフィールドの構造をそのまま保持するため、`sdv_1m` も `vehicle_id`、`signal`、`value` という同じスキーマになる。Grafana クエリのバケット名（`"sdv"` → `"sdv_1m"`）を変えるだけで長期データの表示に切り替えられる。スキーマの一貫性がダウンサンプリングの鍵。

---

**Q7.** `group_wait: 10s` の意味と、M9 で最初に `[FIRING:2]` が届いた理由を説明せよ。

> A. `group_wait` は同グループの複数アラートをまとめる待機時間。10 秒以内に届いた同グループ（`grafana_folder: SDV Alerts`）のアラートを 1 つの通知にまとめる。最初の通知で `[FIRING:2]` だったのは、High Speed と Low Battery の両方が group_wait の 10 秒以内に Firing になり、1 つの Webhook リクエストにまとめられたため。

---

**Q8.** 本番 SDV で「アラートの車両特定」を実現するにはどう設計するか？

> A. alert_rules.yaml の Flux クエリで `vehicle_id` フィルタを外し（全台評価）、かつ Grafana の「For each series」機能（または `group()` を使わない評価）で vehicle_id ごとに個別アラートを生成する。各アラートに `vehicle_id` ラベルを付与し、Notification Policy で vehicle_id ラベルに基づくルーティング（例: vehicle-001 → 担当 A チーム）を設定する。

---

**Q9.** `setup-influxdb-tasks.sh` で既存チェックを行っている理由は何か？

> A. スクリプトを複数回実行しても安全に動作する（冪等性）ようにするため。InfluxDB API は同名のバケットや Task の作成要求に対してエラーを返す（400 Conflict）。既存チェックなしでは 2 回目の実行がエラーで終わる。`Already exists — skipping` で正常終了させることで、`docker compose down -v && up` 後の再セットアップや CI での自動実行に対応できる。

---

**Q10.** M9 全体を通じて「監視自動化」の観点から最も重要な設計判断は何か？

> A. Grafana のアラートルールとダッシュボードをすべてプロビジョニングファイル（YAML/JSON）でコード管理したこと（DR-75 の IaC 原則）。`docker compose down -v` でコンテナを完全削除しても、`docker compose up` で全設定が自動復元される。手動 UI 設定では「設定が消えた」「再現できない」という問題が本番環境で深刻なインシデントになる。監視設定をコードとして Git 管理することが、運用の信頼性の基礎。

---

*以上が Milestone 9 — Grafana Alerting + InfluxDB Tasks のアーキテクチャレビューです。*
*M1〜M9 でデータ取得・伝送・AI 分析・OTA・時系列 DB・フリート管理・監視・通知・データ圧縮まで、*
*SDV クラウドバックエンドの主要レイヤーが揃いました。*
