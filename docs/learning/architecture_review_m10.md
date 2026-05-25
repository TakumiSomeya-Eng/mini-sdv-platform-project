# M10 アーキテクチャレビュー & 学習ガイド
## セキュリティ層（TLS/mTLS + 証明書管理）

---

## 1. このマイルストーンで学んだこと（3行サマリー）

M10では、MQTTブローカー（Mosquitto）とすべてのクライアントサービス間の通信をTLS/mTLSで完全暗号化した。ポート1883（平文）を廃止し、ポート8883（TLS専用）に移行することで、通信の機密性と接続元認証（mTLS）を同時に実現した。自己署名CA・サーバー証明書・クライアント証明書の生成から、Python paho-mqttへのTLS設定適用、Docker Composeによる証明書配布まで、SDVセキュリティの基礎を一貫して実装した。

---

## 2. なぜセキュリティ層が必要か（SDV文脈）

```
[問題] M2〜M9のMQTT通信はポート1883（平文）
         → tcpdumpで Speed=87.3 のような平文シグナル値が丸見え
         → 誰でも接続可能（匿名アクセス）

[背景] 実車のV2C（Vehicle-to-Cloud）では:
  • 車両テレメトリ = 走行パターン + バッテリー状態 + 位置情報
  • UN ECE R155（自動車サイバーセキュリティ規則）で通信暗号化が義務
  • AWS IoT Core / Azure IoT Hub は port 8883（TLS必須）

[解決] TLS/mTLS で:
  ① 暗号化: 傍受しても解読不能（機密性）
  ② 認証:   証明書を持つクライアントのみ接続可能（mTLS = 相互認証）
```

---

## 3. PKI（公開鍵基盤）の仕組み

### 3.1 証明書の階層構造

```
CA（認証局）
├── ca.key   ← CA秘密鍵（絶対に外部に出してはいけない）
└── ca.crt   ← CA証明書（全クライアントが信頼する公開証明書）
    │
    ├── server.crt  ← CAが署名したサーバー証明書（Mosquitto用）
    │   └── server.key
    │
    └── client.crt  ← CAが署名したクライアント証明書（全MQTTクライアント共通）
        └── client.key
```

### 3.2 TLS ハンドシェイクの流れ

```
Client                          Server (Mosquitto)
  │                                   │
  │──── ClientHello ─────────────────▶│
  │◀─── ServerHello + server.crt ─────│  ← クライアントがサーバーを認証
  │     (CA署名を検証 → 信頼できる？)
  │──── client.crt ──────────────────▶│  ← サーバーがクライアントを認証 (mTLS)
  │     (CA署名を検証 → 許可する？)
  │◀─── 暗号化セッション確立 ─────────│
  │                                   │
  ▼ 以降はすべて暗号化されたMQTT通信   ▼
```

### 3.3 TLS vs mTLS の違い

| 項目 | TLS | mTLS |
|------|-----|------|
| 暗号化 | ✅ | ✅ |
| サーバー認証 | ✅ | ✅ |
| クライアント認証 | ❌ | ✅ |
| 設定 | `cafile` のみ | `require_certificate true` |
| 用途 | HTTPS（ブラウザ） | IoT / V2C（機器認証） |

---

## 4. OpenSSL コマンドの意味

```bash
# ① CA生成
openssl genrsa -out ca.key 4096          # RSA秘密鍵（4096bit）生成
openssl req -new -x509 -days 3650 \      # 自己署名証明書（CA自身）
  -key ca.key -out ca.crt \
  -subj "/CN=SDV-CA/O=mini-sdv-platform/C=JP"
#  CN=Common Name（識別名）, O=Organization, C=Country

# ② サーバー証明書
openssl genrsa -out server.key 2048
openssl req -new -key server.key -out server.csr   # CSR(証明書署名要求)生成
openssl x509 -req -days 365 \
  -in server.csr -CA ca.crt -CAkey ca.key \        # CAで署名
  -extfile <(printf "subjectAltName=DNS:localhost,IP:127.0.0.1") \  # SAN追加
  -out server.crt
# SAN(Subject Alternative Name): OpenSSL 3.x では CN ではなく SAN でホスト名検証
```

### M10で踏んだトラブル: CN vs SAN

```
[エラー] certificate verify failed (OpenSSL 3.x)
[原因]   CNのみ設定 → OpenSSL 3.x はCNによるホスト名検証を廃止
[修正]   -extfile で subjectAltName=DNS:localhost,IP:127.0.0.1 を追加

# 検証
openssl verify -CAfile ca.crt server.crt  # → OK ならCA署名は正しい
```

---

## 5. Mosquitto TLS設定の意味

```conf
listener 8883            # ← TLS専用ポート（平文1883は廃止）
cafile  /mosquitto/certs/ca.crt     # CA証明書（クライアント証明書の検証用）
certfile /mosquitto/certs/server.crt  # サーバー証明書
keyfile  /mosquitto/certs/server.key  # サーバー秘密鍵

require_certificate true           # mTLS: クライアント証明書必須
allow_anonymous false              # ユーザー名なし接続を拒否
use_identity_as_username true      # クライアント証明書のCNをMQTTユーザー名として使用
                                   # ↑ allow_anonymous false と mTLS の両立に必要
```

