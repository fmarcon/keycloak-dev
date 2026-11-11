"""Laboratorio: cliente OpenID Connect (Flask) para o Keycloak de dev.

MESMA coisa que ``app.py``, mas usando o **Authlib** (biblioteca de alto nivel
p/ OAuth/OIDC) em vez de implementar o fluxo na mao com requests/PyJWT.

Comparacao com o app.py (o que a lib abstrai):

  app.py (manual)                         app_authlib.py (Authlib)
  --------------------------------------  --------------------------------------
  OidcClient.discover()               ->  server_metadata_url (busca e cacheia)
  OidcClient.build_authorization_url()->  authorize_redirect()
  OidcClient.exchange_code()          ->  authorize_access_token()
  validar ID token (JWKS/iss/aud/exp/
    nonce) manual                     ->  feito dentro do authorize_access_token()
                                            (via parse_id_token); claims em
                                            token['userinfo']
  OidcClient.userinfo()               ->  userinfo() (Bearer gerenciado pela lib)
  OidcClient.build_logout_url()       ->  load_server_metadata() +
                                           configuravel em config.build_logout_url()

Executar (uv):
    uv sync
    uv run python app_authlib.py          # dev server em http://localhost:5000

Config: mesmo ``.env`` do app.py (OIDC_*, SECRET_KEY).
"""

from __future__ import annotations

import os
import secrets

import jwt  # PyJWT (so para decodificar o payload do access token p/ exibir)
from authlib.integrations.base_client.errors import OAuthError
from authlib.integrations.flask_client import OAuth
from dotenv import load_dotenv
from flask import Flask, redirect, render_template, session, url_for

from config import build_logout_url, OidcConfig

load_dotenv()

cfg = OidcConfig.from_env()

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))


# --- Authlib --------------------------------------------------------------------
# OAuth.register recebe o discovery URL e a lib resolve metadata, jwks_uri,
# authorization/token/userinfo endpoints sozinha (equivale ao OidcClient).
# ``client_kwargs["verify"]`` e repassado para a Session interna do requests
# (TLS self-signed do Keycloak dev).
oauth = OAuth(app)
oauth.register(
    "keycloak",
    server_metadata_url=f"{cfg.issuer_url}/.well-known/openid-configuration",
    client_id=cfg.client_id,
    client_secret=cfg.client_secret,
    client_kwargs={
        "scope": "openid profile email",  # openid e OBRIGATORIO p/ OIDC
        "verify": cfg.ca_bundle,  # True (CA do sistema) ou caminho do cert
    },
)

# --- Rotas ---------------------------------------------------------------------


@app.route("/")
def index():
    claims = session.get("id_claims")
    return render_template(
        "page.html",
        app_title="Flask OIDC Lab (Authlib)",
        app_subtitle="Flask + OpenID Connect (laboratório — Authlib)",
        userinfo_label="(endpoint protegido, via Authlib)",
        idtoken_label="(validado pelo authorize_access_token)",
        section="index",
        id_claims=claims,
        userinfo=None,
        access_claims=None,
    )


@app.route("/auth")
def auth():
    """Passo 1: authorize_redirect monta a URL (state + nonce gerados e
    guardados na sessao pela lib) e ja retorna a Response de redirect."""
    return oauth.keycloak.authorize_redirect(cfg.redirect_uri)


@app.route("/callback")
def callback():
    """Passo 3: authorize_access_token troca o code por tokens E valida:
    state (anti-CSRF) e ID token (assinatura via JWKS, iss, aud, exp, nonce).
    Claims validados ficam em ``token["userinfo"]``.
    """
    try:
        token = oauth.keycloak.authorize_access_token()
    except OAuthError as exc:
        return f"Erro na autorizacao: {exc}", 400

    session["token"] = token  # a lib reusa p/ userinfo() / logout
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

    return render_template(
        "page.html",
        app_title="Flask OIDC Lab (Authlib)",
        app_subtitle="Flask + OpenID Connect (laboratório — Authlib)",
        userinfo_label="(endpoint protegido, via Authlib)",
        idtoken_label="(validado pelo authorize_access_token)",
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
    # descoberto (mesmo ``end_session_endpoint`` que o app.py usava).
    metadata = oauth.keycloak.load_server_metadata()
    end_session = metadata.get("end_session_endpoint")
    if not end_session:
        return redirect(url_for("index"))

    logout_url = build_logout_url(end_session, id_token, cfg.post_logout_redirect_uri)
    return redirect(logout_url)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
