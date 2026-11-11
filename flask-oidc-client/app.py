"""Laboratorio: cliente OpenID Connect (Flask) para o Keycloak de dev.

Fluxo Authorization Code com client confidential, implementado na mao
com `requests` + `pyjwt` para aprender o protocolo:

  1. Descoberta ......... GET {issuer}/.well-known/openid-configuration
  2. /auth .............. monta URL de autorizacao (state + nonce) e redireciona
  3. /callback .......... troca o code por tokens e valida o ID token (JWKS)
  4. /me ................ userinfo + claims do ID token + payload do access token
  5. /logout ............ end_session no Keycloak + limpa a sessao

Executar (uv):
    uv sync
    uv run python app.py        # dev server em http://localhost:5000

Config: copie `.env.example` para `.env` e preencha (client criado no console
admin do Keycloak, realm dev, tipo Confidential).
"""

from __future__ import annotations

import os
import secrets
import ssl
import time
import urllib.parse
from pathlib import Path

import jwt  # PyJWT
import requests
from dotenv import load_dotenv
from flask import (
    Flask,
    abort,
    redirect,
    render_template_string,
    request,
    session,
    url_for,
)

load_dotenv()

# --- Config (env) -------------------------------------------------------------
# Defaults alinhados ao stack dev: apenas OIDC_CLIENT_SECRET e obrigatoria
# (segredo nao tem default). Copie .env.example para .env e ajuste.
_DEFAULTS: dict[str, str] = {
    "OIDC_ISSUER_URL": "https://auk.ucs.br/realms/dev",
    "OIDC_CLIENT_ID": "flask-lab",
    "OIDC_REDIRECT_URI": "http://localhost:5000/callback",
    "OIDC_POST_LOGOUT_REDIRECT_URI": "http://localhost:5000/",
    "OIDC_CLIENT_AUTH_METHOD": "post",  # post | basic
    "OIDC_CA_BUNDLE": str(
        Path(__file__).resolve().parent.parent / "nginx" / "certs" / "fullchain.pem"
    ),
}


def _env(name: str, required: bool = False) -> str:
    """Le env var com default do stack; se obrigatoria e ausente, aponta o erro."""
    val = os.environ.get(name, _DEFAULTS.get(name, ""))
    if required and not val:
        raise RuntimeError(
            f"ERRO: variavel OIDC nao definida: {name}\n"
            "Como resolver:\n"
            "  1. cd flask-oidc-client\n"
            "  2. cp .env.example .env\n"
            "  3. edite o .env (cole o client secret do Keycloak na aba Credentials e troque SECRET_KEY)"
        )
    return val


ISSUER_URL = _env("OIDC_ISSUER_URL")
CLIENT_ID = _env("OIDC_CLIENT_ID")
CLIENT_SECRET = _env("OIDC_CLIENT_SECRET", required=True)
REDIRECT_URI = _env("OIDC_REDIRECT_URI")
POST_LOGOUT_REDIRECT_URI = _env("OIDC_POST_LOGOUT_REDIRECT_URI")
CLIENT_AUTH_METHOD = _env("OIDC_CLIENT_AUTH_METHOD")
CA_BUNDLE = _env("OIDC_CA_BUNDLE") or True  # True = validar com CA do sistema

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))

# --- Descoberta (cache curto) -------------------------------------------------
# Os endpoints sao descobertos em runtime; na pratica nao mudam, entao
# cacheamos por TTL para nao bater no Keycloak a cada request.
_OIDC_DISCOVERY: dict | None = None
_OIDC_DISCOVERY_TS = 0.0
OIDC_DISCOVERY_TTL = 600.0


def discover() -> dict:
    """Busca os endpoints (authorization, token, userinfo, jwks, logout)
    no documento de descoberta OpenID."""

    global _OIDC_DISCOVERY, _OIDC_DISCOVERY_TS
    now = time.monotonic()
    if _OIDC_DISCOVERY and (now - _OIDC_DISCOVERY_TS) < OIDC_DISCOVERY_TTL:
        return _OIDC_DISCOVERY

    url = f"{ISSUER_URL}/.well-known/openid-configuration"
    with http() as s:
        resp = s.get(url, timeout=10)
    resp.raise_for_status()

    _OIDC_DISCOVERY = resp.json()
    _OIDC_DISCOVERY_TS = now
    return _OIDC_DISCOVERY


