"""OAuth 2.1 + PKCE authorization server for BioMate connector surfaces.

Endpoints:
    GET  /oauth/authorize    — consent screen (returns HTML / 302 to login)
    POST /oauth/authorize    — user consents, server issues authorization code
    POST /oauth/token        — exchange code (or refresh token) for access token
    POST /oauth/revoke       — RFC 7009 token revocation
    GET  /oauth/introspect   — RFC 7662 introspection (internal callers only)
    GET  /oauth/grants       — list active grants for the logged-in user
    POST /oauth/grants/revoke — revoke all tokens for a surface

This module is framework-agnostic: it exposes pure functions returning
typed responses. A thin FastAPI / Flask adapter wires HTTP to these.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import FrozenSet, Optional

from .pkce import verify_pkce
from .scopes import DEFAULT_SCOPES, parse_scope_string, validate_scopes
from .store import AuthorizationCode, OAuthStore
from .tokens import (
    ACCESS_TOKEN_TTL_SECONDS,
    AUTHZ_CODE_TTL_SECONDS,
    AccessTokenClaims,
    issue_access_token,
    new_authorization_code,
    new_refresh_token,
    now,
)


@dataclass
class AuthorizeRequest:
    response_type: str
    client_id: str
    redirect_uri: str
    code_challenge: str
    code_challenge_method: str
    scope: Optional[str] = None
    state: Optional[str] = None


@dataclass
class AuthorizeError:
    error: str
    error_description: str
    http_status: int = 400


@dataclass
class AuthorizeChallenge:
    """Returned to the HTTP layer to render a consent page."""

    client_name: str
    surface: str
    requested_scopes: FrozenSet[str]
    state: Optional[str]


@dataclass
class TokenResponse:
    access_token: str
    token_type: str = "Bearer"
    expires_in: int = ACCESS_TOKEN_TTL_SECONDS
    refresh_token: Optional[str] = None
    scope: str = ""


@dataclass
class GrantSummary:
    surface: str
    scopes: list[str]
    expires_at: int


class OAuthServer:
    def __init__(self, store: OAuthStore):
        self.store = store

    # ---- /oauth/authorize ----

    def begin_authorize(self, req: AuthorizeRequest) -> AuthorizeChallenge | AuthorizeError:
        if req.response_type != "code":
            return AuthorizeError("unsupported_response_type", "only `code` is supported")
        client = self.store.get_client(req.client_id)
        if client is None:
            return AuthorizeError("invalid_client", "unknown client_id")
        if req.redirect_uri not in client.redirect_uris:
            return AuthorizeError(
                "invalid_request",
                "redirect_uri not registered for this client",
            )
        if req.code_challenge_method != "S256":
            return AuthorizeError(
                "invalid_request",
                "OAuth 2.1 requires PKCE with method=S256",
            )
        if not (43 <= len(req.code_challenge) <= 128):
            return AuthorizeError(
                "invalid_request",
                "code_challenge length out of range",
            )
        requested = parse_scope_string(req.scope) or DEFAULT_SCOPES
        scopes = validate_scopes(requested)
        if not scopes:
            return AuthorizeError("invalid_scope", "no recognized scopes requested")
        return AuthorizeChallenge(
            client_name=client.name,
            surface=client.surface,
            requested_scopes=scopes,
            state=req.state,
        )

    def complete_authorize(
        self, req: AuthorizeRequest, *, user_id: str, granted_scopes: FrozenSet[str]
    ) -> str | AuthorizeError:
        """Called after the user clicks 'Allow'. Returns the authorization code."""
        client = self.store.get_client(req.client_id)
        if client is None:
            return AuthorizeError("invalid_client", "unknown client_id")
        granted = validate_scopes(granted_scopes)
        if not granted:
            return AuthorizeError("invalid_scope", "user granted no scopes")
        code = new_authorization_code()
        self.store.save_authz_code(
            AuthorizationCode(
                code=code,
                client_id=client.client_id,
                user_id=user_id,
                redirect_uri=req.redirect_uri,
                scopes=granted,
                code_challenge=req.code_challenge,
                code_challenge_method=req.code_challenge_method,
                surface=client.surface,
                expires_at=now() + AUTHZ_CODE_TTL_SECONDS,
            )
        )
        return code

    # ---- /oauth/token ----

    def exchange_code(
        self,
        *,
        grant_type: str,
        code: Optional[str] = None,
        redirect_uri: Optional[str] = None,
        client_id: Optional[str] = None,
        code_verifier: Optional[str] = None,
        refresh_token: Optional[str] = None,
    ) -> TokenResponse | AuthorizeError:
        if grant_type == "authorization_code":
            return self._exchange_authz_code(code, redirect_uri, client_id, code_verifier)
        if grant_type == "refresh_token":
            return self._exchange_refresh_token(refresh_token, client_id)
        return AuthorizeError("unsupported_grant_type", f"unknown grant_type {grant_type}")

    def _exchange_authz_code(
        self,
        code: Optional[str],
        redirect_uri: Optional[str],
        client_id: Optional[str],
        code_verifier: Optional[str],
    ) -> TokenResponse | AuthorizeError:
        if not (code and redirect_uri and client_id and code_verifier):
            return AuthorizeError("invalid_request", "missing required parameter")
        rec = self.store.consume_authz_code(code)
        if rec is None:
            return AuthorizeError("invalid_grant", "code unknown, expired, or already used")
        if rec.client_id != client_id:
            return AuthorizeError("invalid_grant", "client_id mismatch")
        if rec.redirect_uri != redirect_uri:
            return AuthorizeError("invalid_grant", "redirect_uri mismatch")
        if not verify_pkce(code_verifier, rec.code_challenge, rec.code_challenge_method):
            return AuthorizeError("invalid_grant", "PKCE verification failed")
        return self._mint_tokens(
            user_id=rec.user_id,
            client_id=rec.client_id,
            scopes=rec.scopes,
            surface=rec.surface,
        )

    def _exchange_refresh_token(
        self, refresh_token: Optional[str], client_id: Optional[str]
    ) -> TokenResponse | AuthorizeError:
        if not (refresh_token and client_id):
            return AuthorizeError("invalid_request", "missing required parameter")
        rec = self.store.lookup_refresh_token(refresh_token)
        if rec is None:
            return AuthorizeError("invalid_grant", "refresh token unknown or expired")
        if rec.client_id != client_id:
            return AuthorizeError("invalid_grant", "client_id mismatch")
        # Rotate: revoke the presented refresh token, issue a new one.
        new_refresh = new_refresh_token()
        self.store.rotate_refresh_token(refresh_token, new_refresh)
        access = self._issue_access(rec.user_id, rec.client_id, rec.scopes, rec.surface)
        return TokenResponse(
            access_token=access,
            refresh_token=new_refresh,
            scope=" ".join(sorted(rec.scopes)),
        )

    def _mint_tokens(
        self, *, user_id: str, client_id: str, scopes: FrozenSet[str], surface: str
    ) -> TokenResponse:
        refresh = new_refresh_token()
        self.store.save_refresh_token(
            refresh,
            client_id=client_id,
            user_id=user_id,
            scopes=scopes,
            surface=surface,
        )
        access = self._issue_access(user_id, client_id, scopes, surface)
        return TokenResponse(
            access_token=access,
            refresh_token=refresh,
            scope=" ".join(sorted(scopes)),
        )

    def _issue_access(
        self, user_id: str, client_id: str, scopes: FrozenSet[str], surface: str
    ) -> str:
        import secrets as _secrets

        iat = now()
        return issue_access_token(
            AccessTokenClaims(
                sub=user_id,
                surface=surface,
                scopes=scopes,
                client_id=client_id,
                iat=iat,
                exp=iat + ACCESS_TOKEN_TTL_SECONDS,
                jti=_secrets.token_urlsafe(12),
            )
        )

    # ---- /oauth/revoke ----

    def revoke(self, token: str, *, token_type_hint: Optional[str] = None) -> None:
        """RFC 7009 — silently succeeds even if token unknown."""
        self.store.revoke_refresh_token(token)

    # ---- /oauth/grants ----

    def list_grants(self, user_id: str) -> list[GrantSummary]:
        rows = self.store.list_user_grants(user_id)
        return [
            GrantSummary(surface=r["surface"], scopes=r["scopes"], expires_at=r["expires_at"])
            for r in rows
        ]

    def revoke_surface(self, user_id: str, surface: str) -> int:
        return self.store.revoke_all_for_surface(user_id, surface)
