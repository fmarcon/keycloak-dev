"""Configuracao e helpers compartilhados entre app.py e app_authlib.py.

Fornece:
  - OidcConfig (dataclass frozen) carregado de env vars
  - http_session()  — requests.Session com CA bundle customizado
  - ssl_context()   — SSLContext para PyJWKClient (urllib)
  - build_logout_url() — monta URL de RP-Initiated Logout
"""

from __future__ import annotations

import os
import ssl
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# Defaults alinhados ao stack dev: apenas OIDC_CLIENT_SECRET e obrigatoria
# (segredo nao tem default). Copie .env.example para .env e ajuste.
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OidcConfig:
    """Configuracao carregada de variaveis de ambiente (com defaults)."""

    issuer_url: str
    client_id: str
    client_secret: str
    redirect_uri: str
    post_logout_redirect_uri: str
    client_auth_method: str  # "post" | "basic"
    ca_bundle: str | bool  # True = CA do sistema; str = caminho do cert self-signed

    @staticmethod
    def from_env() -> OidcConfig:
        ca_raw = _env("OIDC_CA_BUNDLE")
        ca_bundle: str | bool = True if not ca_raw else ca_raw
        return OidcConfig(
            issuer_url=_env("OIDC_ISSUER_URL"),
            client_id=_env("OIDC_CLIENT_ID"),
            client_secret=_env("OIDC_CLIENT_SECRET", required=True),
            redirect_uri=_env("OIDC_REDIRECT_URI"),
            post_logout_redirect_uri=_env("OIDC_POST_LOGOUT_REDIRECT_URI"),
            client_auth_method=_env("OIDC_CLIENT_AUTH_METHOD"),
            ca_bundle=ca_bundle,
        )


# ---------------------------------------------------------------------------
# Helpers HTTP / SSL
# ---------------------------------------------------------------------------


def http_session(cfg: OidcConfig) -> requests.Session:
    """Session com o CA bundle do Keycloak de dev (cert self-signed)."""
    s = requests.Session()
    s.verify = cfg.ca_bundle  # True (CA do sistema) ou caminho de arquivo
    return s


def ssl_context(cfg: OidcConfig) -> ssl.SSLContext | None:
    """Contexto SSL com CA bundle custom (PyJWKClient usa urllib, nao requests).

    Retorna None quando cfg.ca_bundle e True (valida com CAs do sistema).
    """
    if cfg.ca_bundle is True:
        return None
    return ssl.create_default_context(cafile=cfg.ca_bundle)


# ---------------------------------------------------------------------------
# Helpers de URL
# ---------------------------------------------------------------------------


def build_logout_url(
    end_session_endpoint: str,
    id_token: str,
    post_logout_redirect_uri: str,
) -> str:
    """Monta a URL de RP-Initiated Logout (end_session).

    O Keycloak invalida a sessao SSO e devolve o browser para a
    post_logout_redirect_uri cadastrada no client.
    """
    return (
        f"{end_session_endpoint}"
        f"?id_token_hint={urllib.parse.quote(id_token)}"
        f"&post_logout_redirect_uri={urllib.parse.quote(post_logout_redirect_uri)}"
    )
