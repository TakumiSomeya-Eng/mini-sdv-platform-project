# M10 TRD — セキュリティ層（TLS/mTLS + 証明書管理）

## L5 Implementation Hypothesis

---

## 1. ファイル変更一覧

### 新規作成
```
scripts/
  generate-certs.sh        ← CA + サーバー + クライアント証明書生成

config/
  certs/                   ← .gitignore 対象（generate-certs.sh が生成）
    ca.crt / ca.key
    server.crt / server.key
    client.crt / client.key
```

### 変更
```
config/mosquitto/mosquitto.conf   ← TLS専用設定に書き換え
docker-compose.yml                ← 証明書ボリューム追加、MQTT_PORT=8883
services/mqtt-bridge/main.py      ← TLS ヘルパー追加
services/ai-monitor/main.py       ← 同上
services/ota-manager/main.py      ← 同上
services/dashboard/main.py        ← 同上
services/fleet-simulator/main.py  ← 同上
.gitignore                        ← config/certs/ 追加
```

---

## 2. generate-certs.sh の設計

```bash
CERTS_DIR="config/certs"

# ① CA（認証局）
openssl genrsa -out ca.key 4096
openssl req -new -x509 -days 3650 -key ca.key -out ca.crt \
  -subj "/CN=SDV-CA/O=mini-sdv-platform/C=JP"

# ② Mosquitto サーバー証明書（CN=localhost で WSL2 に対応）
openssl genrsa -out server.key 2048
openssl req -new -key server.key -out server.csr \
  -subj "/CN=localhost/O=mini-sdv-platform/C=JP"
openssl x509 -req -days 365 \
  -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
  -out server.crt

# ③ クライアント証明書（全 MQTT クライアント共通）
openssl genrsa -out client.key 2048
openssl req -new -key client.key -out client.csr \
  -subj "/CN=sdv-client/O=mini-sdv-platform/C=JP"
openssl x509 -req -days 365 \
  -in client.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
  -out client.crt

chmod 600 *.key   # 秘密鍵のパーミッションを制限
```

---

## 3. Mosquitto TLS 設定（mosquitto.conf 書き換え）

```conf
# M10: TLS/mTLS 専用設定
listener 8883
cafile  /mosquitto/certs/ca.crt
certfile /mosquitto/certs/server.crt
keyfile  /mosquitto/certs/server.key

# mTLS: クライアント証明書を必須にする
require_certificate true
allow_anonymous false
```

docker-compose の Mosquitto ボリュームに `./config/certs:/mosquitto/certs:ro` を追加。

---

## 4. Python MQTT クライアントの TLS ヘルパー

全 MQTT クライアントに同一パターンで追加:

```python
import ssl
import os

MQTT_PORT      = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_TLS       = os.environ.get("MQTT_TLS", "false").lower() == "true"
MQTT_CA_CERT   = os.environ.get("MQTT_CA_CERT", "/certs/ca.crt")
MQTT_CLIENT_CERT = os.environ.get("MQTT_CLIENT_CERT", "/certs/client.crt")
MQTT_CLIENT_KEY  = os.environ.get("MQTT_CLIENT_KEY", "/certs/client.key")

def apply_tls(client: mqtt_client.Client) -> None:
    if not MQTT_TLS:
        return
    client.tls_set(
        ca_certs=MQTT_CA_CERT,
        certfile=MQTT_CLIENT_CERT,
        keyfile=MQTT_CLIENT_KEY,
    )

# connect_mqtt() 内で client.connect() の前に呼ぶ:
def connect_mqtt() -> mqtt_client.Client:
    client = mqtt_client.Client(...)
    apply_tls(client)           # ← 追加
    client.connect(MQTT_HOST, MQTT_PORT)
    ...
```

---

## 5. docker-compose.yml の変更パターン

### Mosquitto
```yaml
mosquitto:
  volumes:
    - ./config/mosquitto/mosquitto.conf:/mosquitto/config/mosquitto.conf:ro
    - ./config/certs:/mosquitto/certs:ro    # ← 追加
```

### 各 MQTT クライアント（mqtt-bridge / ai-monitor / ota-manager × 3 / dashboard / fleet-simulator）
```yaml
  environment:
    MQTT_PORT: "8883"                        # ← 1883 から変更
    MQTT_TLS: "true"                         # ← 追加
    MQTT_CA_CERT: /certs/ca.crt             # ← 追加
    MQTT_CLIENT_CERT: /certs/client.crt     # ← 追加
    MQTT_CLIENT_KEY: /certs/client.key      # ← 追加
  volumes:
    - ./config/certs:/certs:ro              # ← 追加
```

---

## 6. .gitignore への追加

```
# TLS certificates (private keys must not be committed)
config/certs/
```

---

## 7. 実装ステップ（順序）

1. `scripts/generate-certs.sh` 作成
2. `.gitignore` に `config/certs/` 追加
3. `config/mosquitto/mosquitto.conf` を TLS 設定に書き換え
4. 全 MQTT クライアントの `main.py` に `apply_tls()` ヘルパー追加
5. `docker-compose.yml` の全 MQTT サービスを更新
6. WSL2: `bash scripts/generate-certs.sh`（証明書生成）
7. WSL2: `docker compose build && docker compose up -d`
8. 検証A: 証明書なし接続が拒否されること
9. 検証B: 証明書付き接続が成功し MQTT メッセージが流れること

---

## 8. 検証コマンド（WSL2）

```bash
# NG: 証明書なし → エラーになること
mosquitto_sub -h localhost -p 8883 -t "sdv/#" -v

# OK: CA + クライアント証明書付き → 接続成功
mosquitto_sub \
  --cafile config/certs/ca.crt \
  --cert   config/certs/client.crt \
  --key    config/certs/client.key \
  -h localhost -p 8883 -t "sdv/#" -v

# パケットが暗号化されているか確認（tcpdump）
sudo tcpdump -i lo -A port 8883 | head -30
# → バイナリが流れていれば暗号化 OK（平文シグナル値が見えないこと）
```
