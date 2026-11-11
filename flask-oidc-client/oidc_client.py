"""Service layer: cliente OpenID Connect (Authorization Code flow).

Encapsula descoberta de endpoints, montagem da URL de autorizacao,
troca de code por tokens, validacao do ID token (JWKS), userinfo
e construcao da URL de RP-Initiated Logout.

Usado exclusivamente pelo app.py (implementacao manual do protocolo).
"""

from __future__ import annotations

import secrets
import time
import urllib.parse

import jwt  # PyJWT
from jwt import PyJWKClient

from config import build_logout_url, http_session, OidcConfig, ssl_context


class OidcClientError(Exception):
    """Erro na comunicacao com o provider OIDC."""


class OidcClient:
    """Cliente OIDC reutilizavel (stateless a nao ser pelo cache de descoberta).

    Uso tipico:
        cfg = OidcConfig.from_env()
        oidc = OidcClient(cfg)

        # /auth
        url, state, nonce = oidc.build_authorization_url()
        # persista state e nonce na sessao, redirecione para url

        # /callback
        result = oidc.exchange_code(code, expected_state, expected_nonce)
        # result["access_token"], result["id_token"], result["id_claims"]

        # /me
        userinfo = oidc.userinfo(access_token)

        # /logout
        logout_url = oidc.build_logout_url(id_token)  # None se provider nao suportar
    """

    DISCOVERY_TTL: float = 600.0  # cache dos metadados por 10 min

    def __init__(self, cfg: OidcConfig) -> None:
        self._cfg = cfg
        self._discovery: dict | None = None
        self._discovery_ts: float = 0.0
        self._jwks_client: PyJWKClient | None = None

    # ------------------------------------------------------------------
    # Descoberta de endpoints (.well-known)
    # ------------------------------------------------------------------

    def discover(self) -> dict:
        """Retorna o documento de descoberta OpenID, cacheado por DISCOVERY_TTL."""
        now = time.monotonic()
        if self._discovery and (now - self._discovery_ts) < self.DISCOVERY_TTL:
            return self._discovery

        url = f"{self._cfg.issuer_url}/.well-known/openid-configuration"
        with http_session(self._cfg) as s:
            resp = s.get(url, timeout=10)
        resp.raise_for_status()

        self._discovery = resp.json()
        self._discovery_ts = now
        return self._discovery

    # -- helpers que acessam campos do discovery -----------------------

    @property
    def authorization_endpoint(self) -> str:
        return self.discover()["authorization_endpoint"]

    @property
    def token_endpoint(self) -> str:
        return self.discover()["token_endpoint"]

    @property
    def userinfo_endpoint(self) -> str:
        return self.discover()["userinfo_endpoint"]

    @property
    def end_session_endpoint(self) -> str | None:
        return self.discover().get("end_session_endpoint")

    @property
    def jwks_uri(self) -> str:
        return self.discover()["jwks_uri"]

    # ------------------------------------------------------------------
    # /auth — montar URL de autorizacao
    # ------------------------------------------------------------------

    def build_authorization_url(self) -> tuple[str, str, str]:
        """Monta a URL de autorizacao e retorna (url, state, nonce).

        O caller deve persistir ``state`` e ``nonce`` na sessao HTTP
        antes de redirecionar o browser.
        """
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)

        params: dict[str, str] = {
            "response_type": "code",  # Authorization Code Flow
            "client_id": self._cfg.client_id,
            "redirect_uri": self._cfg.redirect_uri,
            "scope": "openid profile email",  # openid e OBRIGATORIO p/ OIDC
            "state": state,
            "nonce": nonce,
        }
        url = f"{self.authorization_endpoint}?{urllib.parse.urlencode(params)}"
        return url, state, nonce

    # ------------------------------------------------------------------
    # /callback — trocar code por tokens e validar ID token
    # ------------------------------------------------------------------

    def exchange_code(
        self,
        code: str,
        expected_state: str,
        expected_nonce: str,
    ) -> dict[str, str | dict]:
        """Troca o authorization code por tokens e retorna as claims do ID token.

        Steps:
          1. Verifica ``expected_state`` vs. state recebido (anti-CSRF).
             (NOTA: a verificacao do state pode ficar a cargo do caller;
              este metodo recebe o state ja conferido e foca na troca.)
          2. POST para token_endpoint com code + client authentication.
          3. Valida ID token: assinatura (JWKS), issuer, audience, exp e nonce.
          4. Retorna ``{"access_token": ..., "id_token": ..., "id_claims": ...}``.

        Raises:
            OidcClientError: se a validacao falhar.
            requests.HTTPError: se o token endpoint rejeitar a requisicao.
        """
        # -- monta body e auth -----------------------------------------
        body: dict[str, str] = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self._cfg.redirect_uri,
            "client_id": self._cfg.client_id,
        }
        auth: tuple[str, str] | None = None
        if self._cfg.client_auth_method == "basic":
            auth = (self._cfg.client_id, self._cfg.client_secret)
        else:  # post
            body["client_secret"] = self._cfg.client_secret

        # -- requisicao ao token endpoint ------------------------------
        with http_session(self._cfg) as s:
            resp = s.post(self.token_endpoint, data=body, auth=auth, timeout=10)
        resp.raise_for_status()
        tokens: dict = resp.json()
        id_token: str = tokens["id_token"]
        access_token: str = tokens["access_token"]

        # -- validacao do ID token -------------------------------------
        key = self._get_jwks_client().get_signing_key_from_jwt(id_token).key
        claims: dict = jwt.decode(
            id_token,
            key,
            algorithms=["RS256"],
            audience=self._cfg.client_id,
            issuer=self._cfg.issuer_url,
            options={"require": ["exp", "iss", "aud"]},
        )
        if not secrets.compare_digest(expected_nonce, claims.get("nonce", "")):
            raise OidcClientError("nonce mismatch (possivel replay)")

        return {
            "access_token": access_token,
            "id_token": id_token,
            "id_claims": claims,
        }

    def _get_jwks_client(self) -> PyJWKClient:
        if self._jwks_client is None:
            self._jwks_client = PyJWKClient(
                self.jwks_uri,
                cache_keys=True,
                ssl_context=ssl_context(self._cfg),
            )
        return self._jwks_client

    # ------------------------------------------------------------------
    # /me — userinfo
    # ------------------------------------------------------------------

    def userinfo(self, access_token: str) -> dict:
        """Obtem perfil do usuario no endpoint userinfo (Bearer token)."""
        with http_session(self._cfg) as s:
            resp = s.get(
                self.userinfo_endpoint,
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=10,
            )
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # /logout — RP-Initiated Logout
    # ------------------------------------------------------------------

    def build_logout_url(self, id_token: str) -> str | None:
        """Retorna URL de RP-Initiated Logout ou None se o provider nao suportar."""
        endpoint = self.end_session_endpoint
        if not endpoint:
            return None
        return build_logout_url(endpoint, id_token, self._cfg.post_logout_redirect_uri)
