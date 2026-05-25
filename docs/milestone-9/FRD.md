# M9 FRD — Grafana Alerting + InfluxDB Tasks

## L3 Domain Hypothesis (ドメインルール)

### E: Grafana Alerting

| ID | ルール | 根拠 |
|----|--------|------|
| DR-90 | アラートルールは Grafana Alerting でプロビジョニングファイルとして定義（UI 手動設定禁止） | DR-75 の IaC 原則を維持 |
| DR-91 | Contact Point は Webhook（JSON POST）。webhook-receiver サービスが受信してログ出力 | Slack 等の外部サービス依存をなくしローカル完結 |
| DR-92 | アラート条件: `Speed > 130 km/h` OR `BatterySoC < 15%` | 現実的な車両異常閾値 |
| DR-93 | Evaluation interval: 10s、For duration: 0s（即 Firing） | テスト環境での応答速度を優先 |
| DR-94 | Notification Policy はデフォルト（全アラートを Webhook へ） | ルーティング複雑化を避ける |

### F: InfluxDB Tasks

| ID | ルール | 根拠 |
|----|--------|------|
| DR-95 | ダウンサンプリング先バケット: `sdv_1m`（保持期間: 30 日） | 元データ（`sdv`）と分離して管理 |
| DR-96 | Task 実行間隔: `every(1m)`、集計関数: `mean` | 2 秒ごとの生データを 1 分平均に圧縮 |
| DR-97 | Tasks は `scripts/setup-influxdb-tasks.sh` で InfluxDB API を呼び出して作成 | Flux スクリプトをコードとして Git 管理 |
| DR-98 | Grafana に `sdv_1m` 用データソースを追加しない。既存 `InfluxDB` データソースのクエリでバケット名を変えるだけ | 設定の最小変更 |

## L4 Interaction Hypothesis (UI/UX)

### E: Grafana Alerting フロー

```
① fleet-simulator が Speed > 130 の値を生成（自然に発生、またはパラメータ調整）

② Grafana Alerting エンジン（10s ごと評価）
   Flux クエリ → last() が 130 超 → Firing に遷移

③ webhook-receiver（http://localhost:9000）
   POST /webhook を受信 → コンテナログに JSON を出力

④ 確認方法:
   Grafana → http://localhost:3000 → Alerting → Alert rules
   docker compose logs -f webhook-receiver
```

**アラートルール一覧:**

| ルール名 | 条件 | 評価間隔 |
|---------|------|---------|
| High Speed Alert | Speed の last() > 130 | 10s |
| Low Battery Alert | BatterySoC の last() < 15 | 10s |

### F: InfluxDB Tasks フロー

```
① scripts/setup-influxdb-tasks.sh を WSL2 で実行
   → sdv_1m バケット作成
   → Downsample Task 作成（every 1m）

② 1 分後に InfluxDB が Task を自動実行
   sdv バケット（生データ）→ mean(1m) → sdv_1m バケット

③ 確認方法:
   InfluxDB UI → http://localhost:8086 → Tasks
   InfluxDB UI → Data Explorer → sdv_1m バケット
```
