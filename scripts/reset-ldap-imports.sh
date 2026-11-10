#!/usr/bin/env sh
# Limpa os imports de usuarios federados do realm 'dev' (LDAP).
#
# Use apos reiniciar/recriar o container openldap: a imagem re-seeda os dados
# a cada boot, gerando novos entryUUIDs. Usuarios ja importados no Keycloak
# ficam com federation link stale e o login falha (401 invalid credentials).
# Deletar o import local nao toca no LDAP; o proximo login reimporta.
#
# Requisitos: .env com KC_ADMIN_USER/KC_ADMIN_PASSWORD, python3 e curl.
set -eu

cd "$(dirname "$0")/.."

ADMIN_USER="${KC_ADMIN_USER:-$(grep '^KC_ADMIN_USER' .env | cut -d= -f2)}"
ADMIN_PASSWORD="${KC_ADMIN_PASSWORD:-$(grep '^KC_ADMIN_PASSWORD' .env | cut -d= -f2)}"
BASE_URL="${KC_URL:-$(grep '^KC_HOSTNAME' .env | cut -d= -f2)}"

TOKEN=$(curl -sk -X POST "$BASE_URL/realms/master/protocol/openid-connect/token" \
  -d "grant_type=password&client_id=admin-cli&username=$ADMIN_USER&password=$ADMIN_PASSWORD" |
  python3 -c "import json,sys;print(json.load(sys.stdin)['access_token'])")

python3 - "$TOKEN" "$BASE_URL" <<'EOF'
import json, subprocess, sys, urllib.request, ssl

token, base = sys.argv[1], sys.argv[2]
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

def call(req):
    req.add_header("Authorization", f"Bearer {token}")
    return urllib.request.urlopen(req, context=ctx)

users = json.load(call(urllib.request.Request(f"{base}/admin/realms/dev/users?max=500")))
federated = [u for u in users if u.get("federationLink")]
for u in federated:
    call(urllib.request.Request(f"{base}/admin/realms/dev/users/{u['id']}", method="DELETE"))
    print(f"deletado import stale: {u['username']} ({u['id']})")
if not federated:
    print("nenhum import federado para limpar")
EOF

echo "OK. Proximo login de cada usuario reimporta do LDAP."
