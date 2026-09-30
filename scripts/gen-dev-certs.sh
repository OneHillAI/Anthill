#!/usr/bin/env bash
# Generate a throwaway dev CA + certificates for orchestrator and two nodes.
# NOT for production - keys are unencrypted and the CA is self-signed.
set -euo pipefail

OUT="${1:-certs}"
mkdir -p "$OUT"

# ── CA ─────────────────────────────────────────────────────────────────────────
openssl req -x509 -newkey rsa:2048 -days 3650 -nodes \
  -keyout "$OUT/ca.key" -out "$OUT/ca.crt" \
  -subj "/CN=anthill-dev-ca" 2>/dev/null

# ── helper: issue a cert signed by the dev CA ─────────────────────────────────
issue() {
  local name="$1"; shift
  local san="$1";  shift   # e.g. "DNS:orchestrator,IP:127.0.0.1"
  openssl req -newkey rsa:2048 -nodes \
    -keyout "$OUT/${name}.key" -out "$OUT/${name}.csr" \
    -subj "/CN=${name}" 2>/dev/null
  openssl x509 -req -in "$OUT/${name}.csr" \
    -CA "$OUT/ca.crt" -CAkey "$OUT/ca.key" -CAcreateserial \
    -days 365 -out "$OUT/${name}.crt" \
    -extfile <(printf "subjectAltName=%s\n" "$san") 2>/dev/null
  rm "$OUT/${name}.csr"
}

issue orchestrator "DNS:orchestrator,DNS:localhost,IP:127.0.0.1"
issue node-1       "DNS:node-1,DNS:localhost,IP:127.0.0.1"
issue node-2       "DNS:node-2,DNS:localhost,IP:127.0.0.1"

echo "Certs written to $OUT/"
ls "$OUT/"
