# Laboratório Keycloak: PostgreSQL + Nginx (HTTPS) + LDAP

Este é um ambiente local e reproduzível para estudar Keycloak, federação de usuários via LDAP e OpenID Connect. A proposta **não** é ser uma configuração de produção: tudo aqui foi simplificado para você experimentar, quebrar e recompor sem medo.

A stack sobe com Docker Compose. O Keycloak fica atrás de um nginx com TLS, guarda seus dados em PostgreSQL e federa os usuários de um OpenLDAP de testes (com dados fictícios do Futurama). Para estudar o protocolo, há também três clients Flask em Python que implementam o fluxo *Authorization Code* de formas diferentes.

> **Aviso:** senhas, certificados e dados deste laboratório são públicos e fictícios. Não os reutilize em produção.

## Sumário

- [Visão geral](#visão-geral)
- [Estrutura do projeto](#estrutura-do-projeto)
- [Primeiros passos](#primeiros-passos)
- [Como o TLS funciona](#como-o-tls-funciona)
- [Federação LDAP no realm `dev`](#federação-ldap-no-realm-dev)
- [Laboratório Flask + OpenID Connect](#laboratório-flask--openid-connect)
  - [Acessando pelo nginx](#acessando-pelo-nginx)
- [Certificados](#certificados)
- [Detalhes importantes](#detalhes-importantes)
- [Problemas comuns](#problemas-comuns)
- [Limpeza](#limpeza)

## Visão geral

```
browser --HTTPS--> nginx --HTTP--> keycloak:8080 --JDBC--> postgres:5432
                     |                  |
                     |                  +--LDAP--> openldap:10389
                     |
                     +--HTTP--> phpldapadmin:80 (subpath /phpldapadmin)
                     +--/flask/--> host.docker.internal:5000 (app.py no host)
```

O TLS termina no nginx; o tráfego entre nginx e Keycloak é HTTP simples na rede interna. O realm `dev` é importado automaticamente na primeira inicialização e já vem com o provedor de federação LDAP configurado.

### Imagens utilizadas

| Componente | Imagem / versão |
|---|---|
| Keycloak | `quay.io/keycloak/keycloak:26.0` |
| PostgreSQL | `postgres:16-alpine` |
| nginx | `nginx:1.27-alpine` |
| OpenLDAP | `keycloak-dev-openldap:latest` (imagem local em `ldap/`, baseada no [rroemhild/docker-test-openldap](https://github.com/rroemhild/docker-test-openldap), MIT) |
| phpLDAPadmin | `osixia/phpldapadmin:latest` |

### Serviços e portas

| Serviço | IP interno | Porta no host | Função |
|---|---|---|---|
| postgres | `172.28.0.10` | — | banco do Keycloak |
| keycloak | `172.28.0.20` | — | health interno na porta 9000 |
| nginx | `172.28.0.30` | `NGINX_HTTP_PORT` (80) e `NGINX_HTTPS_PORT` (443) | proxy HTTPS |
| openldap | `172.28.0.40` | `LDAP_PORT` (10389) e `LDAPS_PORT` (10636) | diretório de testes |
| phpldapadmin | `172.28.0.50` | `PHPLDAPADMIN_PORT` (8081) | interface web para o LDAP |

### Pré-requisitos

- Docker e Docker Compose.
- Para os clients Flask: Python >= 3.12 e [uv](https://docs.astral.sh/uv/).

## Estrutura do projeto

| Arquivo | Função |
|---|---|
| `docker-compose.yml` | define os serviços postgres, keycloak, openldap, phpldapadmin e nginx |
| `.env.example` | variáveis de ambiente do stack (copie para `.env`) |
| `nginx/default.conf.template` | reverse proxy HTTPS, redirecionamento HTTP, `/health`, `/phpldapadmin/` e `/flask/`, processado por `envsubst` |
| `nginx/certs/` | certificados `fullchain.pem` e `privkey.pem` |
| `nginx/certs/generate-dev-cert.sh` | gera o certificado self-signed de dev |
| `nginx/phpldapadmin-http.conf` | VirtualHost do Apache com `Alias /phpldapadmin` (subpath) |
| `keycloak/realms/dev-realm.json` | realm `dev` com federação LDAP e client `flask-lab` (importado no startup) |
| `ldap/` | imagem Docker do OpenLDAP de dev (Dockerfile, entrypoint com seed único e LDIFs) |
| `flask-oidc-client/` | os três clients OIDC de exemplo |

## Primeiros passos

Copie o arquivo de exemplo e ajuste as senhas:

```bash
cp .env.example .env
# edite o .env com senhas fortes (POSTGRES_PASSWORD, KC_ADMIN_PASSWORD)
```

O hostname padrão é `auk.ucs.br`. Aponte-o no `/etc/hosts` da sua máquina:

```bash
echo "127.0.1.1 auk.ucs.br" | sudo tee -a /etc/hosts
```

Gere o certificado self-signed e suba o stack:

```bash
./nginx/certs/generate-dev-cert.sh
docker compose up -d
docker compose logs -f keycloak
```

Agora é só acessar **https://auk.ucs.br** (porta 443). O certificado é self-signed, então o navegador vai avisar — aceite o risco, já que é um ambiente de dev. O login da console admin usa `KC_ADMIN_USER` / `KC_ADMIN_PASSWORD` do `.env` (`admin` / `admin` por padrão; troque!).

No primeiro boot, o Keycloak importa o realm `dev` já com a federação LDAP: os usuários do LDAP entram com senha igual ao `uid` (`fry`, `leela`, ...), sem precisar de cadastro no Keycloak. O endereço `http://auk.ucs.br` (porta 80) redireciona com 301 para HTTPS.

### Variação: localhost com portas altas

Se você não quer mexer no `/etc/hosts` nem usar portas privilegiadas, ajuste o `.env`:

```dotenv
KC_HOSTNAME=https://localhost:8443
NGINX_HTTP_PORT=8080
NGINX_HTTPS_PORT=8443
```

Acesse **https://localhost:8443** (o redirecionamento HTTP chega em `http://localhost:8080`). O certificado gerado já cobre `localhost` e `127.0.0.1` via SAN. Nas seções abaixo, troque `auk.ucs.br` por `localhost:8443` nas URLs (por exemplo, no teste de token LDAP).

## Como o TLS funciona

- O TLS termina no nginx; dentro da rede, nginx fala HTTP simples com o Keycloak.
- `KC_HOSTNAME` deve ser a URL HTTPS pública vista pelo navegador (`https://auk.ucs.br` por padrão, `https://localhost:8443` na variação dev).
- `KC_PROXY_HEADERS=xforwarded` faz o Keycloak confiar em `X-Forwarded-Proto`, `X-Forwarded-Host` e `X-Forwarded-For`. Sem isso, o issuer e os redirects saem com `http`.
- `KC_HTTP_ENABLED=true` é necessário porque o tráfego interno é HTTP.

## Federação LDAP no realm `dev`

O Compose sobe um OpenLDAP de dev (imagem construída a partir de `ldap/`, com os LDIFs fictícios da Planet Express herdados do projeto rroemhild, MIT) junto com o phpLDAPadmin para navegar e editar os dados.

- O realm `dev` é importado automaticamente no startup (`--import-realm` + `keycloak/realms/dev-realm.json`) já com o provedor LDAP configurado.
- O LDAP responde em `openldap:10389` na rede interna e também nas portas `10389`/`10636` do host. Bind: `cn=admin,dc=planetexpress,dc=com` / `GoodNewsEveryone`.
- phpLDAPadmin (mesmo login admin do LDAP):
  - Pelo nginx (recomendado): `https://auk.ucs.br/phpldapadmin` (ou `https://localhost/phpldapadmin`).
  - Acesso direto de dev: `http://localhost:${PHPLDAPADMIN_PORT}` (8081 por padrão).
- O phpLDAPadmin roda no subpath `/phpldapadmin`: o nginx faz proxy pass-through (`location /phpldapadmin/` → `phpldapadmin:80`, sem reescrever a URI) e o Apache do container serve nesse caminho via `Alias`, definido em `nginx/phpldapadmin-http.conf`. Como a aplicação monta os links a partir de `SCRIPT_NAME`, o prefixo chega intacto.
- `PHPLDAPADMIN_LDAP_HOSTS` no Compose precisa do prefixo `#PYTHON2BASH:` (convenção interna da imagem osixia) para que o valor `[{'openldap': [...]}]` seja interpretado como estrutura e vire PHP válido no `config.php`. Sem o prefixo, a string fica literal e o site quebra com `Parse error`.
- O `config.php` gerado persiste no volume nomeado `keycloak_phpldapadmin_data` (montado em `/var/www/phpldapadmin`); sem ele, a imagem cria um volume anônimo e uma configuração antiga sobrevive à recriação do container.
- Mapeamento de atributos: `uid` → username, `givenName`/`sn`/`mail` → firstName/lastName/email, RDN `cn`, UUID `entryUUID` e grupos via `memberOf`. No modo `IMPORT`, os grupos LDAP viram grupos do Keycloak no primeiro login.
- `editMode` é `READ_ONLY`: a senha é validada por bind no LDAP e os perfis não podem ser editados pelo Keycloak.

### Usuários e grupos de teste

Bind admin: DN `cn=admin,dc=planetexpress,dc=com`, senha `GoodNewsEveryone`. Base dos usuários: `ou=people,dc=planetexpress,dc=com`.

Login = `uid`, senha = `uid`:

| Login (`uid`) | Senha | Nome (`cn`) | Email | Grupos |
|---|---|---|---|---|
| `fry` | `fry` | Philip J. Fry | fry@planetexpress.com | `ship_crew` |
| `leela` | `leela` | Turanga Leela | leela@planetexpress.com | `ship_crew` |
| `bender` | `bender` | Bender Bending Rodriguez | bender@planetexpress.com | `ship_crew` |
| `professor` | `professor` | Hubert J. Farnsworth | professor@planetexpress.com | `admin_staff` |
| `hermes` | `hermes` | Hermes Conrad | hermes@planetexpress.com | `admin_staff` |
| `zoidberg` | `zoidberg` | John A. Zoidberg | zoidberg@planetexpress.com | — |
| `amy` | `amy` | Amy Wong (`sn`: Kroker) | amy@planetexpress.com | — |

Grupos (`objectClass: Group`, na mesma OU dos usuários, membros por DN em `member`, com o overlay `memberOf` ativo):

| Grupo | Membros |
|---|---|
| `ship_crew` | fry, leela, bender |
| `admin_staff` | professor, hermes |

Algumas curiosidades:

- O DN de `amy` é multi-valorado (`cn=Amy Wong+sn=Kroker`), uma peculiaridade do RDN; login e email funcionam normalmente.
- `professor` tem dois emails no LDAP (`professor@` e `hubert@`); o mapper importa o primeiro.
- As senhas estão em texto puro no LDAP (são dados falsos de dev). Nunca faça isso em produção.

Teste rápido — pegar um token usando a senha do LDAP:

```bash
curl -k -X POST https://auk.ucs.br/realms/dev/protocol/openid-connect/token \
  -d "grant_type=password&client_id=admin-cli&username=fry&password=fry&scope=openid"
```

### Persistência e re-seed do LDAP

- Os dados do LDAP ficam nos volumes nomeados `keycloak_openldap_data` (banco) e `keycloak_openldap_config` (`slapd.d`). Um restart, um `down` + `up` ou a recriação dos containers preserva os dados e os `entryUUID`s — inclusive edições feitas pelo phpLDAPadmin.
- O seed (schema, overlays e dados do Futurama) roda **uma única vez**, no primeiro boot com os volumes vazios, controlado pelo marcador `/var/lib/ldap/.seeded`.
- Para semear do zero: `LDAP_RESEED=1 docker compose up -d openldap` (apaga banco e config do LDAP). Os `entryUUID`s mudam, e os usuários já importados no Keycloak ficam com o link de federação inválido — o login falha com 401. Para corrigir, apague o realm `dev` no console admin e reinicie o Keycloak (o realm é reimportado), ou use `docker compose down -v` (que também apaga o PostgreSQL).
- O import do realm só acontece se o realm `dev` não existir. Para reimportar do zero, apague o realm no console e reinicie o Keycloak, ou use `docker compose down -v`.
- Trocar `LDAP_DOMAIN` exige editar os LDIFs em `ldap/bootstrap/`, onde os DNs estão escritos de forma fixa.

## Laboratório Flask + OpenID Connect

Em `flask-oidc-client/` há três clients de exemplo que rodam no host com `uv` e autenticam contra o realm `dev`. Todos usam o fluxo **Authorization Code** com client **confidential**, e todos escutam na porta **5000** — portanto, rode **um de cada vez**. O `app.py` pode ser acessado diretamente em `http://localhost:5000` ou pelo nginx da stack, no subpath `https://auk.ucs.br/flask/` (veja [Acessando pelo nginx](#acessando-pelo-nginx)).

### 1. Configurar o client no Keycloak

No boot do zero (realm `dev` recriado do nada), o client `flask-lab` já vem pronto em `keycloak/realms/dev-realm.json`: tipo **Confidential**, Standard flow habilitado e secret fixo de dev `flask-lab-dev-secret`. As URIs cadastradas são:

- Valid redirect URIs: `http://auk.ucs.br/flask/callback/*`, `http://localhost:5000/callback`, `https://auk.ucs.br/flask/callback` e `https://localhost:8443/flask/callback`
- Valid post logout redirect URIs: `http://auk.ucs.br/flask/*`, `http://localhost:5000/*`, `https://auk.ucs.br/flask/*` e `https://localhost:8443/flask/*`

O client exportado também mantém `serviceAccountsEnabled` e `directAccessGrantsEnabled`. A autorização fina (`authorizationSettings`, com uma política JS padrão) foi removida: o Keycloak em modo production recusa importar políticas JS inline e falha com `Script upload is disabled`.

Como o `--import-realm` só importa realms que ainda não existem, se o realm `dev` já estiver criado ajuste o client no console (`https://auk.ucs.br/admin` → **Clients → flask-lab**) ou apague o realm e reinicie o Keycloak para reimportar. O secret acima é fictício, de dev — nunca use em produção.

### 2. Rodar

```bash
cd flask-oidc-client
cp .env.example .env        # passo obrigatório
# o .env já traz o OIDC_CLIENT_SECRET fixo de dev; troque apenas o SECRET_KEY
uv sync
uv run python app.py
```

Sem o `.env`, o app não sobe e avisa que falta o secret. Todas as variáveis já vêm com valores alinhados ao stack, inclusive o `OIDC_CLIENT_SECRET` fixo `flask-lab-dev-secret`; vale trocar apenas o `SECRET_KEY` por um valor aleatório.

Abra `http://localhost:5000` e faça login com um usuário do LDAP (`fry` / `fry`, ...).

### Acessando pelo nginx

O app continua rodando no host (`uv run python app.py`), mas o nginx da stack pode serví-lo em **https://auk.ucs.br/flask/** (ou `https://localhost:8443/flask/` na variação de portas altas). O container alcança o host via `host.docker.internal`, configurado com `extra_hosts: host-gateway` no Compose. O Flask escuta em `0.0.0.0:5000` e usa `ProxyFix` com `X-Forwarded-Prefix: /flask` para manter os links dentro do subpath.

Para esse modo, ajuste o `.env` do cliente:

```dotenv
OIDC_REDIRECT_URI=https://auk.ucs.br/flask/callback
OIDC_POST_LOGOUT_REDIRECT_URI=https://auk.ucs.br/flask/
```

Essas URLs já estão cadastradas no client via `dev-realm.json`. Se o realm for pré-existente, cadastre-as no console:

- Valid redirect URIs: `https://auk.ucs.br/flask/callback` (e `https://localhost:8443/flask/callback`)
- Valid post logout redirect URIs: `https://auk.ucs.br/flask/*` (e `https://localhost:8443/flask/*`)

Mantenha as URLs de `http://localhost:5000` no client para não perder o acesso direto — alterne entre os dois perfis pelo `.env`. Como o `load_dotenv()` só lê o arquivo na inicialização, reinicie o app ao trocar de perfil. Com o Flask parado, o subpath responde `502`.

### Variáveis de ambiente

| Variável | Default | Obrigatória | Função |
|---|---|---|---|
| `OIDC_ISSUER_URL` | `https://auk.ucs.br/realms/dev` | não | issuer do realm; endpoints descobertos via `.well-known` |
| `OIDC_CLIENT_ID` | `flask-lab` | não | client criado no Keycloak |
| `OIDC_CLIENT_SECRET` | — | **sim** | segredo do client (aba Credentials no console admin) |
| `OIDC_REDIRECT_URI` | `http://localhost:5000/callback` | não | deve bater com "Valid redirect URIs" do client |
| `OIDC_POST_LOGOUT_REDIRECT_URI` | `http://localhost:5000/` | não | deve bater com "Valid post logout redirect URIs" do client |
| `OIDC_CLIENT_AUTH_METHOD` | `post` | não | como o client se autentica no token endpoint: `post` (secret no body) ou `basic` (HTTP Basic) |
| `OIDC_CA_BUNDLE` | `../nginx/certs/fullchain.pem` | não | CA self-signed do Keycloak de dev (apague a linha para usar a CA do sistema) |
| `SECRET_KEY` | gerada a cada boot | não (recomendada fixa) | assina o cookie de sessão do Flask; sem valor fixo, o login cai a cada restart |

### O que o laboratório mostra

| Rota | Aprendizado |
|---|---|
| `/auth` | monta a URL de autorização (`state` + `nonce`) e redireciona |
| `/callback` | troca o `code` por tokens e valida o ID token via JWKS (`iss`, `aud`, `exp`, `nonce`) |
| `/me` | userinfo (endpoint protegido) + claims do ID token + payload do access token |
| `/logout` | chama o `end_session_endpoint` no Keycloak e limpa a sessão |

Os endpoints são descobertos em tempo de execução via `/.well-known/openid-configuration` — nada fica hardcoded. Como o Keycloak de dev usa certificado self-signed, o app aponta `OIDC_CA_BUNDLE` para o certificado do nginx (nunca usa `verify=False`); com um certificado válido, basta apagar a linha do `.env`.

### As três implementações

| Etapa | `app.py` (manual) | `app_authlib.py` (Authlib) | `app_python_keycloak.py` (python-keycloak) |
|---|---|---|---|
| Descoberta | `OidcClient.discover()` + cache do `.well-known` | `server_metadata_url` | `KeycloakOpenID` (implícita) |
| URL de auth | `build_authorization_url()` | `authorize_redirect()` | `kc.auth_url()` |
| Code → token | `exchange_code()` | `authorize_access_token()` | `kc.token()` |
| Validação do ID token | JWKS + `iss`/`aud`/`exp`/`nonce` | interna da lib (inclui nonce) | `kc.decode_token(validate=True)`; nonce não conferido no app |
| userinfo | `GET` com Bearer | `userinfo()` | `kc.userinfo()` |
| Logout | `end_session_endpoint` + `id_token_hint` | `end_session_endpoint` direto | backchannel com refresh token |

Para rodar as versões com biblioteca:

```bash
uv run python app_authlib.py          # Authlib
uv run python app_python_keycloak.py  # python-keycloak
```

O `.env` é único e as três usam a mesma porta (5000), então escolha um app por vez. Se quiser rodar mais de um simultaneamente, dê portas diferentes a cada um (e ajuste `OIDC_REDIRECT_URI`, `OIDC_POST_LOGOUT_REDIRECT_URI` e os redirects cadastrados no client do Keycloak).

Limitações conscientes do laboratório: o payload do access token é apenas decodificado (sem verificar assinatura) no `/me` dos três apps; a sessão é um cookie assinado do Flask (~4 KB), não server-side; não há refresh automático no `app.py` nem no `app_authlib.py`.

Próximos passos sugeridos: refresh token, PKCE (client público) e exposição de roles/groups no token.

## Certificados

Para desenvolvimento (self-signed):

```bash
./nginx/certs/generate-dev-cert.sh
FORCE=1 ./nginx/certs/generate-dev-cert.sh                       # regenerar
CN=outro-host.local DAYS=365 ./nginx/certs/generate-dev-cert.sh  # CN e validade custom
# padrões: CN=auk.ucs.br, 825 dias
```

Em produção, substitua `nginx/certs/fullchain.pem` e `nginx/certs/privkey.pem` por um certificado real (Let's Encrypt, CA interna etc.), montando os arquivos nos mesmos caminhos. Para Let's Encrypt, o desafio HTTP-01 funciona na porta 80, que o nginx já atende.

Depois de trocar o certificado, recarregue o nginx:

```bash
docker compose exec nginx nginx -s reload
```

## Detalhes importantes

- `KC_HOSTNAME_STRICT=false` permite acesso por outros hosts (dev). Em produção, com domínio fixo, mude para `true`.
- Postgres não expõe porta no host; só é acessível pela rede interna `keycloak-net`.
- A rede bridge usa `172.28.0.0/24` (gateway `172.28.0.1`): postgres `172.28.0.10`, keycloak `172.28.0.20`, nginx `172.28.0.30`, openldap `172.28.0.40` e phpldapadmin `172.28.0.50`.
- Os dados persistem nos volumes `keycloak_postgres_data`, `keycloak_openldap_data`, `keycloak_openldap_config` e `keycloak_phpldapadmin_data` (nomes explícitos, sem prefixo do projeto).
- O healthcheck do Keycloak usa a porta de management 9000 (`KC_HEALTH_ENABLED=true`); o nginx também expõe `/health` publicamente, fazendo proxy para essa porta. Métricas habilitadas com `KC_METRICS_ENABLED=true`.
- O nginx alcança o app Flask no host via `host.docker.internal` (`extra_hosts: host-gateway` no Compose). O subpath `/flask/` depende do app rodando e escutando em `0.0.0.0:5000`.
- O redirecionamento HTTP monta a URL com `NGINX_HTTPS_PORT`; quando a porta é 443, o sufixo é omitido.
- O HSTS está comentado no template — habilite somente com certificado válido e domínio fixo.

## Problemas comuns

| Sintoma | Causa provável | Como resolver |
|---|---|---|
| Issuer e redirects saem com `http` | falta de `KC_PROXY_HEADERS=xforwarded` | mantenha a variável; o nginx envia os headers `X-Forwarded-*` |
| Login falha com 401 após um re-seed | `LDAP_RESEED=1` muda os `entryUUID`s e o link de federação fica inválido | apague o realm `dev` no console e reinicie o Keycloak, ou use `docker compose down -v` |
| Edição do `dev-realm.json` não surte efeito | o import só roda quando o realm não existe | apague o realm `dev` e reinicie o Keycloak, ou use `docker compose down -v` |
| phpLDAPadmin com `Parse error` | `PHPLDAPADMIN_LDAP_HOSTS` sem o prefixo `#PYTHON2BASH:` | mantenha o prefixo |
| phpLDAPadmin volta com configuração antiga | `config.php` gerado em volume anônimo | mantenha `keycloak_phpldapadmin_data` montado em `/var/www/phpldapadmin` |
| Client Flask falha TLS contra o Keycloak | certificado self-signed | use `OIDC_CA_BUNDLE=../nginx/certs/fullchain.pem` (nunca `verify=False`) |
| O segundo app Flask não sobe | os três apps usam `localhost:5000` | rode um por vez ou configure portas/redirects diferentes |
| `/flask/` retorna `502` | app parado ou ainda com o bind antigo em `127.0.0.1:5000` | reinicie `uv run python app.py` (o código agora escuta em `0.0.0.0`) |
| Login via `/flask/` falha com `invalid_request_uri` | URLs do subpath não cadastradas no client `flask-lab` ou `.env` ainda no perfil A | cadastre `https://auk.ucs.br/flask/callback` no Keycloak e ajuste `OIDC_REDIRECT_URI` no `.env` do cliente |
| Keycloak não sobe com `Script upload is disabled` | client importado com `authorizationSettings` e política JS inline | remova `authorizationSettings` do client no realm JSON (autorização fina não é usada pelo lab) |

## Limpeza

```bash
docker compose down          # para os containers; dados do LDAP e do banco persistem
docker compose down -v       # para e apaga os volumes: banco, realm, usuários, LDAP e config do phpLDAPadmin
```

Depois de um `down` + `up`, o LDAP mantém os `entryUUID`s e os imports do Keycloak continuam válidos — não há nada a fazer. Se o LDAP foi re-semeado com o banco preservado (`LDAP_RESEED=1` ou remoção manual apenas dos volumes `keycloak_openldap_*`), apague o realm `dev` no console admin e reinicie o Keycloak para reimportá-lo.
