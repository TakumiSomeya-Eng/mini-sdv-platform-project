# M7 FRD — 時系列 DB + Grafana 可視化

## L3 Domain Hypothesis (ドメインルール)

| ID | ルール | 根拠 |
|----|--------|------|
| DR-70 | `influxdb-writer` は Kuksa Databroker に対して READ ONLY。`set_current_values()` 呼び出し禁止 | 信号源はECU Simulatorのみ |
| DR-71 | InfluxDB measurement 名は `vehicle_signals` に統一 | Grafana クエリの一貫性 |
| DR-72 | タグ: `vehicle_id`（例: `vehicle-001`）、`signal`（例: `Speed`） | フリート拡張 (M7B) への対応 |
| DR-73 | フィールド: `value` (float64) のみ | シンプルな時系列スキーマ |
| DR-74 | InfluxDB バケット名: `sdv`、組織名: `sdv-org` | 設定の一元管理 |
| DR-75 | Grafana ダッシュボードは JSON ファイルとしてプロビジョニング（UI手動設定禁止） | Infrastructure as Code、再現性保証 |
| DR-76 | 書き込みは Kuksa gRPC サブスクリプションのイベント駆動（ポーリング禁止） | 低レイテンシ、mqtt-bridge と同パターン |
| DR-77 | Grafana デフォルト表示範囲: Last 30 minutes、自動更新: 5秒 | OTA前後の変化が1画面に収まる |

## L4 Interaction Hypothesis (UI/UX)

### 4.1 Grafana ダッシュボード構成

```
┌─────────────────────────────────────────────────────┐
│  Vehicle Signals — Last 30 min  [auto-refresh: 5s]  │
├──────────────┬──────────────┬───────────────────────┤
│ Vehicle Speed│ Battery SoC  │  Cabin Temperature    │
│  (km/h)      │  (%)         │  (°C)                 │
│  Line chart  │  Line chart  │  Line chart           │
│  0–160       │  0–100       │  15–30                │
└──────────────┴──────────────┴───────────────────────┘
```

- 各パネルは同一時間軸で同期
- OTA アップデート後（Speed 上限 120→150）の変化がグラフ上で視認できる
- Grafana URL: http://localhost:3000（ブラウザから直接アクセス）
- ログイン不要（Anonymous Access 有効）

### 4.2 データフロー（追加分のみ）

```
Kuksa Databroker
      │
      │ gRPC Subscribe (既存パターン)
      ▼
influxdb-writer ──── write() ────▶ InfluxDB :8086
                                        │
                                   Flux query
                                        ▼
                                   Grafana :3000
```

### 4.3 対象シグナル（3系統）

| VSS パス | Grafana パネル名 | 単位 | Y軸範囲 |
|----------|----------------|------|---------|
| `Vehicle.Speed` | Vehicle Speed | km/h | 0–160 |
| `Vehicle.Powertrain.TractionBattery.StateOfCharge.Current` | Battery SoC | % | 0–100 |
| `Vehicle.Cabin.HVAC.AmbientAirTemperature` | Cabin Temperature | °C | 15–30 |
