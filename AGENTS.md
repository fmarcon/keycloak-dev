# AGENTS.md

## Visão geral

Laboratório de desenvolvimento de identidade e autenticação. Sobe Keycloak atrás de nginx com TLS, com persistência em PostgreSQL, user federation em um OpenLDAP de testes (dados fake do Futurama) e três clients OIDC em Flask para estudo do protocolo.

Objetivo: ambiente local reproduzível para estudar Keycloak (realm, LDAP federation, OIDC), não configuração de produção. Senhas e certificados são de dev.

Idioma do projeto: português do Brasil em README, comentários, docstrings e mensagens de commit.

## Stack

| Componente | Versão / imagem |
|---|---|
| Keycloak | `quay.io/keycloak/keycloak:26.0` |
| PostgreSQL | `postgres:16-alpine` |
| nginx | `nginx:1.27-alpine` |
| OpenLDAP | imagem local `keycloak-dev-openldap:latest` (Debian bookworm + slapd) |
| phpLDAPadmin | `osixia/phpldapadmin:latest` |
| Cliente Flask | Python >= 3.12, Flask >= 3.0, uv |
| Libs do cliente | requests, PyJWT, Authlib, python-keycloak, python-dotenv, cryptography |
| Lint | ruff >= 0.8 (`extend-select = ["I"]`) |

## Mapa de arquivos

| Caminho | Papel |
|---|---|
| `docker-compose.yml` | serviços postgres, keycloak, openldap, phpldapadmin, nginx; rede e volumes |
| `.env.example` | variáveis do stack (copiar para `.env`) |
| `.env` | valores locais reais, **não versionado** |
| `README.md` | documentação de uso do stack |
| `AGENTS.md` | este arquivo: contexto + memória do projeto |
| `nginx/default.conf.template` | reverse proxy HTTPS, redirect HTTP, `/health`, `/phpldapadmin`; processado por envsubst |
| `nginx/phpldapadmin-http.conf` | VirtualHost do Apache com `Alias /phpldapadmin` |
| `nginx/certs/generate-dev-cert.sh` | gera certificado self-signed de dev |
| `nginx/certs/fullchain.pem`, `privkey.pem` | certificado de dev, **não versionados** |
| `keycloak/realms/dev-realm.json` | realm `dev` + federation LDAP, importado no startup |
| `ldap/Dockerfile` | imagem OpenLDAP própria (baseada em rroemhild/docker-test-openldap, MIT) |
| `ldap/entrypoint.sh` | seed único controlado por marker + slapd em foreground |
| `ldap/bootstrap/config/*.ldif` | schema `msad`, overlay `memberof`, logging |
| `ldap/bootstrap/data/*.ldif` | usuários e grupos fake (Planet Express) |
| `flask-oidc-client/app.py` | client OIDC manual (requests + PyJWT) |
| `flask-oidc-client/oidc_client.py` | camada de serviço OIDC usada pelo `app.py` |
| `flask-oidc-client/config.py` | config compartilhada (`OidcConfig`, sessão HTTP, SSL, logout URL) |
| `flask-oidc-client/app_authlib.py` | mesmo client usando Authlib |
| `flask-oidc-client/app_python_keycloak.py` | mesmo client usando python-keycloak (config duplicada de propósito) |
| `flask-oidc-client/templates/page.html` | template único dos três apps |
| `flask-oidc-client/pyproject.toml`, `uv.lock` | dependências e lock do cliente |
| `flask-oidc-client/.env.example` | variáveis OIDC e `SECRET_KEY` |

Arquivos ignorados no git: `.env`, `flask-oidc-client/.env`, `.venv/`, `__pycache__/` e os dois PEM de `nginx/certs/`.

## Arquitetura e rede

```
browser --HTTPS--> nginx --HTTP--> keycloak:8080 --JDBC--> postgres:5432
                     |                  |
                     |                  +--LDAP--> openldap:10389
                     +--HTTP--> phpldapadmin:80 (subpath /phpldapadmin)
```

TLS termina no nginx. O tráfego nginx → Keycloak é HTTP simples; `KC_PROXY_HEADERS=xforwarded` garante issuer e redirects em `https`.

Rede bridge fixa `172.28.0.0/24`, gateway `172.28.0.1`:

| Serviço | IP | Porta no host | Volume |
|---|---|---|---|
| postgres | `172.28.0.10` | nenhuma | `keycloak_postgres_data` |
| keycloak | `172.28.0.20` | nenhuma (health em 9000 interno) | import read-only de `keycloak/realms` |
| nginx | `172.28.0.30` | `NGINX_HTTP_PORT` (80), `NGINX_HTTPS_PORT` (443) | `nginx/certs` |
| openldap | `172.28.0.40` | `LDAP_PORT` (10389), `LDAPS_PORT` (10636) | `keycloak_openldap_data`, `keycloak_openldap_config` |
| phpldapadmin | `172.28.0.50` | `PHPLDAPADMIN_PORT` (8081) | `keycloak_phpldapadmin_data` |

