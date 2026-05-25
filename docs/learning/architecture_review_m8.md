# Milestone 8 アーキテクチャレビュー & 学習ガイド
# マルチビークル・フリート管理（fleet-simulator + Fleet OTA）

---

## 目次

1. [M8 が追加するもの — フリート管理層](#1)
2. [フリート管理アーキテクチャの全体像](#2)
3. [vehicle-002/003 が Databroker をバイパスする理由](#3)
4. [fleet-simulator の設計](#4)
5. [Python threading による並行実行](#5)
6. [InfluxDB フリートデータモデル — vehicle_id タグの拡張性](#6)
7. [Grafana Fleet Overview ダッシュボード](#7)
8. [OTA フリート配信の設計](#8)
9. [Docker Compose サービス再利用パターン](#9)
10. [STATE_FILE 環境変数パターン](#10)
11. [エンドツーエンド データフロー追跡](#11)
12. [実際のフリート管理フレームワーク比較](#12)
13. [既知の制約と本番との差分](#13)
14. [M1〜M8 アーキテクチャ全体俯瞰](#14)
15. [復習クイズ](#15)

---

<a name="1"></a>
## 1. M8 が追加するもの — フリート管理層

### M1〜M7 の限界

M7 まで、プラットフォームは **vehicle-001 の単体シミュレーション**だった。

```
vehicle-001 のみ:
  ECU Simulator → CAN → Databroker → MQTT Bridge
                                    → InfluxDB Writer → Grafana
                                    → OTA Manager
```

InfluxDB のスキーマは `vehicle_id` タグで fleet-ready だったが、
データが1台分しか入っておらず、フリート管理の本質を体験できていなかった。

### M8 が解決すること

```
M8 追加後:

vehicle-001: ECU Simulator → CAN → Databroker → InfluxDB Writer ─┐
vehicle-002: fleet-simulator → MQTT + InfluxDB (直接)            ├→ Grafana Fleet Overview
vehicle-003: fleet-simulator → MQTT + InfluxDB (直接)            ─┘

                          OTA サーバー（マニフェスト共有）
                          ├── ota-manager       (vehicle-001)
                          ├── ota-manager-v002  (vehicle-002)
                          └── ota-manager-v003  (vehicle-003)
```

**M8 が実現すること:**
- 3 台の車両が並行してテレメトリを送信
- Grafana で車両横断の比較可視化
- OTA を 1 コマンドで全車両に同時配信
- 「単体車両の深い実装」から「フリートの水平スケール」への視点転換

---

<a name="2"></a>
## 2. フリート管理アーキテクチャの全体像

### 2.1 本番 SDV フリートのアーキテクチャ

```
各車両（物理）:
┌─────────────────────────────────────┐
│  ECU → CAN → Central Vehicle Computer │
│  CVC → Databroker → Telemetry Agent  │
│  Telemetry Agent → TCU               │
└─────────────────────────────────────┘
          │ MQTT / HTTPS over TLS
          ▼ (車両ごとに独立した接続)
┌─────────────────────────────────────────────────────┐
│                   クラウド                            │
│  IoT Core / MQTT Broker                             │
│    ├── sdv/vehicle-001/telemetry                    │
│    ├── sdv/vehicle-002/telemetry                    │
│    └── sdv/vehicle-003/telemetry                    │
│                │                                    │
│  Time-Series DB (InfluxDB / Timestream)             │
│    └── measurement: vehicle_signals                 │
│          tag: vehicle_id = "vehicle-001" | "002"... │
│                │                                    │
│  Visualization (Grafana / OEM BI)                   │
│    └── Fleet Overview Dashboard                     │
└─────────────────────────────────────────────────────┘
```

### 2.2 M8 の簡略化とその意図

| 要素 | 本番 | M8（開発） |
|------|------|-----------|
| 各車両の通信経路 | 独立した物理 TCP 接続 | 同一ホストの MQTT + InfluxDB |
| 車両のデータブローカー | 車両ごとに Databroker インスタンス | vehicle-001 のみ Databroker あり |
| テレメトリ送信 | 各 TCU が独立して送信 | fleet-simulator が代理送信 |
| 認証 | mTLS 証明書（車両ごと） | 固定トークン |

M8 の fleet-simulator は「クラウド側から見た複数台テレメトリ受信」を
最小コストで体験するための模倣。本番の vehicle-002/003 は
それぞれ独立した Linux 上で同じサービス群を動かしている。

---

<a name="3"></a>
## 3. vehicle-002/003 が Databroker をバイパスする理由

### なぜ Databroker を追加しないのか

Kuksa Databroker は**単一車両向け**のミドルウェアとして設計されている。
複数車両を管理するための「namespace」や「vehicle_id ルーティング」機能を持たない。

本番では各車両が**自分の Databroker インスタンス**を持つ。
M8 でそれを再現しようとすると:

```
databroker-v001: port 55555
databroker-v002: port 55556  ← 追加が必要
databroker-v003: port 55557  ← 追加が必要
can-gateway-v002            ← 追加が必要（vcan1 が必要）
can-gateway-v003            ← 追加が必要（vcan2 が必要）
ecu-simulator-v002          ← 追加が必要
ecu-simulator-v003          ← 追加が必要
```

これは学習目的に対してコストが高すぎる。

### fleet-simulator のアプローチ

```
fleet-simulator
  → MQTT: sdv/vehicle-002/telemetry  → Mosquitto（既存）
  → MQTT: sdv/vehicle-003/telemetry  → Mosquitto（既存）
  → InfluxDB: vehicle_signals{vehicle_id="vehicle-002"}
  → InfluxDB: vehicle_signals{vehicle_id="vehicle-003"}
```

クラウド側から見ると、vehicle-002/003 のテレメトリは
vehicle-001 のものと**完全に同じ形式**で届く。
Grafana と InfluxDB には vehicle_id タグの違いしか見えない。

これが「クラウド側の視点からフリートを体験する」設計の本質。

---

<a name="4"></a>
## 4. fleet-simulator の設計

### 4.1 VehicleState — ランダムウォーク

```python
class VehicleState:
    def __init__(self, vehicle_id: str) -> None:
        seed = int(vehicle_id.split("-")[-1])  # vehicle-002 → 2
        self._rng = random.Random(seed * 1000 + int(time.time()) % 1000)
        
        # 初期値（v1.0.0 パラメータ範囲）
        self.speed = self._rng.uniform(30, 90)
        self.soc   = self._rng.uniform(60, 95)
        self.cabin = self._rng.uniform(20, 23)

    def step(self) -> None:
        # Speed: ランダムウォーク 10〜120 km/h
        self.speed = max(10.0, min(120.0, self.speed + self._rng.uniform(-5, 5)))
        
        # SoC: 徐々にドレイン → 20% で充電シミュレーション
        self.soc = max(5.0, min(100.0, self.soc - 0.05))
        if self.soc < 20.0:
            self.soc = self._rng.uniform(80, 95)
        
        # CabinTemp: 21°C 付近で小さく揺れる
        self.cabin = max(19.5, min(24.5, self.cabin + self._rng.uniform(-0.3, 0.3)))
```

**シード設計のポイント:**
- `seed = int(vehicle_id.split("-")[-1])` で vehicle-002=2、vehicle-003=3 を導出
- 起動時刻も加味（`int(time.time()) % 1000`）することで、
  同時起動でも車両ごとに異なる初期値になる
- 2 台が同じ動きをしないよう、乱数列を意図的に分離

### 4.2 MQTT + InfluxDB への書き込み

```python
def simulate_vehicle(vehicle_id, mqtt, write_api):
    state = VehicleState(vehicle_id)
    topic = f"sdv/{vehicle_id}/telemetry"

    while True:
        state.step()
        signals = state.signals()  # {"Speed": 87.3, "BatterySoC": 82.1, "CabinTemp": 22.4}

        # MQTT: JSON ペイロード
        mqtt.publish(topic, state.mqtt_payload(), qos=0)

        # InfluxDB: 3 Point をまとめてバッチ書き込み
        points = [
            Point("vehicle_signals")
            .tag("vehicle_id", vehicle_id)
            .tag("signal", sig)
            .field("value", val)
            for sig, val in signals.items()
        ]
        write_api.write(bucket="sdv", record=points)

        time.sleep(INTERVAL_SEC)
```

3 つの Point をリストにまとめて一度の `write()` 呼び出しで送ることで、
InfluxDB への往復回数を 1/3 に削減している。

---

<a name="5"></a>
## 5. Python threading による並行実行

### 5.1 なぜ asyncio ではなく threading か

vehicle-002/003 のシミュレーションループは:
1. `time.sleep(INTERVAL_SEC)` で I/O 待ちが発生する
2. InfluxDB への書き込み（SYNCHRONOUS モード）でブロッキング I/O が発生する

`asyncio` は非同期 I/O（ノンブロッキング）が前提。
`influxdb-client` の SYNCHRONOUS モードはブロッキング呼び出しのため、
asyncio と組み合わせると `loop.run_in_executor` が必要になり複雑になる。

`threading` を使えばブロッキング I/O でも並行動作し、実装がシンプル。

### 5.2 daemon スレッドの意味

```python
t = threading.Thread(
    target=simulate_vehicle,
    args=(vid, mqtt, write_api),
    daemon=True,  # ← メインスレッド終了時に強制終了
)
```

`daemon=True` を設定しない場合、メインスレッドが終了しても
子スレッドが生き続けてプロセスが終了しない。
daemon スレッドはメインスレッドの終了に追従して自動終了する。

### 5.3 GIL（Global Interpreter Lock）との関係

Python の GIL は CPU バウンドな処理では並列化を妨げる。
しかし I/O バウンド（sleep、ネットワーク書き込み）な処理では
GIL が解放されるため、threading で実質的な並列 I/O が実現する。

```
Thread-vehicle-002: step() → sleep(2) ↓GIL解放 → write() → step()...
Thread-vehicle-003:          step() → sleep(2) ↓GIL解放 → write() → step()...
```

ECU シミュレーションのような計算量が少なく I/O 中心のワークロードには threading が最適。

---

<a name="6"></a>
## 6. InfluxDB フリートデータモデル — vehicle_id タグの拡張性

### 6.1 M7 で仕込んだ拡張性

M7 でデータスキーマを設計した際に、`vehicle_id` タグを追加していた:

```python
# M7 influxdb-writer
Point("vehicle_signals")
    .tag("vehicle_id", VEHICLE_ID)  # ← これがあったため M8 はスキーマ変更なし
    .tag("signal", label)
    .field("value", float(datapoint.value))
```

M8 で fleet-simulator を追加しただけで、InfluxDB 側は**一切変更不要**。
`vehicle_id` タグの値が増えるだけで、自動的に新しいシリーズが生成される。

```
M7 まで:
  vehicle_signals{vehicle_id="vehicle-001", signal="Speed"} → 1 シリーズ

M8 以降:
  vehicle_signals{vehicle_id="vehicle-001", signal="Speed"} → シリーズ 1
  vehicle_signals{vehicle_id="vehicle-002", signal="Speed"} → シリーズ 2
  vehicle_signals{vehicle_id="vehicle-003", signal="Speed"} → シリーズ 3
```

これが「スキーマ設計時に拡張性を意識する」ことの実際の効果。

### 6.2 シリーズ数とカーディナリティ

| 条件 | シリーズ数 |
|------|-----------|
| 1 台 × 3 シグナル | 3 |
| 3 台 × 3 シグナル | 9 |
| 100 台 × 3 シグナル | 300 |
| 10,000 台 × 3 シグナル | 30,000 |

InfluxDB の推奨上限はシリーズ数 < 1,000,000。
10,000 台のフリートでも 30,000 シリーズなので問題なし。

本番でよくある高カーディナリティ問題の例:
- タグに `session_id`（毎回変わる UUID）を使うと、
  1 台 × 1,000,000 セッション = 1,000,000 シリーズ → メモリ枯渇

---

<a name="7"></a>
## 7. Grafana Fleet Overview ダッシュボード

### 7.1 vehicle_signals.json との差分

| 項目 | vehicle_signals.json (M7) | fleet_overview.json (M8) |
|------|--------------------------|-------------------------|
| vehicle_id フィルタ | なし（全台表示） | なし（全台表示） |
| パネル幅 | w=8（3列並び） | w=24（1列、横幅最大） |
| 凡例 | 非表示 | 表示（vehicle_id 自動） |
| 凡例の統計値 | なし | Last, Max/Min/Mean |
| tooltip | single | multi（全台同時表示） |

### 7.2 vehicle_id の自動凡例表示

Flux クエリに vehicle_id フィルタを書かないと、
異なる vehicle_id タグを持つシリーズが別々の折れ線として描画される。
Grafana がタグ値（vehicle-001, vehicle-002, vehicle-003）を
自動的に凡例ラベルとして使用する。

```flux
from(bucket: "sdv")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r["_measurement"] == "vehicle_signals")
  |> filter(fn: (r) => r["signal"] == "Speed")
  # vehicle_id のフィルタなし → 全車両が自動的に別シリーズになる
  |> filter(fn: (r) => r["_field"] == "value")
  |> aggregateWindow(every: v.windowPeriod, fn: mean, createEmpty: false)
  |> yield(name: "mean")
```

### 7.3 multi tooltip の意味

```json
"tooltip": { "mode": "multi", "sort": "none" }
```

`single`: カーソルを当てた折れ線の値のみ表示  
`multi`: 同一時刻の全シリーズの値を一覧表示

フリートダッシュボードでは `multi` が有用。
同じ時刻に vehicle-001=92.3, vehicle-002=78.1, vehicle-003=105.6 のように
全台の値を比較できる。

---

<a name="8"></a>
## 8. OTA フリート配信の設計

### 8.1 「共有マニフェスト」モデル

```
OTA サーバー（マニフェスト共有）
  manifest.json: { "latest_version": "1.1.0", ... }
       │
       │ GET /manifest（各車両が独立してポーリング）
       ├── ota-manager       (vehicle-001)  → 30秒ごとにポーリング
       ├── ota-manager-v002  (vehicle-002)  → 30秒ごとにポーリング
       └── ota-manager-v003  (vehicle-003)  → 30秒ごとにポーリング
```

`fleet-ota-trigger.sh` が `POST /release/1.1.0` を**1回**呼ぶだけで、
次のポーリングサイクルで全車両がアップデートを検知して適用する。

### 8.2 各車両の OTA 進捗が独立している理由

```python
# ota-manager/main.py（M8 の1行変更）
STATE_FILE = os.environ.get("OTA_STATE_FILE", "/tmp/ota_state.json")
```

| 車両 | OTA_STATE_FILE |
|------|---------------|
| vehicle-001 | /tmp/ota_state.json（デフォルト） |
| vehicle-002 | /tmp/ota_state_v002.json |
| vehicle-003 | /tmp/ota_state_v003.json |

それぞれが独立した「インストール済みバージョン」を記録するため、
一方の車両の OTA 失敗が他の車両に影響しない。

### 8.3 MQTT ステータストピックの分離

```
sdv/vehicle-001/ota/status → vehicle-001 の進捗
sdv/vehicle-002/ota/status → vehicle-002 の進捗
sdv/vehicle-003/ota/status → vehicle-003 の進捗

# ワイルドカード購読で全台を一括監視
mosquitto_sub -h localhost -p 1883 -t "sdv/+/ota/status" -v
```

`+` ワイルドカードは 1 レベルのトピックセグメントにマッチする。
`#` は末尾の全レベルにマッチする（`sdv/#` は `sdv/vehicle-001/ota/status` も含む）。

### 8.4 fleet-ota-trigger.sh の設計

```bash
#!/bin/bash
VERSION="${1:-1.1.0}"
OTA_SERVER="${OTA_SERVER_URL:-http://localhost:8080}"

# マニフェスト更新（全車両共有）
curl -s -X POST "${OTA_SERVER}/release/${VERSION}"

# MQTT 監視（10 秒間）
timeout 10 mosquitto_sub -h localhost -p 1883 -t "sdv/+/ota/status" -v
```

`timeout 10` は 10 秒後に `mosquitto_sub` を強制終了する GNU coreutils のコマンド。
スクリプトがハングしないための安全策。

---

<a name="9"></a>
## 9. Docker Compose サービス再利用パターン

### 9.1 同一イメージを複数サービスで再利用

```yaml
# ota-manager（vehicle-001 用）
ota-manager:
  build:
    context: ./services/ota-manager  # ← 同じ Dockerfile
  environment:
    VEHICLE_ID: "vehicle-001"
    OTA_STATE_FILE: /tmp/ota_state.json

# ota-manager（vehicle-002 用）
ota-manager-v002:
  build:
    context: ./services/ota-manager  # ← 同じ Dockerfile
  environment:
    VEHICLE_ID: "vehicle-002"
    OTA_STATE_FILE: /tmp/ota_state_v002.json
```

**同一イメージ・異なる設定**というパターンは本番でも頻繁に使われる:
- Kubernetes の ReplicaSet（同一 Pod 仕様を N 個起動）
- 12 Factor App の原則: "設定を環境変数に外部化する"

### 9.2 コンテナ名と Docker ビルドキャッシュ

`ota-manager-v002` は `ota-manager` と同じ `context: ./services/ota-manager` を参照するため、
**Docker ビルドキャッシュが共有される**。
`docker compose build ota-manager-v002` は `ota-manager` のビルドが済んでいれば即完了。

### 9.3 network_mode: host でのポート競合

全サービスが `network_mode: host` のため、同じポートを複数サービスで使えない。
ota-manager-v002/v003 は HTTP サーバーを持たないため競合しない。
InfluxDB (8086) や Grafana (3000) はシングルインスタンスで問題なし。

---

<a name="10"></a>
## 10. STATE_FILE 環境変数パターン

### 10.1 変更前後の比較

```python
# 変更前（M6まで）: ハードコード
STATE_FILE = "/tmp/ota_state.json"

# 変更後（M8）: 環境変数で上書き可能
STATE_FILE = os.environ.get("OTA_STATE_FILE", "/tmp/ota_state.json")
```

この 1 行の変更により:
- vehicle-001 はデフォルト値を使うため**後方互換性を維持**
- vehicle-002/003 は異なるパスを指定して**独立した状態管理**が可能に

### 10.2 12 Factor App の原則 III — 設定

> 「設定（Config）をコードから分離し、環境変数に格納する」

コードに設定値をハードコードすると:
- 異なる環境（開発/ステージング/本番）でコードを書き換える必要がある
- 同一イメージを複数インスタンスで起動できない

環境変数に外部化すると:
- 同一イメージが任意の設定で動作する
- 設定の変更がコードの変更を伴わない
- Kubernetes の ConfigMap/Secret で管理できる

### 10.3 デフォルト値の設計

```python
STATE_FILE = os.environ.get("OTA_STATE_FILE", "/tmp/ota_state.json")
#                            ↑ 環境変数名       ↑ デフォルト値（後方互換性）
```

デフォルト値を設定することで:
- 既存の docker-compose.yml を変更しなくても vehicle-001 は動く
- 新規インスタンス（v002/v003）は明示的に環境変数を指定する

---

<a name="11"></a>
## 11. エンドツーエンド データフロー追跡

fleet-ota-trigger.sh を実行した後、vehicle-002 に OTA が適用されるまでの完全なトレース:

```
① fleet-ota-trigger.sh
   curl -X POST http://localhost:8080/release/1.1.0

② OTA サーバー（Flask）
   manifest.json の latest_version を "1.1.0" に更新
   HTTP 200 返却

③ ota-manager-v002（次のポーリングサイクル、最大 30 秒後）
   [CHECK] GET http://localhost:8080/manifest
   → { "latest_version": "1.1.0", ... }
   installed = "1.0.0"（/tmp/ota_state_v002.json から読み込み）
   1.1.0 > 1.0.0 → アップデートあり
   publish: sdv/vehicle-002/ota/status { "phase": "downloading", ... }

④ [DOWNLOAD] GET http://localhost:8080/packages/1.1.0.tar.gz
   → /tmp/ota-staging/1.1.0.tar.gz に保存

⑤ [VERIFY] SHA-256 ハッシュ計算
   expected: 21474495df885bb58704d85823ac7c4aa0c0efe1fb15413d6a9402f621d2e0a8
   actual:   （計算結果が一致）→ OK

⑥ [APPLY] tar.gz から ecu_config.json を展開
   shutil.copy2(extracted, "/tmp/ecu_config_v002.json")
   ※ vehicle-002 は fleet-simulator で動いているため、
     このファイルは実際には使用されない（vehicle-001 の ecu-simulator と同様の位置付け）

⑦ [REPORT] /tmp/ota_state_v002.json に version="1.1.0" を保存
   publish: sdv/vehicle-002/ota/status { "phase": "complete", "version": "1.1.0" }

⑧ mosquitto_sub で確認:
   sdv/vehicle-002/ota/status {"phase": "complete", "version": "1.1.0", ...}
   sdv/vehicle-003/ota/status {"phase": "complete", "version": "1.1.0", ...}
```

---

<a name="12"></a>
## 12. 実際のフリート管理フレームワーク比較

| フレームワーク | 提供元 | OTA 方式 | テレメトリ |
|--------------|--------|----------|----------|
| **Eclipse hawkBit** | Eclipse Foundation | プル型、マニフェスト | なし（連携必要） |
| **Mender** | Northern.tech | プル型、アーティファクト署名 | Mender Monitor |
| **UPTANE** | Linux Foundation | ディレクタ/イメージ2リポジトリ | なし（標準外） |
| **AWS IoT + Greengrass** | Amazon | プッシュ型、デプロイメント | IoT Core + Timestream |
| **Azure IoT Hub + ADU** | Microsoft | プル型、デプロイメントグループ | IoT Hub + ADX |
| **Google Cloud IoT** | Google | プッシュ型 | Cloud IoT Core |
| **Bosch IoT Suite** | Bosch | Rollout Management | Bosch IoT Insights |

### M8 との対応関係

| M8 の要素 | 本番での対応物 |
|----------|--------------|
| OTA サーバー（Flask） | Eclipse hawkBit / AWS ADM |
| manifest.json | UPTANE イメージリポジトリ |
| fleet-ota-trigger.sh | hawkBit UI の "Rollout" / AWS Deployment |
| ota-manager（各車両） | Mender Client / Greengrass |
| MQTT ステータス | hawkBit フィードバック API |
| InfluxDB + Grafana | AWS Timestream + Grafana Cloud |

### フリート管理の本番スケール例

```
GM SuperCruise: 約 400,000 台の OTA 管理
Tesla: 数百万台を週次 OTA
VW Group: CARIAD プラットフォームで全ブランド共通 OTA
```

これらのシステムはすべて「共有マニフェスト→各車両が独立ポーリング」という
M8 と同じ基本原理の上に認証・段階ロールアウト・ロールバックを追加している。

---

<a name="13"></a>
## 13. 既知の制約と本番との差分

| 制約 | 本プロジェクト（M8） | 本番 SDV |
|------|---------------------|---------|
| 車両の物理的独立性 | 同一ホストで模倣 | 物理的に分離した車載コンピュータ |
| 通信の独立性 | 同一 Docker ネットワーク | セルラー/V2X ネットワーク |
| OTA 段階ロールアウト | なし（全台同時） | カナリアリリース（1% → 10% → 100%） |
| OTA ロールバック | なし | 署名付きバージョン履歴 + 自動ロールバック |
| 車両認証 | なし | mTLS 証明書（車両ごと） |
| テレメトリ暗号化 | なし | TLS 1.3 |
| fleet-simulator の現実性 | CAN/Databroker をバイパス | 各車両が独立したパイプラインを持つ |

---

<a name="14"></a>
## 14. M1〜M8 アーキテクチャ全体俯瞰

```
【車両内（WSL2 / Docker）】

ECU Simulator (M1/M4)
    │ CAN フレーム (M4: vcan0)
    ▼
CAN Gateway (M4)
    │ gRPC SetCurrentValues
    ▼
Kuksa Databroker (M1)
    │ gRPC Subscribe（扇形配信）
    ├──────────────────────────────────────────────────────────┐
    │                                                          │
    ▼                                                          ▼
MQTT Bridge (M2)                                    InfluxDB Writer (M7)
    │ MQTT publish                                       │ write()
    ▼                                                    ▼
Mosquitto (M2)                                     InfluxDB (M7)
    │                                                    │ Flux query
    ├── AI Monitor (M5)                                  ▼
    │   └── Claude API → 異常アラート                  Grafana (M7)
    │                                                    ├── Vehicle Signals (M7)
    └── Dashboard (M1/M5/M6)                            └── Fleet Overview (M8)
        └── Streamlit: リアルタイム表示

OTA Server (M6) ←─────────── fleet-ota-trigger.sh (M8)
    │ マニフェスト共有
    ├── OTA Manager (M6, vehicle-001)
    ├── OTA Manager v002 (M8, vehicle-002)
    └── OTA Manager v003 (M8, vehicle-003)

Fleet Simulator (M8)
    ├── vehicle-002 → MQTT + InfluxDB（直接）
    └── vehicle-003 → MQTT + InfluxDB（直接）

ROS2 Bridge (M3) → DDS → ros2-subscriber
```

**各マイルストーンが追加した「層」:**

| M | 追加した層 | キーコンセプト |
|---|-----------|--------------|
| M1 | 信号取得・可視化 | Kuksa Databroker, VSS, Streamlit |
| M2 | V2C テレメトリ | MQTT, JSON, クラウド境界 |
| M3 | AD スタック統合 | ROS2, DDS, COVESA VSS |
| M4 | 車載 CAN バス | SocketCAN, vcan0, CANフレーム |
| M5 | AI 監視エージェント | LLM, Observe→Reason→Act |
| M6 | OTA アップデート | CHECK→VERIFY→APPLY, UPTANE |
| M7 | 時系列 DB | InfluxDB, Flux, Grafana |
| M8 | フリート管理 | マルチビークル, 共有マニフェスト |

---

<a name="15"></a>
## 15. 復習クイズ

**Q1.** vehicle-002/003 が Kuksa Databroker を経由しない理由を2つ答えよ。

> A. ①Databroker は単一車両向け設計で、マルチ車両のルーティング機能を持たない。②各車両に Databroker インスタンスを追加するとポート管理・vcan インタフェース・CAN ゲートウェイも増えてコストが高い。fleet-simulator はクラウド側から見た「複数台テレメトリ受信」を最小コストで模倣する。

---

**Q2.** `random.Random(seed)` でシードを車両ごとに変える設計の意図は何か？

> A. vehicle-002 と vehicle-003 が同じ乱数列を持つと、2台の車両が完全に同じ値を生成し続けてしまう。シードを `vehicle_id` から導出することで、各車両が独立したランダムウォークを行い、現実的なフリートの多様性（車両ごとに異なる走行状態）を再現する。

---

**Q3.** Python threading が asyncio より適している理由を、このユースケースの観点から説明せよ。

> A. `influxdb-client` の SYNCHRONOUS 書き込みモードはブロッキング I/O。asyncio は非同期（ノンブロッキング）I/O を前提とするため、ブロッキング呼び出しと組み合わせるには `run_in_executor` が必要で実装が複雑になる。threading ならブロッキング I/O でも各スレッドが独立して待機でき、I/O 中は GIL が解放されるため実質的な並列 I/O が実現する。

---

**Q4.** M7 のスキーマ設計（vehicle_id タグ）が M8 でそのまま活用できた理由を説明せよ。

> A. M7 で `vehicle_id` をタグとしてスキーマに含めていたため、M8 で新しい vehicle_id 値（vehicle-002/003）を持つデータを書き込むだけで新しいシリーズが自動生成された。InfluxDB のスキーマ変更は不要。Grafana のクエリも vehicle_id フィルタを入れなければ全台が自動的に別シリーズとして表示される。

---

**Q5.** `fleet-ota-trigger.sh` が `POST /release/1.1.0` を1回だけ呼ぶだけで全車両に OTA が配信できる理由を説明せよ。

> A. OTA サーバーのマニフェスト（`latest_version`）は全車両共有のため、1回の API 呼び出しで全 ota-manager インスタンスが参照する値が変わる。各 ota-manager は独立したポーリングサイクルでマニフェストを取得し、自身のインストール済みバージョンと比較して更新があれば独立して適用する。車両ごとの個別コマンドは不要。

---

**Q6.** `OTA_STATE_FILE` を環境変数化した変更が「後方互換性を維持している」と言える理由は？

> A. `os.environ.get("OTA_STATE_FILE", "/tmp/ota_state.json")` でデフォルト値を設定しているため、環境変数を指定しない vehicle-001 の既存設定はそのまま動作する。変更は加法的（additive）で、既存の動作を破壊しない。

---

**Q7.** MQTT のワイルドカード `+` と `#` の違いを、OTA ステータス監視の例で説明せよ。

> A. `+` は1レベルのセグメントにマッチ。`sdv/+/ota/status` は `sdv/vehicle-001/ota/status`、`sdv/vehicle-002/ota/status` にマッチするが、`sdv/vehicle-001/alerts/ai` にはマッチしない（セグメント数が異なる）。`#` は末尾の全レベルにマッチ。`sdv/#` は `sdv/vehicle-001/ota/status` も `sdv/vehicle-001/alerts/ai` も含む全トピックにマッチする。

---

**Q8.** Grafana Fleet ダッシュボードで `tooltip: mode: "multi"` を選ぶ理由は？

> A. フリートダッシュボードではカーソル位置の時刻における全車両の値を同時に比較したい。`single` モードではカーソルを当てた1本の折れ線の値しか表示されない。`multi` モードなら vehicle-001=92.3, vehicle-002=78.1, vehicle-003=105.6 のように全台の値が一覧表示され、車両間の比較が容易になる。

---

**Q9.** OTA 段階ロールアウト（カナリアリリース）を M8 に追加するとしたら、どこをどう変更するか？

> A. OTA サーバー側に「対象車両リスト」または「ロールアウト割合」の概念を追加する。例: `/release/1.1.0?target=vehicle-001` または `/release/1.1.0?rollout_pct=33`。ota-manager は `/manifest` を取得する際に自分の `VEHICLE_ID` をクエリパラメータで送り、サーバー側がその車両向けの `latest_version` を返すかどうかを制御する。

---

**Q10.** M1〜M8 で構築したプラットフォームのうち、本番 SDV で「最もギャップが大きい」のはどの部分か、理由とともに答えよ。

> A. セキュリティ層（通信暗号化・車両認証）。本番では全通信が TLS 1.3 で暗号化され、各車両は mTLS 証明書で認証される。OTA パッケージには非対称署名（UPTANE のディレクタ鍵・イメージ鍵）が必要で、中間者攻撃・なりすましへの対策が法規（UN ECE R155）で義務化されている。M1〜M8 のすべてのサービスは平文通信・固定トークン・無署名パッケージで動いており、このギャップが最も大きい。

---

*以上が Milestone 8 — マルチビークル・フリート管理 のアーキテクチャレビューです。*
*M1〜M8 で「単一車両の深い実装」から「フリートの水平スケール」まで一貫して構築しました。*
