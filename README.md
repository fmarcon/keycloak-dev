# Keycloak + PostgreSQL + Nginx (HTTPS)

Stack: Keycloak 26 atras de nginx com TLS, persistencia em PostgreSQL 16.

Imagens pinadas: `quay.io/keycloak/keycloak:26.0`, `postgres:16-alpine`, `nginx:1.27-alpine`.

## Arquivos

| Arquivo | Funcao |
|---|---|
| `docker-compose.yml` | servicos postgres, keycloak, nginx |
| `nginx/default.conf.template` | reverse proxy HTTPS + redirect HTTP, processado por envsubst |
| `nginx/certs/` | certificados (`fullchain.pem`, `privkey.pem`) |
| `nginx/certs/generate-dev-cert.sh` | gera certificado self-signed de dev |
| `.env.example` | variaveis de ambiente |

## Uso

Copie e edite o `.env`:

```bash
cp .env.example .env
# edite senhas fortes (POSTGRES_PASSWORD, KC_ADMIN_PASSWORD)
```

O hostname padrao e `neo.ucs.br`; aponte-o no `/etc/hosts` do host:

```bash
echo "127.0.1.1 neo.ucs.br" | sudo tee -a /etc/hosts
```

Gere o certificado self-signed de dev e suba:

```bash
./nginx/certs/generate-dev-cert.sh
docker compose up -d
docker compose logs -f keycloak
```

Acesse `https://neo.ucs.br` (porta 443 por padrao). Certificado self-signed gera aviso no browser (aceite em dev). Login admin: `KC_ADMIN_USER`/`KC_ADMIN_PASSWORD` do `.env` (`admin`/`admin` por padrao, troque).

`http://neo.ucs.br` (porta 80) redireciona 301 para HTTPS.

### Variacao dev em localhost com portas altas

Sem `/etc/hosts` e sem portas privilegiadas, ajuste o `.env`:

```dotenv
KC_HOSTNAME=https://localhost:8443
NGINX_HTTP_PORT=8080
NGINX_HTTPS_PORT=8443
```

Acesse `https://localhost:8443` (o redirect HTTP chega em `http://localhost:8080`). O certificado self-signed ja cobre `localhost` e `127.0.0.1` (SAN).

## Como o TLS funciona

- TLS termina no nginx. Nginx fala HTTP simples com Keycloak na rede interna.
- `KC_HOSTNAME` deve ser a URL publica HTTPS vista pelo browser (`https://neo.ucs.br` por padrao; `https://localhost:8443` na variacao dev).
- `KC_PROXY_HEADERS=xforwarded` faz o Keycloak confiar em `X-Forwarded-Proto`/`Host`/`For`. Sem isso, issuer e redirects saem como `http`.
- `KC_HTTP_ENABLED=true` e necessario porque o trafego interno e HTTP.

## Certificado

Dev (self-signed):

```bash
./nginx/certs/generate-dev-cert.sh
FORCE=1 ./nginx/certs/generate-dev-cert.sh           # regenerar
CN=outro-host.local DAYS=365 ./nginx/certs/generate-dev-cert.sh   # CN e validade custom (padrao: CN=neo.ucs.br, 825 dias)
```

Producao: substitua `nginx/certs/fullchain.pem` e `nginx/certs/privkey.pem` por certificado real (Let's Encrypt, CA interna). Monte os arquivos no mesmo path. Para Let's Encrypt, o desafio HTTP-01 funciona na porta 80 (nginx ja responde nela).

Depois de trocar certificado: `docker compose exec nginx nginx -s reload`.

## Detalhes importantes

- `KC_HOSTNAME_STRICT=false` permite acesso por outros hosts (dev). Em producao com dominio fixo, mude para `true`.
- Portas no host: `NGINX_HTTP_PORT` (80 interno) e `NGINX_HTTPS_PORT` (443 interno).
- Postgres nao expoe porta no host; so a rede interna `keycloak-net`.
- Rede interna fixa em `172.28.0.0/24` (gateway `172.28.0.1`): postgres `172.28.0.10`, keycloak `172.28.0.20`, nginx `172.28.0.30`.
- Dados persistem no volume `postgres_data`.
- Healthcheck do Keycloak usa a porta de management (9000) com `KC_HEALTH_ENABLED=true`. O nginx tambem expoe `/health` publicamente (proxy para a porta 9000). Metricas habilitadas com `KC_METRICS_ENABLED=true`.
- O redirect HTTP usa `NGINX_HTTPS_PORT` para montar a URL; com porta 443 o sufixo e omitido.
- HSTS esta comentado no template; habilite so com certificado valido e dominio fixo.

## Limpeza

```bash
docker compose down          # para os containers
docker compose down -v       # para e apaga o volume do banco
```