URL pública padrão: `https://auk.ucs.br` (`KC_HOSTNAME`). Variação local: `KC_HOSTNAME=https://localhost:8443`, `NGINX_HTTP_PORT=8080`, `NGINX_HTTPS_PORT=8443`.

Fluxo de login federado: usuário digita credenciais no Keycloak → Keycloak faz bind no LDAP → usuário e grupos são importados (`mode: IMPORT`) → tokens emitidos. Senha nunca é copiada para o banco do Keycloak.

## Comandos

```bash
# stack
cp .env.example .env                # editar senhas
./nginx/certs/generate-dev-cert.sh
docker compose up -d
docker compose logs -f keycloak
docker compose down                 # dados persistem
docker compose down -v              # apaga volumes (banco + LDAP + realm)

# re-seed do LDAP (entryUUIDs mudam; ver Armadilhas)
LDAP_RESEED=1 docker compose up -d openldap

# validação de config do compose
docker compose config -q

# teste de token (Authorization Code fica no Flask; aqui é password grant)
curl -k -X POST https://auk.ucs.br/realms/dev/protocol/openid-connect/token \
  -d "grant_type=password&client_id=admin-cli&username=fry&password=fry&scope=openid"

# client Flask (escolher UM app; todos usam a porta 5000)
cd flask-oidc-client
cp .env.example .env                # preencher OIDC_CLIENT_SECRET e SECRET_KEY
uv sync
uv run python app.py                # fluxo manual
uv run python app_authlib.py        # Authlib
uv run python app_python_keycloak.py  # python-keycloak
uv run ruff check
```

Client `flask-lab` precisa existir no Keycloak (realm `dev`, Confidential, redirect `http://localhost:5000/callback`, post logout `http://localhost:5000/*`).

## Credenciais de dev

LDAP: bind `cn=admin,dc=planetexpress,dc=com` / `GoodNewsEveryone`; base `ou=people,dc=planetexpress,dc=com`. Login = `uid`, senha = `uid`.

| Login | Nome | Grupos |
|---|---|---|
| `fry`, `leela`, `bender` | tripulação | `ship_crew` |
| `professor`, `hermes` | administração | `admin_staff` |
| `zoidberg`, `amy` | sem grupo | — |

Keycloak admin: `KC_ADMIN_USER` / `KC_ADMIN_PASSWORD` (default `admin`/`admin` no `.env.example`). phpLDAPadmin usa o mesmo bind admin do LDAP.

Tudo acima é fake e público no README. Nunca reutilizar em produção.

## Convenções

- Documentação, comentários, docstrings e commits em pt-BR; código e identificadores em inglês.
- Docstrings de módulo explicam o "porquê" e o fluxo (os apps Flask são material didático).
- Python: `from __future__ import annotations`, type hints, `pathlib` em vez de concatenação de path.
- Config por variável de ambiente com defaults alinhados ao stack; apenas `OIDC_CLIENT_SECRET` é obrigatória.
- Dependências via `uv` + `pyproject.toml`; `uv.lock` versionado.
- Containers e volumes com prefixo `keycloak_*`; rede `keycloak-net`; IPs fixos documentados aqui.
- Segredos e certificados nunca entram no git. Não editar `.env` nem PEM sem o usuário pedir.
- Mudança de comportamento em serviço exige atualização do `README.md` e deste arquivo.

## Armadilhas conhecidas

| Sintoma | Causa | Correção |
|---|---|---|
| issuer/redirect saem com `http` | falta de `KC_PROXY_HEADERS=xforwarded` | manter a variável; nginx envia `X-Forwarded-*` |
| login 401 depois de re-seed | `LDAP_RESEED=1` muda `entryUUID`; link de federation fica stale | apagar o realm `dev` no console e reiniciar o Keycloak, ou `docker compose down -v` |
| realm não atualiza após editar JSON | import só roda se o realm não existir | apagar realm `dev` e reiniciar; ou `down -v` |
| phpLDAPadmin com `Parse error` | `PHPLDAPADMIN_LDAP_HOSTS` sem prefixo `#PYTHON2BASH:` | manter o prefixo (convenção interna da imagem osixia) |
| phpLDAPadmin volta com config antiga | config gerado em volume anônimo | manter `keycloak_phpldapadmin_data` montado em `/var/www/phpldapadmin` |
| Flask falha TLS contra o Keycloak | cert self-signed | `OIDC_CA_BUNDLE=../nginx/certs/fullchain.pem` (nunca `verify=False`) |
| segundo app Flask não sobe | todos usam `localhost:5000` | rodar um por vez ou sobrescrever porta/redirect no `.env` |
| LDAP não sobe com domínio novo | DNs `dc=planetexpress,dc=com` hardcoded nos LDIFs e no bind | editar `ldap/bootstrap/data/*.ldif`, `Dockerfile` e `dev-realm.json` juntos |
| redirect HTTP errado em porta alta | nginx monta a URL com `NGINX_HTTPS_PORT` | manter a variável igual à porta publicada |