def http() -> requests.Session:
    """Session com o CA bundle do Keycloak de dev (cert self-signed)."""
    s = requests.Session()
    s.verify = CA_BUNDLE  # True (validar com CA do sistema) ou caminho de arquivo
    return s


def ssl_context() -> ssl.SSLContext | None:
    """Contexto SSL com o CA bundle custom (o PyJWKClient usa urllib, nao requests)."""
    if CA_BUNDLE is True:
        return None  # validar com as CAs do sistema
    ctx = ssl.create_default_context(cafile=CA_BUNDLE)
    return ctx


def token_endpoint() -> str:
    return discover()["token_endpoint"]


def userinfo_endpoint() -> str:
    return discover()["userinfo_endpoint"]


def end_session_endpoint() -> str | None:
    return discover().get("end_session_endpoint")


def jwks_uri() -> str:
    return discover()["jwks_uri"]


# --- Rotas ---------------------------------------------------------------------


@app.route("/")
def index():
    claims = session.get("id_claims")
    return render_template_string(
        PAGE, section="index", id_claims=claims, userinfo=None, access_claims=None
    )


@app.route("/auth")
def auth():
    """Passo 1: monta a URL de autorizacao e manda o browser para o Keycloak.

    `state` protege conta CSRF (o /callback recebe o mesmo valor de volta)
    e `nonce` protege contra replay do ID token.
    """
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    session["oidc_state"] = state
    session["oidc_nonce"] = nonce

    params = {
        "response_type": "code",  # Authorization Code Flow
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": "openid profile email",  # openid e OBRIGATORIO p/ OIDC
        "state": state,
        "nonce": nonce,
    }
    authz_url = discover()["authorization_endpoint"]
    return redirect(f"{authz_url}?{urllib.parse.urlencode(params)}")


@app.route("/callback")
def callback():
    """Passo 3: o Keycloak volta aqui com o `code`; trocamos pelos tokens."""
    if err := request.args.get("error"):
        msg = request.args.get("error_description", "")
        return f"Erro na autorizacao: {err} ({msg})", 400

    code = request.args.get("code")
    state = request.args.get("state")
    if not code or not state:
        abort(400, "callback sem code/state")

    # 1) state precisa bater com o que /auth guardou (anti-CSRF)
    if not secrets.compare_digest(state, session.pop("oidc_state", "")):
        abort(400, "state mismatch (possivel ataque CSRF)")

    # 2) troca o code por tokens. Client confidential se autentica com o secret:
    #    `post` = client_secret no body | `basic` = HTTP Basic Auth.
    body = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "client_id": CLIENT_ID,
    }
    auth = None
    if CLIENT_AUTH_METHOD == "basic":
        auth = (CLIENT_ID, CLIENT_SECRET)
    else:  # post
        body["client_secret"] = CLIENT_SECRET

    with http() as s:
        resp = s.post(token_endpoint(), data=body, auth=auth, timeout=10)
    resp.raise_for_status()
    tokens = resp.json()
    id_token = tokens["id_token"]
    access_token = tokens["access_token"]

    # 3) valida o ID token: assinatura (chave publica do issuer via jwks_uri),
    #    issuer, audience, exp e nonce. O PyJWT busca a chave certa pelo "kid"
    #    no header do token (PyJWKClient).
    key = (
        jwt.PyJWKClient(jwks_uri(), cache_keys=True, ssl_context=ssl_context())
        .get_signing_key_from_jwt(id_token)
        .key
    )
    claims = jwt.decode(
        id_token,
        key,
        algorithms=["RS256"],  # Keycloak assina com RS256 por padrao
        audience=CLIENT_ID,
        issuer=ISSUER_URL,
        options={"require": ["exp", "iss", "aud"]},
    )
    if not secrets.compare_digest(
        session.pop("oidc_nonce", ""), claims.get("nonce", "")
    ):
        abort(400, "nonce mismatch (possivel replay)")

    session["access_token"] = access_token
    session["id_token"] = id_token
    session["id_claims"] = claims
    return redirect(url_for("me"))


