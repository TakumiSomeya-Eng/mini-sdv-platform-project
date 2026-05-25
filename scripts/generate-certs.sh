#!/bin/bash
# generate-certs.sh — M11: TLS/mTLS + service-specific client certificates
# Run once from project root in WSL2: bash scripts/generate-certs.sh
set -euo pipefail

CERTS_DIR="config/certs"
mkdir -p "$CERTS_DIR"

echo "=== [1/3] Generating CA (Certificate Authority) ==="
openssl genrsa -out "$CERTS_DIR/ca.key" 4096
openssl req -new -x509 -days 3650 \
  -key "$CERTS_DIR/ca.key" \
  -out "$CERTS_DIR/ca.crt" \
  -subj "/CN=SDV-CA/O=mini-sdv-platform/C=JP"

echo "=== [2/3] Generating Mosquitto server certificate (CN=localhost + SAN) ==="
openssl genrsa -out "$CERTS_DIR/server.key" 2048
openssl req -new \
  -key "$CERTS_DIR/server.key" \
  -out "$CERTS_DIR/server.csr" \
  -subj "/CN=localhost/O=mini-sdv-platform/C=JP"
# SAN required by OpenSSL 3.x — CN alone is no longer used for hostname verification
openssl x509 -req -days 365 \
  -in "$CERTS_DIR/server.csr" \
  -CA "$CERTS_DIR/ca.crt" \
  -CAkey "$CERTS_DIR/ca.key" \
  -CAcreateserial \
  -extfile <(printf "subjectAltName=DNS:localhost,IP:127.0.0.1") \
  -out "$CERTS_DIR/server.crt"

echo "=== [3/3] Generating service-specific client certificates (M11 ACL) ==="
# Each service gets its own certificate with a unique CN.
# CN becomes the MQTT username (use_identity_as_username true).
# The ACL file maps each CN to its permitted publish/subscribe topics.
for SERVICE in bridge fleet ai ota dashboard; do
  CN="sdv-${SERVICE}"
  echo "  Generating ${SERVICE}.crt (CN=${CN})..."
  openssl genrsa -out "$CERTS_DIR/${SERVICE}.key" 2048
  openssl req -new \
    -key "$CERTS_DIR/${SERVICE}.key" \
    -out "$CERTS_DIR/${SERVICE}.csr" \
    -subj "/CN=${CN}/O=mini-sdv-platform/C=JP"
  openssl x509 -req -days 365 \
    -in "$CERTS_DIR/${SERVICE}.csr" \
    -CA "$CERTS_DIR/ca.crt" \
    -CAkey "$CERTS_DIR/ca.key" \
    -CAcreateserial \
    -out "$CERTS_DIR/${SERVICE}.crt"
  rm "$CERTS_DIR/${SERVICE}.csr"
done

# Clean up serial and CSR files
rm -f "$CERTS_DIR"/*.csr "$CERTS_DIR"/*.srl

# Restrict private key permissions
chmod 600 "$CERTS_DIR"/*.key
chmod 644 "$CERTS_DIR"/*.crt

echo ""
echo "=== Certificates generated in $CERTS_DIR/ ==="
ls -la "$CERTS_DIR/"
echo ""
echo "Next: docker compose build && docker compose up -d"