### `use_identity_as_username true` が必要な理由

```
[allow_anonymous false] = MQTTユーザー名が必要
[require_certificate true] = 証明書必須

→ 証明書を提示してもMQTTユーザー名がないと "not authorised" エラー
→ use_identity_as_username true = 証明書のCN("sdv-client")をユーザー名として扱う
→ 解決: 証明書 = 認証 + 識別 の両方を担う
```

---

## 6. Python paho-mqtt TLS設定パターン

```python
MQTT_TLS         = os.environ.get("MQTT_TLS", "false").lower() == "true"
MQTT_CA_CERT     = os.environ.get("MQTT_CA_CERT", "/certs/ca.crt")
MQTT_CLIENT_CERT = os.environ.get("MQTT_CLIENT_CERT", "/certs/client.crt")
MQTT_CLIENT_KEY  = os.environ.get("MQTT_CLIENT_KEY", "/certs/client.key")

def apply_tls(client: mqtt_client.Client) -> None:
    if not MQTT_TLS:
        return                  # ← MQTT_TLS=false なら何もしない（開発環境互換）
    client.tls_set(
        ca_certs=MQTT_CA_CERT,    # サーバー証明書の検証用CA
        certfile=MQTT_CLIENT_CERT, # クライアント証明書（mTLS）
        keyfile=MQTT_CLIENT_KEY,   # クライアント秘密鍵
    )

# connect() の前に必ず呼ぶ
def connect_mqtt():
    client = mqtt_client.Client(...)
    apply_tls(client)    # ← TLS設定をここで適用
    client.connect(MQTT_HOST, MQTT_PORT)
```

### 12 Factor App パターン（環境変数で制御）

```
開発環境: MQTT_TLS=false, MQTT_PORT=1883  （証明書なしで動作）
本番環境: MQTT_TLS=true,  MQTT_PORT=8883  （mTLS強制）

→ コード変更なし、env varだけで切り替え
```

---

## 7. Docker Composeでの証明書配布

```yaml
mosquitto:
  volumes:
    - ./config/certs:/mosquitto/certs:ro  # ← サーバー証明書を読み込み

mqtt-bridge:
  environment:
    MQTT_PORT: "8883"
    MQTT_TLS: "true"
    MQTT_CA_CERT: /certs/ca.crt
    MQTT_CLIENT_CERT: /certs/client.crt
    MQTT_CLIENT_KEY: /certs/client.key
  volumes:
    - ./config/certs:/certs:ro            # ← クライアント証明書を読み込み
```

### セキュリティ設計

```
config/certs/  → .gitignore で Git管理外
                  秘密鍵（*.key）がリポジトリに入らないよう保護
                  → UN ECE R155 の基本要件を満たす

:ro            → コンテナからは読み取り専用マウント
                  コンテナが侵害されても証明書を書き換えられない
```

---

## 8. 検証結果

```bash
# 検証A: 証明書なし → Protocol error（TLS接続を拒否）
mosquitto_sub -h localhost -p 8883 -t "sdv/#" -v
# → Error: Protocol error  ✅ 正しく拒否

# 検証B: 証明書付き → 全車両のMQTTメッセージが流れる
mosquitto_sub --cafile config/certs/ca.crt \
  --cert config/certs/client.crt --key config/certs/client.key \
  -h localhost -p 8883 -t "sdv/#" -v
# → sdv/vehicle-001/Vehicle/Speed {"value": 87.3, ...}  ✅ 暗号化通信成功
# → sdv/vehicle-002/telemetry {"Speed": 63.4, ...}      ✅ 全車両流れる
```

---

## 9. 本番SDVプラットフォームとの差分

| 項目 | M10（開発） | 本番SDV |
|------|------------|---------|
| CA | 自己署名CA | OEM PKI / 車載HSM |
| クライアント証明書 | 全車両共通 | 車両ごとに一意（VINベース） |
| 証明書配布 | bind mount | Provisioning Service（AWS IoT, Azure DPS） |
| 証明書更新 | 手動再生成 | SCEP / EST プロトコルで自動更新 |
| 秘密鍵保護 | ファイル（chmod 600） | HSM（Hardware Security Module） |
| ポート | 8883 (Mosquitto) | 8883 (AWS IoT Core / Azure IoT Hub) |

---

## 10. 次のステップ候補

| 候補 | 内容 | 技術 |
|------|------|------|
| A. Role-Based Access Control | ユーザー/ACLファイルでトピック単位の読み書き制御 | Mosquitto ACL |
| B. JWT/OAuth2 認証 | 証明書の代わりにトークン認証 | Mosquitto JWT plugin |
| C. Kubernetes移行 | Docker Compose → K8s（cert-manager で証明書自動管理） | Helm, cert-manager |
| D. CAN セキュリティ | SecOC（Secure Onboard Communication）シミュレーション | AUTOSAR SecOC |
| E. 侵入検知（IDS） | 異常なMQTTパターンの検出 | eBPF / Snort |
