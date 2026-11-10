# Keycloak + PostgreSQL + Nginx (HTTPS) + LDAP dev

Stack: Keycloak 26 atras de nginx com TLS, persistencia em PostgreSQL 16, user federation LDAP (OpenLDAP de testes) no realm `dev`.

Imagens: `quay.io/keycloak/keycloak:26.0`, `postgres:16-alpine`, `nginx:1.27-alpine`, `ghcr.io/rroemhild/docker-test-openldap:master`, `osixia/phpldapadmin:latest`.

## Arquivos

| Arquivo | Funcao |
|---|---|
| `docker-compose.yml` | servicos postgres, keycloak, openldap, phpldapadmin, nginx |
| `nginx/default.conf.template` | reverse proxy HTTPS + redirect HTTP, processado por envsubst |
| `nginx/certs/` | certificados (`fullchain.pem`, `privkey.pem`) |
| `nginx/certs/generate-dev-cert.sh` | gera certificado self-signed de dev |
| `keycloak/realms/dev-realm.json` | realm `dev` com user federation LDAP (import no startup) |
| `scripts/reset-ldap-imports.sh` | limpa imports LDAP stale apos restart do openldap |
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

No primeiro boot o Keycloak importa o realm `dev` com federation LDAP (veja secao abaixo): usuarios do LDAP logam com senha = uid (`fry`, `leela`, ...), sem cadastro no Keycloak.

`http://neo.ucs.br` (porta 80) redireciona 301 para HTTPS.

### Variacao dev em localhost com portas altas

Sem `/etc/hosts` e sem portas privilegiadas, ajuste o `.env`:

```dotenv
KC_HOSTNAME=https://localhost:8443
NGINX_HTTP_PORT=8080
NGINX_HTTPS_PORT=8443
```

Acesse `https://localhost:8443` (o redirect HTTP chega em `http://localhost:8080`). O certificado self-signed ja cobre `localhost` e `127.0.0.1` (SAN). Nesta variacao, as URLs das secoes abaixo trocam `neo.ucs.br` por `localhost:8443` (ex.: o teste de login LDAP).

## Como o TLS funciona

- TLS termina no nginx. Nginx fala HTTP simples com Keycloak na rede interna.
- `KC_HOSTNAME` deve ser a URL publica HTTPS vista pelo browser (`https://neo.ucs.br` por padrao; `https://localhost:8443` na variacao dev).
- `KC_PROXY_HEADERS=xforwarded` faz o Keycloak confiar em `X-Forwarded-Proto`/`Host`/`For`. Sem isso, issuer e redirects saem como `http`.
- `KC_HTTP_ENABLED=true` e necessario porque o trafego interno e HTTP.

## User federation LDAP (dev)

O compose sobe um OpenLDAP de testes (`ghcr.io/rroemhild/docker-test-openldap`) com dados fake da Planet Express (Futurama), junto com phpLDAPadmin para navegar/editar.

- Realm `dev` importado automaticamente no startup do Keycloak (`--import-realm` + `keycloak/realms/dev-realm.json`) ja com o federation provider LDAP configurado.
- LDAP interno em `openldap:10389` (sem porta no host). Bind: `cn=admin,dc=planetexpress,dc=com` / `GoodNewsEveryone`.
- phpLDAPadmin em `http://localhost:${PHPLDAPADMIN_PORT}` (8081 por padrao); mesmo login admin do LDAP.
- Mapeamento: `uid` -> username, `givenName`/`sn`/`mail` -> firstName/lastName/email, RDN `cn`, UUID `entryUUID`, grupos via `memberOf` (mode IMPORT: grupos LDAP viram grupos do Keycloak no primeiro login).
- `editMode` READ_ONLY: senha valida via bind no LDAP; perfis nao sao editaveis pelo Keycloak.

### Dados do LDAP (dev)

Bind admin: DN `cn=admin,dc=planetexpress,dc=com`, senha `GoodNewsEveryone`. Base dos usuarios: `ou=people,dc=planetexpress,dc=com`.

Usuarios (login = `uid`, senha = uid):

| Login (uid) | Senha | Nome (cn) | Email | Grupos |
|---|---|---|---|---|
| `fry` | `fry` | Philip J. Fry | fry@planetexpress.com | `ship_crew` |
| `leela` | `leela` | Turanga Leela | leela@planetexpress.com | `ship_crew` |
| `bender` | `bender` | Bender Bending Rodriguez | bender@planetexpress.com | `ship_crew` |
| `professor` | `professor` | Hubert J. Farnsworth | professor@planetexpress.com | `admin_staff` |
| `hermes` | `hermes` | Hermes Conrad | hermes@planetexpress.com | `admin_staff` |
| `zoidberg` | `zoidberg` | John A. Zoidberg | zoidberg@planetexpress.com | — |
| `amy` | `amy` | Amy Wong (sn: Kroker) | amy@planetexpress.com | — |

Grupos (`objectClass: Group`, na mesma OU dos usuarios, membros por DN em `member`, overlay `memberOf` ativo):

| Grupo | Membros |
|---|---|
| `ship_crew` | fry, leela, bender |
| `admin_staff` | professor, hermes |

Notas:

- DN do `amy` e multi-valorado (`cn=Amy Wong+sn=Kroker`), peculiaridade do RDN; login/email funcionam normal.
- `professor` tem dois mails no LDAP (`professor@` e `hubert@`); o mapper importa o primeiro.
- Senhas em texto no LDAP (dev fake); nunca use em producao.

Teste rapido (token com senha do LDAP):

```bash
curl -k -X POST https://neo.ucs.br/realms/dev/protocol/openid-connect/token \
  -d "grant_type=password&client_id=admin-cli&username=fry&password=fry&scope=openid"
```

### Caveats do LDAP de dev

- A imagem e stateless: a cada boot o slapd e reconfigurado e os LDIFs re-seedados. Edicoes feitas no phpLDAPadmin sao perdidas em qualquer restart do container.
- O re-seed gera novos `entryUUID`s; usuarios ja importados no Keycloak ficam com federation link stale e o login falha (401). Rode `./scripts/reset-ldap-imports.sh` para limpar os imports locais (nao toca no LDAP); o proximo login reimporta.
- Import do realm so roda se o realm `dev` nao existir. Para reimportar do zero: apague o realm no console admin e reinicie o keycloak (ou `docker compose down -v`, que apaga tudo).
- `docker compose logs -f openldap` mostra o re-seed acontecendo em cada boot.

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
- Rede interna fixa em `172.28.0.0/24` (gateway `172.28.0.1`): postgres `172.28.0.10`, keycloak `172.28.0.20`, nginx `172.28.0.30`, openldap `172.28.0.40`, phpldapadmin `172.28.0.50`.
- Dados persistem no volume `postgres_data`.
- Healthcheck do Keycloak usa a porta de management (9000) com `KC_HEALTH_ENABLED=true`. O nginx tambem expoe `/health` publicamente (proxy para a porta 9000). Metricas habilitadas com `KC_METRICS_ENABLED=true`.
- O redirect HTTP usa `NGINX_HTTPS_PORT` para montar a URL; com porta 443 o sufixo e omitido.
- HSTS esta comentado no template; habilite so com certificado valido e dominio fixo.

## Limpeza

```bash
docker compose down          # para os containers
docker compose down -v       # para e apaga o volume do banco (realm, usuarios importados)
```

O LDAP nao tem volume: apos `down`+`up` ele re-seeda sozinho, mas os `entryUUID`s mudam e imports do Keycloak ficam stale — rode `./scripts/reset-ldap-imports.sh` apos religar.
