"""Laboratorio: cliente OpenID Connect (Flask) para o Keycloak de dev.

MESMA coisa que ``app.py``, mas usando o **python-keycloak** em vez de
implementar o fluxo na mao com requests/PyJWT.

Comparacao com app.py e app_authlib.py:

  app.py (manual)               python-keycloak            app_authlib.py (Authlib)
  ----------------------------  -------------------------  ---------------------------
  discover()                    kc.well_known()            server_metadata_url
  build_authorization_url()     kc.auth_url()              authorize_redirect()
  exchange_code()               kc.token(code=...)         authorize_access_token()
  decode_token(validate=True)   kc.decode_token()          (interna da lib)
  userinfo()                    kc.userinfo()              userinfo()
  build_logout_url()            kc.logout(refresh_token)   (nao tem helper)

  Fluxo:
  1. Descoberta ......... kc.well_known() implicito (a lib busca quando precisa)
  2. /auth .............. kc.auth_url() (state + nonce) + redireciona
  3. /callback .......... kc.token(code=...) troca code por tokens;
                          kc.decode_token(id_token, validate=True) valida
                          assinatura (JWKS do realm) e exp
  4. /me ................ kc.userinfo(access_token)
  5. /logout ............ kc.logout(refresh_token) invalida sessao SSO
                          (backchannel) + limpa sessao local

Executar (uv):
    uv sync
    uv run python app_python_keycloak.py    # dev server em http://localhost:5000

Config: mesmo ``.env`` dos outros apps (OIDC_*, SECRET_KEY).
"""

from __future__ import annotations

import logging
import os
import secrets

from dotenv import load_dotenv
from flask import abort, Flask, redirect, render_template, request, session, url_for
from keycloak import KeycloakOpenID, KeycloakPostError

log = logging.getLogger(__name__)

load_dotenv()

# ---------------------------------------------------------------------------
# Config (autocontida, nao reusa config.py para manter a comparacao clara)
# ---------------------------------------------------------------------------
_DEFAULTS: dict[str, str] = {
    "OIDC_ISSUER_URL": "https://auk.ucs.br/realms/dev",
    "OIDC_CLIENT_ID": "flask-lab",
    "OIDC_REDIRECT_URI": "http://localhost:5000/callback",
    "OIDC_POST_LOGOUT_REDIRECT_URI": "http://localhost:5000/",
    "OIDC_CA_BUNDLE": "../nginx/certs/fullchain.pem",
}


def _env(name: str, required: bool = False) -> str:
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
CA_BUNDLE = _env("OIDC_CA_BUNDLE") or True  # True = CA do sistema

# Deriva server_url + realm_name do issuer
#   issuer_url = "https://auk.ucs.br/realms/dev"  ->  server_url = "https://auk.ucs.br/"
#                                                       realm_name = "dev"
REALM_NAME = ISSUER_URL.rstrip("/").rsplit("/", 1)[-1]
REALMS_SUFFIX = f"/realms/{REALM_NAME}"
SERVER_URL = ISSUER_URL.removesuffix(REALMS_SUFFIX) + "/"

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))

# ---------------------------------------------------------------------------
# python-keycloak client
# ---------------------------------------------------------------------------
kc = KeycloakOpenID(
    server_url=SERVER_URL,
    realm_name=REALM_NAME,
    client_id=CLIENT_ID,
    client_secret_key=CLIENT_SECRET,
    verify=CA_BUNDLE,
    timeout=10,
)

# --- Rotas ---------------------------------------------------------------------


@app.route("/")
def index():
    claims = session.get("id_claims")
    return render_template(
        "page.html",
        app_title="Flask OIDC Lab (python-keycloak)",
        app_subtitle="Flask + OpenID Connect (laboratório — python-keycloak)",
        userinfo_label="(endpoint protegido, via python-keycloak)",
        idtoken_label="(validado pelo decode_token)",
        section="index",
        id_claims=claims,
        userinfo=None,
        access_claims=None,
    )


@app.route("/auth")
def auth():
    """Passo 1: monta URL de autorizacao e redireciona o browser ao Keycloak.

    ``state`` protege contra CSRF (o /callback recebe o mesmo valor de volta)
    e ``nonce`` protege contra replay do ID token (nao validado neste app —
    a lib nao expoe os claims validados para checagem externa facil).
    """
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    session["oidc_state"] = state
    session["oidc_nonce"] = nonce

    url = kc.auth_url(
        redirect_uri=REDIRECT_URI,
        scope="openid profile email",
        state=state,
        nonce=nonce,
    )
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

    # 2) troca code por tokens
    token = kc.token(
        grant_type="authorization_code",
        code=code,
        redirect_uri=REDIRECT_URI,
    )

    # 3) valida ID token (assinatura via JWKS do realm + exp)
    claims = kc.decode_token(token["id_token"], validate=True)

    session["access_token"] = token["access_token"]
    session["id_token"] = token["id_token"]
    session["refresh_token"] = token.get("refresh_token", "")
    session["id_claims"] = claims
    return redirect(url_for("me"))


@app.route("/me")
def me():
    access_token = session.get("access_token")
    if not access_token:
        return redirect(url_for("auth"))

    # userinfo: perfil via endpoint protegido pelo access token
    userinfo = kc.userinfo(access_token)

    id_claims = session.get("id_claims", {})
    # Payload do access token SO para ler/entender (sem validacao aqui)
    access_claims = kc.decode_token(access_token, validate=False)

    return render_template(
        "page.html",
        app_title="Flask OIDC Lab (python-keycloak)",
        app_subtitle="Flask + OpenID Connect (laboratório — python-keycloak)",
        userinfo_label="(endpoint protegido, via python-keycloak)",
        idtoken_label="(validado pelo decode_token)",
        section="me",
        id_claims=id_claims,
        userinfo=userinfo,
        access_claims=access_claims,
    )


@app.route("/logout")
def logout():
    refresh_token = session.get("refresh_token", "")
    session.clear()

    # Backchannel logout: invalida a sessao SSO no Keycloak usando o
    # refresh token. Se o token ja expirou/invalido, ignora o erro.
    if refresh_token:
        try:
            kc.logout(refresh_token)
        except KeycloakPostError:
            log.warning("Logout backchannel: refresh token ja expirado ou invalido")

    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