## Estado atual do lab

Três implementações do mesmo fluxo Authorization Code + client confidential:

| Etapa | `app.py` (manual) | `app_authlib.py` | `app_python_keycloak.py` |
|---|---|---|---|
| Descoberta | `OidcClient.discover()` + cache 10 min | `server_metadata_url` | `KeycloakOpenID` (implícita) |
| URL de auth | `build_authorization_url()` | `authorize_redirect()` | `kc.auth_url()` |
| Code → token | `exchange_code()` | `authorize_access_token()` | `kc.token()` |
| Validação ID token | JWKS + `iss`/`aud`/`exp`/`nonce` | interna da lib (inclui nonce) | `kc.decode_token(validate=True)`; nonce não conferido no app |
| userinfo | `GET` com Bearer | `userinfo()` | `kc.userinfo()` |
| Logout | `end_session_endpoint` + `id_token_hint` | `end_session_endpoint` direto | backchannel com refresh token |

Limitações conscientes: o payload do access token é apenas decodificado (sem verificar assinatura) no `/me` dos três apps; sessão é cookie assinado do Flask (~4 KB), não server-side; sem refresh automático no `app.py` nem no `app_authlib.py`.

## Memória do projeto

### Decisões

- **Entrypoint próprio no LDAP** (em vez do entrypoint da imagem original): permite persistência real em volumes nomeados, com seed executado uma única vez e re-seed explícito via `LDAP_RESEED`.
- **Realm versionado como JSON** e importado no startup: configuração reproduzível, sem cliques manuais no console; trade-off é que import só ocorre na criação do realm.
- **TLS somente no nginx**: simplifica Keycloak e mantém a rede interna em HTTP; exige `KC_PROXY_HEADERS` e `KC_HTTP_ENABLED`.
- **Três clients Flask em vez de um**: o objetivo é comparar implementação manual, Authlib e python-keycloak. Duplicação de config no app python-keycloak é intencional para manter a comparação legível.
- **Senha do LDAP = uid e dados fake**: reduz atrito didático; aceitável apenas porque o ambiente é local.

### Backlog / próximos passos

- Documentar `app_python_keycloak.py` no README.
- Resolver conflito de porta 5000 entre os três apps (porta própria por app).
- Adicionar refresh token ao `app.py` e ao `app_authlib.py`.
- Testar PKCE com client público.
- Expor roles/groups do Keycloak no token e exibir no `/me`.
- Avaliar sessão server-side (Redis) para tokens grandes.
- Adicionar healthcheck/verificação automatizada dos clients Flask.
- Limpar resíduos históricos de porta nos comentários do `.env.example` do cliente.

### Lacunas conhecidas

- README não tem seção para o client `python-keycloak`.
- README afirma que `app_authlib.py` roda em 5000 "para conviver com `app.py` (porta 5000)" — os apps não convivem na mesma porta.
- `flask-oidc-client/.env.example` repete "5000 p/ app.py, 5000 p/ app_authlib.py".
- Não há testes automatizados no stack nem nos clients.

## Regras para agentes

1. Ler este arquivo antes de propor mudanças.
2. Toda funcionalidade nova com trade-off: adicionar uma entrada em **Decisões** e manter **Backlog** e **Lacunas** atualizados.
3. Manter coerência entre `docker-compose.yml`, `dev-realm.json`, LDIFs e README ao mudar portas, DNs ou nomes de serviço.
4. Não commitar `.env`, PEM ou qualquer segredo; não imprimir conteúdo de `.env` em logs/respostas.
5. Validar com `docker compose config -q` após editar o compose e `uv run ruff check` após editar o cliente.
6. Preferir mudanças pequenas e alinhadas ao estilo existente; não reescrever o README inteiro para uma correção pontual.
7. Se o usuário pedir "atualizar a memória", editar apenas as seções **Decisões**, **Backlog** e **Lacunas**.
