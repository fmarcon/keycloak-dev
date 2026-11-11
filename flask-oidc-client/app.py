"""Laboratorio: cliente OpenID Connect (Flask) para o Keycloak de dev.

Fluxo Authorization Code com client confidential, implementado na mao
com ``requests`` + ``pyjwt`` para aprender o protocolo.

  1. Descoberta ......... GET {issuer}/.well-known/openid-configuration
  2. /auth .............. monta URL de autorizacao (state + nonce) e redireciona
  3. /callback .......... troca o code por tokens e valida o ID token (JWKS)
  4. /me ................ userinfo + claims do ID token + payload do access token
  5. /logout ............ end_session no Keycloak + limpa a sessao

A logica OIDC foi extraida para ``OidcClient`` (oidc_client.py) e a config
para ``OidcConfig`` (config.py). O template HTML fica em templates/page.html.

Executar (uv):
    uv sync
    uv run python app.py          # dev server em http://localhost:5000

Config: copie ``.env.example`` para ``.env`` e preencha (client criado no console
admin do Keycloak, realm dev, tipo Confidential).
"""

from __future__ import annotations

import os
import secrets

import jwt  # PyJWT (so para decodificar access token p/ exibir)
import requests
from dotenv import load_dotenv
from flask import abort, Flask, redirect, render_template, request, session, url_for

from config import OidcConfig
from oidc_client import OidcClient, OidcClientError

load_dotenv()

cfg = OidcConfig.from_env()
oidc = OidcClient(cfg)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))


# --- Rotas ---------------------------------------------------------------------


@app.route("/")
def index():
    claims = session.get("id_claims")
    return render_template(
        "page.html",
        app_title="Flask OIDC Lab",
        app_subtitle="Flask + OpenID Connect (laboratório)",
        userinfo_label="(endpoint protegido, Bearer access token)",
        idtoken_label="(validado no callback)",
        section="index",
        id_claims=claims,
        userinfo=None,
        access_claims=None,
    )


@app.route("/auth")
def auth():
    """Passo 1: monta URL de autorizacao e redireciona o browser ao Keycloak.

    ``state`` protege contra CSRF (o /callback recebe o mesmo valor de volta)
    e ``nonce`` protege contra replay do ID token.
    """
    url, state, nonce = oidc.build_authorization_url()
    session["oidc_state"] = state
    session["oidc_nonce"] = nonce
    return redirect(url)


@app.route("/callback")
def callback():
    """Passo 3: o Keycloak volta aqui com o ``code``; trocamos pelos tokens."""
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

    # 2) troca code por tokens + valida ID token (JWKS, iss, aud, exp, nonce)
    expected_nonce = session.pop("oidc_nonce", "")
    try:
        result = oidc.exchange_code(code, state, expected_nonce)
    except (OidcClientError, requests.HTTPError, jwt.PyJWTError) as exc:
        return f"Erro na troca do code: {exc}", 400

    session["access_token"] = result["access_token"]
    session["id_token"] = result["id_token"]
    session["id_claims"] = result["id_claims"]
    return redirect(url_for("me"))


@app.route("/me")
def me():
    access_token = session.get("access_token")
    if not access_token:
        return redirect(url_for("auth"))

    # userinfo: perfil via endpoint protegido pelo access token
    userinfo = oidc.userinfo(access_token)

    id_claims = session.get("id_claims", {})
    # Payload do access token SO para ler/entender a estrutura (a assinatura
    # nao e verificada aqui; num server real validaria via JWKS igual ao ID token).
    access_claims = jwt.decode(access_token, options={"verify_signature": False})

    return render_template(
        "page.html",
        app_title="Flask OIDC Lab",
        app_subtitle="Flask + OpenID Connect (laboratório)",
        userinfo_label="(endpoint protegido, Bearer access token)",
        idtoken_label="(validado no callback)",
        section="me",
        id_claims=id_claims,
        userinfo=userinfo,
        access_claims=access_claims,
    )


@app.route("/logout")
def logout():
    id_token = session.get("id_token", "")
    logout_url = oidc.build_logout_url(id_token)
    session.clear()

    if logout_url:
        return redirect(logout_url)
    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
