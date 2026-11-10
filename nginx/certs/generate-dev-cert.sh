#!/usr/bin/env sh
# Gera certificado self-signed para desenvolvimento local.
# Em producao use certificado real (Let's Encrypt, CA interna, etc.).
set -eu

DIR="$(cd "$(dirname "$0")" && pwd)"
DAYS="${DAYS:-825}"
CN="${CN:-auk.ucs.br}"

if [ -f "$DIR/fullchain.pem" ] && [ "${FORCE:-0}" != "1" ]; then
  echo "Certificado ja existe: $DIR/fullchain.pem"
  echo "Use FORCE=1 para regenerar."
  exit 0
fi

openssl req -x509 -nodes -newkey rsa:2048 \
  -days "$DAYS" \
  -keyout "$DIR/privkey.pem" \
  -out "$DIR/fullchain.pem" \
  -subj "/C=BR/ST=SP/L=Sao Paulo/O=Dev/CN=${CN}" \
  -addext "subjectAltName=DNS:${CN},DNS:localhost,DNS:keycloak.local,IP:127.0.0.1"

chmod 600 "$DIR/privkey.pem"
echo "Gerado: $DIR/fullchain.pem e $DIR/privkey.pem"
