# M10 FRD — セキュリティ層（TLS/mTLS + 証明書管理）

## L3 Domain Hypothesis (ドメインルール)

| ID | ルール | 根拠 |
|----|--------|------|
| DR-100 | 証明書ファイルはすべて `config/certs/` に格納する | 設定ファイルと分離、マウントしやすい |
| DR-101 | `config/certs/` は `.gitignore` に追加する（秘密鍵を Git 管理外にする） | 秘密鍵の漏洩防止。UN ECE R155 の基本要件 |
| DR-102 | `scripts/generate-certs.sh` 1 回の実行で CA・サーバー・クライアント証明書を全生成する | 再現性のある証明書管理手順 |
| DR-103 | Mosquitto は port 1883（平文）を廃止し、port 8883（TLS）のみに移行する | M10 の学習目標は「完全 TLS 化」であり、後方互換ポートを残さない |
| DR-104 | mTLS を有効化する（`require_certificate true`）。証明書なしの接続は拒否する | クライアント認証（WHO が接続しているかを検証）を体験する |
| DR-105 | すべての MQTT クライアントは**共通のクライアント証明書**（`client.crt` / `client.key`）を使用する | 個別証明書発行はスコープ外（本番での差分として学習ドキュメントで言及） |
| DR-106 | TLS 設定は環境変数（`MQTT_TLS=true` / `MQTT_PORT=8883` / `MQTT_CA_CERT` 等）で制御し、コードの条件分岐で適用する | 開発環境（TLS なし）と本番（TLS あり）を env var 切り替えだけで対応できる |
| DR-107 | 証明書の有効期限: CA = 10 年、サーバー / クライアント = 1 年（開発環境用） | 開発中に再生成が必要にならない期間を設定 |

## L4 Interaction Hypothesis (UI/UX)

### 4.1 証明書生成フロー

```bash
# WSL2 Ubuntu で 1 回だけ実行
bash scripts/generate-certs.sh

# 生成されるファイル:
config/certs/
  ca.crt          ← CA 証明書（全クライアントが信頼する公開証明書）
  ca.key          ← CA 秘密鍵（証明書署名に使用、厳重管理）
  server.crt      ← Mosquitto サーバー証明書
  server.key      ← Mosquitto サーバー秘密鍵
  client.crt      ← MQTT クライアント共通証明書
  client.key      ← MQTT クライアント共通秘密鍵
```

### 4.2 Mosquitto TLS 起動フロー

```bash
docker compose restart mosquitto

# 検証A: 証明書なし接続 → 拒否されること
mosquitto_sub -h localhost -p 8883 -t "sdv/#" -v
# → Error: A TLS error occurred.

# 検証B: 証明書付き接続 → 成功すること
mosquitto_sub \
  --cafile config/certs/ca.crt \
  --cert   config/certs/client.crt \
  --key    config/certs/client.key \
  -h localhost -p 8883 -t "sdv/#" -v
```

### 4.3 影響を受ける MQTT クライアント（更新が必要）

| サービス | 変更内容 |
|---------|---------|
| `mqtt-bridge` | `MQTT_PORT=8883` + TLS 設定追加 |
| `ai-monitor` | 同上 |
| `ota-manager` / `v002` / `v003` | 同上 |
| `dashboard` | 同上 |
| `fleet-simulator` | 同上 |
| `mosquitto` | conf を TLS 専用に変更 |
