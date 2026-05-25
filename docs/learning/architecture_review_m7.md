# Milestone 7 アーキテクチャレビュー & 学習ガイド
# 時系列 DB + Grafana 可視化（InfluxDB + Grafana）

---

## 目次

1. [M7 が追加するもの — データ永続化層](#1)
2. [時系列データベースとは何か](#2)
3. [InfluxDB の基本概念](#3)
4. [Flux クエリ言語](#4)
5. [influxdb-writer の設計](#5)
6. [データスキーマ設計の判断](#6)
7. [Grafana のアーキテクチャ](#7)
8. [Infrastructure as Code — プロビジョニング](#8)
9. [Docker Compose 設計](#9)
10. [SDV 文脈でのデータパイプライン](#10)
11. [エンドツーエンド データフロー追跡](#11)
12. [時系列 DB の比較](#12)
13. [既知の制約と本番との差分](#13)
14. [実際の車載テレメトリフレームワーク比較](#14)
15. [復習クイズ](#15)

---

<a name="1"></a>
## 1. M7 が追加するもの — データ永続化層

### M1〜M6 の問題点

M1〜M6 では、すべてのシグナルデータは**揮発性**だった。

```
ECU Simulator → CAN → Databroker → MQTT Bridge → Mosquitto
                                  → ROS2 Bridge → DDS
                                  → AI Monitor  → アラート (揮発)
                                  → OTA Manager → ECU 設定更新
```

どこにも「過去のデータ」が残らない。Streamlit ダッシュボードは現在値しか表示できず、
「OTA アップデート前後で速度レンジがどう変わったか」を時系列グラフで見ることができなかった。

### M7 が解決すること

```
                              ┌─────────────────────────────┐
Kuksa Databroker              │    M7 追加ブロック           │
      │                       │                             │
      │ gRPC Subscribe        │  influxdb-writer            │
      └──────────────────────▶│    │                        │
                              │    │ write(Point)           │
                              │    ▼                        │
                              │  InfluxDB 2.7               │
                              │    │                        │
                              │    │ Flux query             │
                              │    ▼                        │
                              │  Grafana 10.x               │
                              │    │                        │
                              │    ▼                        │
                              │  http://localhost:3000       │
                              └─────────────────────────────┘
```

**M7 が実現すること:**
- 車両シグナル履歴の永続化（再起動後も消えない）
- 時系列グラフでのトレンド分析
- OTA アップデート前後のパラメータ変化の可視化
- Grafana の標準的な IoT/SDV 可視化スタックの体験

---

<a name="2"></a>
## 2. 時系列データベースとは何か

### 通常の RDB との違い

| 観点 | PostgreSQL / MySQL | InfluxDB (時系列 DB) |
|------|-------------------|---------------------|
| データの特性 | 任意のエンティティ | **時刻付きの計測値** |
| 主キー | 任意の列 | **タイムスタンプ** |
| 書き込みパターン | ランダムな INSERT/UPDATE | **単調増加の追記のみ** |
| 読み込みパターン | 任意の WHERE | **時間範囲 + タグフィルタ** |
| 最適化 | B-Tree インデックス | **時間分割ストレージ (TSM)** |
| 集計関数 | AVG, SUM, COUNT | **+ downsampling, window** |

### なぜ車両データに時系列 DB が向いているか

```
時刻         | speed | soc  | cabin_temp
-------------|-------|------|----------
09:00:00.000 | 87.3  | 82.1 | 22.4
09:00:00.500 | 88.1  | 82.0 | 22.4
09:00:01.000 | 89.2  | 81.9 | 22.5
09:00:01.500 | 87.8  | 81.8 | 22.5
...
```

- 常に**追記のみ**（過去データを UPDATE することはない）
- **時間範囲クエリ**が主体（「過去 30 分の平均速度」など）
- データ量が膨大になるため**自動ダウンサンプリング**が必要
- この特性が時系列 DB の存在意義

---

<a name="3"></a>
## 3. InfluxDB の基本概念

### 3.1 データモデル階層

```
Organization (sdv-org)
  └── Bucket (sdv)
        └── Measurement (vehicle_signals)
              ├── Tags      (インデックスあり、文字列のみ)
              │     ├── vehicle_id = "vehicle-001"
              │     └── signal     = "Speed" | "BatterySoC" | "CabinTemp"
              ├── Fields    (インデックスなし、数値/文字列)
              │     └── value = 87.3 (float64)
              └── Timestamp = 2024-01-15T09:00:00Z (ナノ秒精度)
```

### 3.2 各概念の意味

**Organization（組織）**
- InfluxDB 2.x で導入されたマルチテナント単位
- 本プロジェクトでは `sdv-org` を使用

**Bucket（バケット）**
- データの保存先。保存期間（retention policy）を設定できる
- SQL の「データベース」に相当
- 本プロジェクトでは `sdv`（無期限保存）

**Measurement（計測）**
- SQL の「テーブル」に相当
- 本プロジェクトでは `vehicle_signals` に統一（DR-71）

**Tags（タグ）**
- **インデックスが貼られる**カラム。フィルタリング・グループ化に使う
- 文字列のみ。カーディナリティが低い値に適している
- 例: `vehicle_id`, `signal`
- ⚠️ タグが増えすぎると「High Series Cardinality」問題が発生

**Fields（フィールド）**
- 実際の計測値。インデックスなし
- 数値、文字列、bool を格納できる
- 本プロジェクトでは `value` (float64) のみ（DR-73）

### 3.3 Series（シリーズ）

`measurement + tags の組み合わせ` が一つの**シリーズ**を形成する。

```
vehicle_signals{vehicle_id="vehicle-001", signal="Speed"}      → シリーズ 1
vehicle_signals{vehicle_id="vehicle-001", signal="BatterySoC"} → シリーズ 2
vehicle_signals{vehicle_id="vehicle-001", signal="CabinTemp"}  → シリーズ 3
```

シリーズ数がパフォーマンスに直結するため、タグ設計が重要。

### 3.4 TSM（Time-Structured Merge Tree）

InfluxDB のストレージエンジン。LSM-Tree の時系列特化版。

```
Write Path:  WAL → Cache → TSM Files（圧縮後）
Read Path:   TSM Files + Cache → 時間範囲フィルタ → 結果
```

タイムスタンプの単調増加性を前提とした最適化で、
追記ワークロードに対して非常に高いスループットを発揮する。

### 3.5 自動初期化（DOCKER_INFLUXDB_INIT_*）

```yaml
environment:
  DOCKER_INFLUXDB_INIT_MODE: setup        # 初回起動時のみセットアップ実行
  DOCKER_INFLUXDB_INIT_USERNAME: admin    # 管理者ユーザー
  DOCKER_INFLUXDB_INIT_PASSWORD: sdv-password
  DOCKER_INFLUXDB_INIT_ORG: sdv-org       # Organization 作成
  DOCKER_INFLUXDB_INIT_BUCKET: sdv        # Bucket 作成
  DOCKER_INFLUXDB_INIT_ADMIN_TOKEN: sdv-token-local  # API トークン固定
```

`DOCKER_INFLUXDB_INIT_ADMIN_TOKEN` を固定することで、
influxdb-writer と Grafana が同じトークンを環境変数で参照できる。
本番では動的に発行したトークンを Secret Manager で管理する。

---

<a name="4"></a>
## 4. Flux クエリ言語

### 4.1 Flux とは

InfluxDB 2.x で導入されたデータスクリプト言語。
SQL よりも**パイプライン（|>）**を使った宣言的な記法。

### 4.2 基本構文

```flux
from(bucket: "sdv")                          // データソース
  |> range(start: -30m)                      // 時間範囲フィルタ（必須）
  |> filter(fn: (r) =>                       // 行フィルタ
      r["_measurement"] == "vehicle_signals"
      and r["signal"] == "Speed"
      and r["_field"] == "value"
  )
  |> aggregateWindow(                        // 時間ウィンドウ集計
      every: 10s,
      fn: mean,
      createEmpty: false
  )
  |> yield(name: "mean")                     // 結果出力
```

### 4.3 Grafana との連携変数

Grafana ダッシュボードは以下の変数を自動的に Flux クエリに注入する:

| 変数 | 意味 |
|------|------|
| `v.timeRangeStart` | ダッシュボードの時間範囲開始 |
| `v.timeRangeStop` | ダッシュボードの時間範囲終了 |
| `v.windowPeriod` | 自動計算されたウィンドウ幅 |

```flux
from(bucket: "sdv")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)  // Grafana が注入
  |> filter(fn: (r) => r["signal"] == "Speed")
  |> aggregateWindow(every: v.windowPeriod, fn: mean, createEmpty: false)
```

ユーザーが Grafana UI で時間範囲を変えると、クエリが自動的に再実行される。

### 4.4 InfluxQL との比較

```sql
-- InfluxQL (旧形式、SQL に近い)
SELECT mean("value") FROM "vehicle_signals"
WHERE "signal" = 'Speed' AND time >= now() - 30m
GROUP BY time(10s)

-- Flux (新形式、パイプライン)
from(bucket: "sdv")
  |> range(start: -30m)
  |> filter(fn: (r) => r["signal"] == "Speed")
  |> aggregateWindow(every: 10s, fn: mean)
```

Flux はより表現力が高く、join、pivot、カスタム関数が書ける。

---

<a name="5"></a>
## 5. influxdb-writer の設計

### 5.1 mqtt-bridge との対称性

influxdb-writer は mqtt-bridge と**完全に同じパターン**を踏む。
差分は「何に書き込むか」だけ。

```
mqtt-bridge:
  Kuksa gRPC Subscribe → mqtt.publish(topic, json)

influxdb-writer:
  Kuksa gRPC Subscribe → write_api.write(bucket, Point)
```

このパターンの一貫性が、SDV アーキテクチャにおける
「Databroker を中心とした扇形配信」を体現している。

### 5.2 接続リトライロジック

```python
def connect_influxdb() -> InfluxDBClient:
    retry = 2.0
    while True:
        try:
            client = InfluxDBClient(url=INFLUXDB_URL, token=INFLUXDB_TOKEN, org=INFLUXDB_ORG)
            client.ping()  # ← ヘルスチェック
            return client
        except Exception as exc:
            log.warning(f"InfluxDB connect failed: {exc}. Retrying in {retry:.0f}s...")
            time.sleep(retry)
            retry = min(retry * 2, 30.0)  # 指数バックオフ（上限 30 秒）
```

`client.ping()` で実際の到達確認をしてから返すため、
InfluxDB の起動完了前に書き込みを試みることがない。

### 5.3 Point の構築

```python
point = (
    Point("vehicle_signals")          # measurement
    .tag("vehicle_id", VEHICLE_ID)    # タグ（インデックスあり）
    .tag("signal", label)             # タグ（インデックスあり）
    .field("value", float(datapoint.value))  # フィールド（計測値）
    # タイムスタンプは InfluxDB サーバー側で付与（省略）
)
write_api.write(bucket=INFLUXDB_BUCKET, record=point)
```

タイムスタンプをクライアント側で付与しない理由:
- ネットワーク遅延によるずれを避ける
- InfluxDB のサーバータイムが一貫性を保証する

### 5.4 SYNCHRONOUS 書き込みモード

```python
from influxdb_client.client.write_api import SYNCHRONOUS

write_api = influx.write_api(write_options=SYNCHRONOUS)
```

| モード | 動作 | 用途 |
|--------|------|------|
| SYNCHRONOUS | 書き込み完了まで待機 | シンプル、エラーがすぐわかる |
| ASYNCHRONOUS | バックグラウンドで書き込み | 高スループット（バッファリング） |
| Batching | N件/N秒でまとめて書き込み | 大量データ、本番環境 |

本プロジェクトでは SYNCHRONOUS を選択。
シグナル更新頻度（数Hz）では十分なパフォーマンス。

### 5.5 シグナルラベルのマッピング

```python
SIGNAL_LABELS = {
    "Vehicle.Speed": "Speed",
    "Vehicle.Powertrain.TractionBattery.StateOfCharge.Current": "BatterySoC",
    "Vehicle.Cabin.HVAC.AmbientAirTemperature": "CabinTemp",
}
```

VSS パスをそのままタグに使わない理由:
- Flux クエリが長くなる
- InfluxDB のシリーズキーが肥大化する
- Grafana パネルの表示名が冗長になる

---

<a name="6"></a>
## 6. データスキーマ設計の判断

### 6.1 「幅広テーブル」vs「正規化テーブル」

**幅広（Wide）テーブル案:**
```
measurement: vehicle_signals
fields: speed=87.3, battery_soc=82.1, cabin_temp=22.4
```

**正規化（Narrow）テーブル案（本プロジェクトの選択）:**
```
measurement: vehicle_signals, tag: signal="Speed",      field: value=87.3
measurement: vehicle_signals, tag: signal="BatterySoC", field: value=82.1
measurement: vehicle_signals, tag: signal="CabinTemp",  field: value=22.4
```

| 観点 | 幅広 | 正規化（選択） |
|------|------|--------------|
| クエリのシンプルさ | ◎ 1クエリで全信号 | △ 信号ごとに別クエリ |
| シグナル追加 | △ スキーマ変更が必要 | ◎ タグ値を増やすだけ |
| フリート拡張 | △ 全車両が同じ信号を持つ必要 | ◎ 車両ごとに信号セットが異なってよい |
| Grafana パネル | △ フィールド指定が複雑 | ◎ signal タグでフィルタするだけ |

正規化テーブルを選んだのは**フリート拡張性（M7B 候補）**を見据えているため。

### 6.2 タグとフィールドの設計原則

**タグ（Tags）に入れるもの:**
- フィルタリング・グループ化に使う
- カーディナリティが低い（値の種類が少ない）
- 変化しない（車両 ID、シグナル名）

**フィールド（Fields）に入れるもの:**
- 実際の計測値
- 連続的に変化する数値
- インデックス不要

⚠️ **高カーディナリティ問題**: タグにユーザー ID やセッション ID など
大量の異なる値を入れると、シリーズ数が爆発してメモリを圧迫する。
InfluxDB の推奨: シリーズ数 < 1,000,000。

---

<a name="7"></a>
## 7. Grafana のアーキテクチャ

### 7.1 Grafana の役割

```
データソース（InfluxDB）
      │
      │ クエリ（Flux）
      ▼
  Grafana Backend
      │
      │ JSON データ
      ▼
  Grafana Frontend（ブラウザ）
      │ レンダリング
      ▼
  パネル（折れ線グラフ etc.）
```

Grafana 自体はデータを持たない。
あくまで**クエリ → 可視化**のブリッジ。

### 7.2 パネル種別

本プロジェクトで使用: **Time Series（時系列）**

```json
{
  "type": "timeseries",
  "fieldConfig": {
    "defaults": {
      "unit": "velocitykmh",   // 単位（km/h、%、°C）
      "min": 0,
      "max": 160,
      "custom": {
        "drawStyle": "line",
        "lineWidth": 2,
        "fillOpacity": 10
      }
    }
  }
}
```

### 7.3 自動更新（Auto-Refresh）

```json
{
  "refresh": "5s",
  "time": { "from": "now-30m", "to": "now" }
}
```

Grafana は 5 秒ごとに Flux クエリを再実行し、
最新データをパネルに反映する。
これにより「疑似リアルタイム」の可視化を実現。

---

<a name="8"></a>
## 8. Infrastructure as Code — プロビジョニング

### 8.1 なぜ手動設定ではなくコード管理するか

Grafana は UI から手動でダッシュボードを作成できる。
しかし手動設定には以下の問題がある:

- `docker compose down -v` でコンテナを削除すると設定も消える
- チームメンバーが同じ環境を再現できない
- Git で変更履歴を追跡できない

**Provisioning（プロビジョニング）** = 設定ファイルを YAML/JSON で記述し、
起動時に自動ロードする仕組み。

### 8.2 プロビジョニングファイル構造

```
config/grafana/
  grafana.ini                              ← Grafana 本体の設定
  provisioning/
    datasources/
      influxdb.yaml                        ← データソース定義
    dashboards/
      dashboards.yaml                      ← ダッシュボードプロバイダ設定
      vehicle_signals.json                 ← ダッシュボード定義
```

### 8.3 データソースプロビジョニング

```yaml
# config/grafana/provisioning/datasources/influxdb.yaml
apiVersion: 1
datasources:
  - name: InfluxDB
    uid: influxdb-sdv          # ← ダッシュボード JSON から参照する ID
    type: influxdb
    access: proxy              # Grafana サーバーが InfluxDB にクエリ
    url: http://localhost:8086
    jsonData:
      version: Flux            # InfluxQL ではなく Flux を使用
      organization: sdv-org
      defaultBucket: sdv
    secureJsonData:
      token: sdv-token-local
    isDefault: true
    editable: false            # UI からの変更を禁止
```

`uid: influxdb-sdv` はダッシュボード JSON 内で参照される。
この UID が一致しないとデータソースが見つからずパネルが空になる。

### 8.4 ダッシュボード JSON の要点

```json
{
  "uid": "vehicle-signals",        // ダッシュボード固有 ID
  "title": "Vehicle Signals",
  "refresh": "5s",
  "time": { "from": "now-30m", "to": "now" },
  "panels": [
    {
      "datasource": {
        "type": "influxdb",
        "uid": "influxdb-sdv"      // データソース UID と一致必須
      },
      "targets": [
        {
          "query": "from(bucket: \"sdv\")..."
        }
      ]
    }
  ]
}
```

### 8.5 Anonymous Access 設定

```ini
# config/grafana/grafana.ini
[auth.anonymous]
enabled = true
org_role = Viewer
```

開発環境でログイン不要にするための設定。
本番では無効化し、LDAP/OAuth2 を使う。

---

<a name="9"></a>
## 9. Docker Compose 設計

### 9.1 依存関係グラフ（M7 追加分）

```
influxdb
    ▲
    │ depends_on
influxdb-writer ──── depends_on ──── databroker
    
grafana ──── depends_on ──── influxdb
```

### 9.2 InfluxDB の起動完了問題

`depends_on: condition: service_started` は「コンテナが起動した」だけを意味し、
「InfluxDB が初期化を完了した」ことは保証しない。

初回起動時は `DOCKER_INFLUXDB_INIT_MODE: setup` の処理（Bucket/Org 作成）に
数秒かかる。その間に influxdb-writer が接続を試みると失敗する。

対策: influxdb-writer の `connect_influxdb()` に指数バックオフを実装。
最初の接続失敗は想定内として、リトライで解消する。

### 9.3 network_mode: host との整合性

全サービスが `network_mode: host` を使用しているため、
コンテナ間通信はすべて `localhost` で行う。

```yaml
influxdb-writer:
  environment:
    INFLUXDB_URL: http://localhost:8086  # ← InfluxDB の host port
```

もしブリッジネットワークを使う場合は `http://influxdb:8086` になるが、
本プロジェクトでは vcan0（M4）のために host ネットワークを採用した経緯がある。

---

<a name="10"></a>
## 10. SDV 文脈でのデータパイプライン

### 10.1 本番 SDV でのデータフロー

```
車載 ECU
    │ CAN/Ethernet
    ▼
Central Vehicle Computer (CVC)
    │ VSS / SOME/IP
    ▼
Telemetry Agent（TCU または CVC 内蔵）
    │ MQTT / HTTPS over TLS
    ▼
    ┌────────────────────────────────────────┐
    │              クラウド                   │
    │                                        │
    │  Message Broker（Kafka / IoT Core）    │
    │        │                               │
    │        ▼                               │
    │  Stream Processor（Flink / Spark）     │
    │        │                               │
    │        ▼                               │
    │  Time-Series DB（InfluxDB / Timestream）│
    │        │                               │
    │        ▼                               │
    │  Visualization（Grafana / OEM BI）     │
    └────────────────────────────────────────┘
```

本プロジェクトの influxdb-writer は「Telemetry Agent」と
「Time-Series DB への直接書き込み」を兼ねた簡略版。

### 10.2 本番での追加要素

| 要素 | 本番 | M7（開発） |
|------|------|-----------|
| 通信暗号化 | TLS 1.3 必須 | なし |
| 認証 | mTLS / JWT | 固定トークン |
| バッファリング | Kafka / MQTT QoS 2 | なし（揮発） |
| スケーリング | InfluxDB クラスタ | シングルノード |
| ダウンサンプリング | Tasks で自動化 | aggregateWindow のみ |
| アラート | Grafana Alerting | なし |

### 10.3 OTA との組み合わせ（M6 + M7）

M6 の OTA アップデート（v1.0.0 → v1.1.0）の効果を
M7 の Grafana グラフで確認できる。

```
OTA 前（v1.0.0）: 速度レンジ 10〜120 km/h
        │
        │  curl -X POST http://localhost:8080/release/1.1.0
        ▼
OTA 後（v1.1.0）: 速度レンジ 20〜150 km/h
```

Grafana の Vehicle Speed パネルで、アップデート前後の
速度分布の変化が時系列グラフとして可視化される。

---

<a name="11"></a>
## 11. エンドツーエンド データフロー追跡

ECU Simulator が speed=132.5 km/h を送信した場合の完全なトレース:

```
① ECU Simulator
   vehicle.speed = 132.5
   → CAN フレーム送信（vcan0）

② CAN Gateway
   → Kuksa Databroker に gRPC SetCurrentValues

③ Kuksa Databroker
   → 全 Subscribe クライアントに通知

④ influxdb-writer（Subscribe 受信）
   path="Vehicle.Speed", value=132.5
   label = SIGNAL_LABELS["Vehicle.Speed"] = "Speed"
   point = Point("vehicle_signals")
             .tag("vehicle_id", "vehicle-001")
             .tag("signal", "Speed")
             .field("value", 132.5)
   write_api.write(bucket="sdv", record=point)

⑤ InfluxDB
   TSM エンジンに書き込み:
   vehicle_signals,vehicle_id=vehicle-001,signal=Speed value=132.5 <timestamp_ns>

⑥ Grafana（5 秒ごとのポーリング）
   Flux クエリ実行:
     from(bucket: "sdv")
       |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
       |> filter(fn: (r) => r["signal"] == "Speed")
       |> aggregateWindow(every: v.windowPeriod, fn: mean)

⑦ ブラウザ
   http://localhost:3000 の "Vehicle Speed" パネルに
   132.5 km/h が折れ線グラフとして表示される
```

---

<a name="12"></a>
## 12. 時系列 DB の比較

| DB | 特徴 | 適用場面 |
|----|------|---------|
| **InfluxDB 2.x** | Flux 言語、ダッシュボード内蔵、Telegraf エコシステム | IoT、APM、ネットワーク監視 |
| **TimescaleDB** | PostgreSQL 拡張、SQL 完全互換、Evidence.dev 対応 | 既存 PostgreSQL 資産との統合 |
| **Prometheus** | Pull 型、アラートルール、Kubernetes 標準 | マイクロサービス、インフラ監視 |
| **AWS Timestream** | サーバーレス、自動スケール、IAM 統合 | AWS 環境の本番 IoT |
| **Azure Data Explorer** | 大規模クエリ、KQL 言語 | Azure IoT Hub 連携、大規模フリート |
| **QuestDB** | 超高速書き込み（TCP direct）、SQL ライク | 高頻度 ECU データ（CAN 500kbps） |

車載テレメトリのユースケース別推奨:
- **開発・学習**: InfluxDB（Docker で即起動、Grafana との統合が最良）
- **本番 AWS**: Timestream（フルマネージド、コスト効率）
- **本番 Azure**: Azure Data Explorer（OEM では最も普及）
- **高頻度 CAN データ**: QuestDB（書き込み性能が桁違い）

---

<a name="13"></a>
## 13. 既知の制約と本番との差分

| 制約 | 本プロジェクト（M7） | 本番 SDV |
|------|---------------------|---------|
| データ保持期間 | 無制限（シミュレーション用） | 生データ 90 日、ダウンサンプル版 1 年+ |
| 認証 | 固定トークン（sdv-token-local） | 動的発行トークン + Secret Manager |
| 暗号化 | なし（ローカルネットワーク） | TLS 1.3（HTTPS + INFLUXDB over TLS） |
| 書き込みモード | SYNCHRONOUS（シンプル） | Batching（高スループット） |
| HA 構成 | シングルノード | InfluxDB クラスタ または クラウドマネージド |
| ダウンサンプリング | なし（aggregateWindow のみ） | Tasks API で自動化 |
| Grafana アラート | なし | Alerting + PagerDuty 連携 |

---

<a name="14"></a>
## 14. 実際の車載テレメトリフレームワーク比較

| フレームワーク | 提供元 | 説明 |
|--------------|--------|------|
| **Eclipse Kuksa** | Eclipse Foundation | VSS ベースの車載データブローカー（本プロジェクト採用） |
| **InfluxDB + Telegraf** | InfluxData | IoT テレメトリの実質標準スタック |
| **Apache Kafka** | Apache / Confluent | 大規模フリートのメッセージキュー |
| **AWS IoT Core + Timestream** | Amazon | フルマネージド V2C パイプライン |
| **Azure IoT Hub + ADX** | Microsoft | OEM で広く採用（BMW、VW など） |
| **COVESA VISSR** | COVESA | VSS の REST/WebSocket API 標準 |
| **Bosch IoT Suite** | Bosch | Ditto（デジタルツイン）+ テレメトリ |

---

<a name="15"></a>
## 15. 復習クイズ

**Q1.** InfluxDB の「タグ」と「フィールド」の最大の違いは何か？

> A. タグはインデックスが貼られフィルタリング・グループ化に使う。フィールドは実際の計測値でインデックスがない。タグに高カーディナリティな値（ユーザー ID など）を入れるとシリーズ数が爆発してメモリ問題が起きる。

---

**Q2.** influxdb-writer が `os.replace()` ではなく `write_api.write()` を使うのは当然だが、なぜタイムスタンプをクライアント側で付与しないのか？

> A. ネットワーク遅延や処理遅延によるずれを避けるため。InfluxDB サーバー側で受信時刻を付与することで、全データポイントの時刻の一貫性が保証される。

---

**Q3.** Grafana のプロビジョニングを使う目的を 2 つ答えよ。

> A. ①再現性：`docker compose down -v` でコンテナを削除・再作成しても設定が失われない。②バージョン管理：YAML/JSON ファイルとして Git で管理できるため、設定変更の履歴が追跡できる（Infrastructure as Code）。

---

**Q4.** `v.timeRangeStart`, `v.timeRangeStop`, `v.windowPeriod` は何が提供する変数か？

> A. Grafana が Flux クエリに自動注入するテンプレート変数。ユーザーが UI で時間範囲を変えると、これらの値が更新され、クエリが自動的に再実行される。

---

**Q5.** 本プロジェクトで「幅広テーブル」ではなく「正規化テーブル（signal タグ）」を選んだ主な理由は何か？

> A. フリート拡張性。正規化テーブルでは signal タグの値を増やすだけで新しいシグナルを追加でき、スキーマ変更が不要。また、車両ごとに異なる信号セットを持てる。

---

**Q6.** InfluxDB の初期化に `DOCKER_INFLUXDB_INIT_ADMIN_TOKEN` を固定値で設定する利点と、本番でやってはいけない理由を答えよ。

> A. 利点：influxdb-writer と Grafana が同じトークンを環境変数で参照でき、起動順序に依存しない。本番でやってはいけない理由：固定トークンがコード・設定ファイルに埋め込まれると漏洩リスクが高い。本番では InfluxDB Operator や Secret Manager で動的発行する。

---

**Q7.** `influxdb-writer` サービスが InfluxDB の起動完了前に接続を試みて失敗する問題を、本プロジェクトではどう解決したか？

> A. `connect_influxdb()` 関数で指数バックオフを実装（2秒→4秒→8秒…最大 30 秒）。`docker compose depends_on` は「コンテナ起動」を保証するだけで「サービス準備完了」を保証しないため、アプリ側でリトライを実装するのが正しいアプローチ。

---

**Q8.** Grafana の `refresh: "5s"` はどのような仕組みで動作するか？

> A. Grafana のフロントエンド（ブラウザ）が 5 秒ごとに Grafana バックエンドへ HTTP リクエストを送る。バックエンドは Flux クエリを InfluxDB に実行し、結果を JSON でフロントエンドに返す。フロントエンドがグラフを再レンダリングする。WebSocket ではなくポーリング方式。

---

**Q9.** M6（OTA）と M7（InfluxDB）を組み合わせると何が確認できるか？

> A. OTA アップデート（v1.0.0→v1.1.0）前後の速度レンジ変化（上限 120→150 km/h）が Grafana の Vehicle Speed パネルで時系列グラフとして可視化できる。時刻を軸に「更新前」と「更新後」のシグナル分布の違いが一目でわかる。

---

**Q10.** 本プロジェクト（M7）の influxdb-writer が担う役割を、実際の SDV アーキテクチャの文脈で説明せよ。

> A. 本番 SDV では TCU（Telematics Control Unit）または CVC 内蔵のテレメトリエージェントが、VSS シグナルを暗号化した上でクラウドの Message Broker（Kafka / IoT Core）に送信し、Stream Processor を経由して時系列 DB に書き込む。influxdb-writer はその「テレメトリエージェント + DB 直接書き込み」を簡略化したもの。暗号化・バッファリング・スケーリングが省略されている。

---

*以上が Milestone 7 — 時系列 DB + Grafana 可視化 のアーキテクチャレビューです。*
*M1〜M7 でデータ取得・伝送・AI 分析・OTA・可視化の全レイヤーが揃いました。*
