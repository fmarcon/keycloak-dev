"""Laboratorio: cliente OpenID Connect (Flask) para o Keycloak de dev.

MESMA coisa que `app.py`, mas usando o **Authlib** (biblioteca de alto nivel
p/ OAuth/OIDC) em vez de implementar o fluxo na mao com requests/PyJWT.

Comparacao com o app.py (o que a lib abstrai):

  app.py (manual)                         app_authlib.py (Authlib)
  --------------------------------------  --------------------------------------
  discover() + cache do .well-known  ->   server_metadata_url (busca e cacheia)
  montar URL de auth (state+nonce)   ->   authorize_redirect()
  trocar code por tokens             ->   authorize_access_token()
  validar ID token (JWKS/iss/aud/exp/nonce) -> feito dentro do authorize_access_token()
                                            (via parse_id_token); claims em token['userinfo']
  GET userinfo com Bearer manual     ->   userinfo() (Bearer gerenciado pela lib)
  montar end_session URL manual      ->   load_server_metadata()['end_session_endpoint']
                                            (so o logout nao tem helper; montado na mao)

Executar (uv):
    uv sync
    uv run python app_authlib.py          # dev server em http://localhost:5000

Config: mesmo `.env` do app.py (OIDC_*, SECRET_KEY).
"""

from __future__ import annotations

import os
import secrets
import urllib.parse
from pathlib import Path

import jwt  # PyJWT (so para decodificar o payload do access token p/ exibir)
import requests
from authlib.integrations.base_client.errors import OAuthError
from authlib.integrations.flask_client import OAuth
from dotenv import load_dotenv
from flask import Flask, redirect, render_template_string, session, url_for

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


def http() -> requests.Session:
    """Session com o CA bundle do Keycloak de dev (cert self-signed)."""
    s = requests.Session()
    s.verify = CA_BUNDLE  # True (validar com CA do sistema) ou caminho de arquivo
    return s


# --- Authlib -------------------------------------------------------------------
# OAuth.register recebe o discovery URL e a lib resolve metadata, jwks_uri,
# authorization/token/userinfo endpoints sozinha (equivale ao discover() do app.py).
# `client_kwargs['verify']` e' repassado para a Session interna do requests
# (TLS self-signed do Keycloak dev).
oauth = OAuth(app)
oauth.register(
    "keycloak",
    server_metadata_url=f"{ISSUER_URL}/.well-known/openid-configuration",
    client_id=CLIENT_ID,
    client_secret=CLIENT_SECRET,
    client_kwargs={
        "scope": "openid profile email",  # openid e OBRIGATORIO p/ OIDC
        "verify": CA_BUNDLE,  # True (CA do sistema) ou caminho do cert self-signed
    },
)

# --- Rotas ---------------------------------------------------------------------


@app.route("/")
def index():
    claims = session.get("id_claims")
    return render_template_string(
        PAGE, section="index", id_claims=claims, userinfo=None, access_claims=None
    )


@app.route("/auth")
def auth():
    """Passo 1: authorize_redirect monta a URL (state + nonce gerados e
    guardados na sessao pela lib) e ja retorna a Response de redirect."""
    return oauth.keycloak.authorize_redirect(REDIRECT_URI)


@app.route("/callback")
def callback():
    """Passo 3: authorize_access_token troca o code por tokens E valida:
    state (anti-CSRF) e ID token (assinatura via JWKS, iss, aud, exp, nonce).
    Claims validados ficam em token['userinfo'] — equivale a todo o passo
    de validacao manual do app.py.
    """
    try:
        token = oauth.keycloak.authorize_access_token()
    except OAuthError as exc:
        return f"Erro na autorizacao: {exc}", 400

    session["token"] = token  # a lib reusa p/ userinfo()/logout
    session["id_claims"] = dict(token["userinfo"])
    return redirect(url_for("me"))


@app.route("/me")
def me():
    token = session.get("token")
    if not token:
        return redirect(url_for("auth"))

    # userinfo: endpoint protegido; a lib usa o access token guardado
    oauth.keycloak.token = token
    userinfo = oauth.keycloak.userinfo()

    id_claims = session.get("id_claims", {})
    # Payload do access token SO para ler/entender (a lib nao valida aqui;
    # num server real, a assinatura seria verificada como no ID token).
    access_claims = jwt.decode(
        token["access_token"], options={"verify_signature": False}
    )

    return render_template_string(
        PAGE,
        section="me",
        id_claims=id_claims,
        userinfo=userinfo,
        access_claims=access_claims,
    )


@app.route("/logout")
def logout():
    token = session.get("token")
    id_token = (token or {}).get("id_token", "")
    session.clear()

    # Authlib nao tem helper de RP-Initiated Logout; o endpoint vem do metadata
    # descoberto (mesmo "end_session_endpoint" que o app.py montava na mao).
    metadata = oauth.keycloak.load_server_metadata()
    logout_url = metadata.get("end_session_endpoint")
    if not logout_url:
        return redirect(url_for("index"))

    url = (
        f"{logout_url}?id_token_hint={urllib.parse.quote(id_token)}"
        f"&post_logout_redirect_uri={urllib.parse.quote(POST_LOGOUT_REDIRECT_URI)}"
    )
    return redirect(url)


# --- Template ------------------------------------------------------------------
# Mesmo template do app.py (as rotas entregam os mesmos dados p/ a pagina).

PAGE = """<!doctype html>
<html lang="pt-br">
<head>
<meta charset="utf-8">
<title>Flask OIDC Lab (Authlib)</title>
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
<h1>Flask + OpenID Connect (laboratorio — Authlib)</h1>
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
<h2>userinfo (endpoint protegido, via Authlib)</h2>
<table>
{% for k, v in userinfo.items() %}<tr><td><code>{{ k }}</code></td><td>{{ v }}</td></tr>{% endfor %}
</table>

<h2>ID token — claims (validado pelo authorize_access_token)</h2>
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