@app.route("/me")
def me():
    access_token = session.get("access_token")
    if not access_token:
        return redirect(url_for("auth"))

    # userinfo: perfil via endpoint protegido pelo access token
    with http() as s:
        resp = s.get(
            userinfo_endpoint(),
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
    resp.raise_for_status()
    userinfo = resp.json()

    id_claims = session.get("id_claims", {})
    # Payload do access token SO para ler/entender a estrutura (a assinatura
    # nao e verificada aqui; num server real validaria via JWKS igual ao ID token).
    access_claims = jwt.decode(access_token, options={"verify_signature": False})

    return render_template_string(
        PAGE,
        section="me",
        id_claims=id_claims,
        userinfo=userinfo,
        access_claims=access_claims,
    )


@app.route("/logout")
def logout():
    id_token = session.get("id_token")
    logout_url = end_session_endpoint()
    session.clear()

    if not logout_url:
        return redirect(url_for("index"))

    # end_session (RP-Initiated Logout): o Keycloak invalida a sessao SSO e
    # devolve o browser para o post_logout_redirect_uri cadastrado no client.
    url = (
        f"{logout_url}?id_token_hint={urllib.parse.quote(id_token or '')}"
        f"&post_logout_redirect_uri={urllib.parse.quote(POST_LOGOUT_REDIRECT_URI)}"
    )
    return redirect(url)


# --- Template -------------------------------------------------------------------

PAGE = """<!doctype html>
<html lang="pt-br">
<head>
<meta charset="utf-8">
<title>Flask OIDC Lab</title>
<style>
 body { font-family: system-ui, sans-serif; max-width: 1000px; margin: 2rem auto; padding: 0 1rem; color: #222; line-height: 1.5; }
 h1 { border-bottom: 2px solid #ddd; padding-bottom: .4rem; }
 table { border-collapse: collapse; width: 100%; }
 th, td { text-align: left; padding: .35rem .6rem; border-bottom: 1px solid #eee; vertical-align: top; }
 code, pre { background: #f4f4f4; padding: .1rem .3rem; border-radius: 4px; font-size: .9em; }
 pre { padding: 1rem; overflow: auto; }
 a.btn { display: inline-block; padding: .5rem 1.1rem; background: #2152a1; color: #fff; text-decoration: none; border-radius: 6px; }
 a.logout { background: #a12c2c; }
 .nav a { margin-right: .8rem; }
</style>
</head>
<body>
<h1>Flask + OpenID Connect (laboratorio)</h1>
<p class="nav">
  <a href="{{ url_for('index') }}">Home</a>
  {% if id_claims %}| <a href="{{ url_for('me') }}">/me</a> | <a class="logout" href="{{ url_for('logout') }}">sair</a>{% endif %}
</p>

{% if not id_claims %}
  <p>Voce nao esta autenticado no Keycloak.</p>
  <p><a class="btn" href="{{ url_for('auth') }}">Login com Keycloak</a></p>
{% else %}
  <p>Logado como <strong>{{ id_claims.get('preferred_username') or id_claims.get('name') }}</strong>
     (subject <code>{{ id_claims.get('sub') }}</code>).</p>
{% endif %}

{% if section == 'me' %}
<h2>userinfo (endpoint protegido, Bearer access token)</h2>
<table>
{% for k, v in userinfo.items() %}<tr><td><code>{{ k }}</code></td><td>{{ v }}</td></tr>{% endfor %}
</table>

<h2>ID token — claims (validado no callback)</h2>
<table>
{% for k, v in id_claims.items() %}<tr><td><code>{{ k }}</code></td><td>{{ v }}</td></tr>{% endfor %}
</table>

<h2>Access token — payload (nao validado aqui; so para entender)</h2>
<pre>{{ access_claims | tojson(indent=2) }}</pre>
{% endif %}

</body>
</html>"""


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
