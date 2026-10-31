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
from typing import FrozenSet, Optional, Sequence, Union
from urllib.parse import urlsplit, urlunsplit

from .pkce import verify_pkce
from .scopes import DEFAULT_SCOPES, NON_PRIVILEGE_SCOPES, parse_scope_string, validate_scopes
from .store import AuthorizationCode, Client, OAuthStore
from .tokens import (
    ACCESS_TOKEN_TTL_SECONDS,
    AUTHZ_CODE_TTL_SECONDS,
    AccessTokenClaims,
    issue_access_token,
    new_authorization_code,
    new_refresh_token,
    now,
    verify_access_token,
    verify_client_secret,
)

ResourceParam = Union[None, str, Sequence[str]]


def canonical_resource(uri: str) -> Optional[str]:
    """Canonical form of an RFC 8707 resource URI, or None if it is not one.

    Absolute http(s) URI without a fragment; scheme and host are lowercased,
    default ports and a trailing slash are dropped, so `https://Host/mcp/` and
    `https://host:443/mcp` both match `https://host/mcp`.
    """
    try:
        parts = urlsplit(uri.strip())
        port = parts.port
    except (AttributeError, ValueError):
        return None
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https") or not parts.hostname or parts.fragment:
        return None
    netloc = parts.hostname.lower()
    if port is not None and port != {"http": 80, "https": 443}[scheme]:
        netloc = f"{netloc}:{port}"
    return urlunsplit((scheme, netloc, parts.path.rstrip("/"), parts.query, ""))


def client_credentials(
    authorization_header: Optional[str],
    form_client_id: Optional[str],
    form_client_secret: Optional[str],
) -> tuple[Optional[str], Optional[str], bool] | AuthorizeError:
    """Extract (client_id, client_secret, used_basic) from a token request.

    Supports ``client_secret_basic`` (RFC 6749 §2.3.1: both parts are
    form-urlencoded, then base64) and ``client_secret_post``. Using both in one
    request is rejected, as §2.3 allows only one authentication method.
    """
    import base64
    import binascii
    from urllib.parse import unquote_plus

    header = (authorization_header or "").strip()
    if not header.lower().startswith("basic "):
        return form_client_id or None, form_client_secret or None, False
    try:
        decoded = base64.b64decode(header[6:].strip(), validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return AuthorizeError("invalid_client", "malformed Basic authorization header", 401)
    if ":" not in decoded:
        return AuthorizeError("invalid_client", "malformed Basic authorization header", 401)
    raw_id, raw_secret = decoded.split(":", 1)
    basic_id, basic_secret = unquote_plus(raw_id), unquote_plus(raw_secret)
    if form_client_secret:
        return AuthorizeError("invalid_request", "use only one client authentication method")
    if form_client_id and form_client_id != basic_id:
        return AuthorizeError("invalid_request", "client_id does not match Basic credentials")
    return basic_id or None, basic_secret or None, True


def _resource_values(resource: ResourceParam) -> tuple[str, ...]:
    if resource is None:
        return ()
    if isinstance(resource, str):
        return (resource,) if resource else ()
    return tuple(r for r in resource if r)


@dataclass
class AuthorizeRequest:
    response_type: str
    client_id: str
    redirect_uri: str
    code_challenge: str
    code_challenge_method: str
    scope: Optional[str] = None
    state: Optional[str] = None
    resource: ResourceParam = None  # RFC 8707; one or more `resource` values


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
    def __init__(
        self,
        store: OAuthStore,
        *,
        resource: Optional[str] = None,
        allowed_scopes: Optional[FrozenSet[str]] = None,
    ):
        """``resource`` is the RFC 8707 resource URI this AS issues tokens for
        (the MCP endpoint). Without it, any request carrying ``resource`` is
        rejected with invalid_target, since the audience cannot be honoured.

        ``allowed_scopes`` narrows the grantable scopes for this deployment
        (e.g. the cloud MCP endpoint never grants instruments:control)."""
        self.store = store
        self.allowed_scopes = allowed_scopes
        self.resource = canonical_resource(resource) if resource else None
        if resource and self.resource is None:
            raise ValueError(f"not an absolute http(s) resource URI: {resource!r}")

    def resolve_resource(self, resource: ResourceParam) -> Optional[str] | AuthorizeError:
        """Validate requested resource indicator(s); None when none was sent."""
        values = _resource_values(resource)
        if not values:
            return None
        for value in values:
            if self.resource is None or canonical_resource(value) != self.resource:
                return AuthorizeError(
                    "invalid_target", f"resource {value!r} is not served by this authorization server"
                )
        return self.resource

    def authenticate_client(
        self, client_id: Optional[str], client_secret: Optional[str]
    ) -> Client | AuthorizeError:
        """Token-endpoint client authentication (RFC 6749 §2.3).

        Confidential clients must present their secret (Basic or POST body;
        the HTTP adapter extracts it). Public clients are identified by
        client_id alone and bound by PKCE; a secret sent by a public client is
        ignored because some hosts (ChatGPT Actions) always send one.
        """
        client = self.store.get_client(client_id or "")
        if client is None:
            return AuthorizeError("invalid_client", "unknown client_id", 401)
        if client.public:
            return client
        if not verify_client_secret(client_secret, client.client_secret_hash):
            return AuthorizeError("invalid_client", "client authentication failed", 401)
        return client

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
        requested = (parse_scope_string(req.scope) - NON_PRIVILEGE_SCOPES) or DEFAULT_SCOPES
        scopes = self._grantable(requested)
        if not scopes:
            return AuthorizeError("invalid_scope", "no recognized scopes requested")
        resource = self.resolve_resource(req.resource)
        if isinstance(resource, AuthorizeError):
            return resource
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
        granted = self._grantable(granted_scopes)
        if not granted:
            return AuthorizeError("invalid_scope", "user granted no scopes")
        resource = self.resolve_resource(req.resource)
        if isinstance(resource, AuthorizeError):
            return resource
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
                resource=resource,
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
        client_secret: Optional[str] = None,
        resource: ResourceParam = None,
    ) -> TokenResponse | AuthorizeError:
        if grant_type == "authorization_code":
            if not (code and redirect_uri and client_id and code_verifier):
                return AuthorizeError("invalid_request", "missing required parameter")
        elif grant_type == "refresh_token":
            if not (refresh_token and client_id):
                return AuthorizeError("invalid_request", "missing required parameter")
        else:
            return AuthorizeError("unsupported_grant_type", f"unknown grant_type {grant_type}")
        client = self.authenticate_client(client_id, client_secret)
        if isinstance(client, AuthorizeError):
            return client
        requested = self.resolve_resource(resource)
        if isinstance(requested, AuthorizeError):
            return requested
        if grant_type == "authorization_code":
            return self._exchange_authz_code(code, redirect_uri, client_id, code_verifier, requested)
        return self._exchange_refresh_token(refresh_token, client_id, requested)

    @staticmethod
    def _bind_resource(
        granted: Optional[str], requested: Optional[str]
    ) -> Optional[str] | AuthorizeError:
        if granted and requested and granted != requested:
            return AuthorizeError("invalid_target", "resource differs from the one authorized")
        return granted or requested

    def _exchange_authz_code(
        self,
        code: Optional[str],
        redirect_uri: Optional[str],
        client_id: Optional[str],
        code_verifier: Optional[str],
        requested_resource: Optional[str] = None,
    ) -> TokenResponse | AuthorizeError:
        rec = self.store.consume_authz_code(code)
        if rec is None:
            return AuthorizeError("invalid_grant", "code unknown, expired, or already used")
        if rec.client_id != client_id:
            return AuthorizeError("invalid_grant", "client_id mismatch")
        if rec.redirect_uri != redirect_uri:
            return AuthorizeError("invalid_grant", "redirect_uri mismatch")
        if not verify_pkce(code_verifier, rec.code_challenge, rec.code_challenge_method):
            return AuthorizeError("invalid_grant", "PKCE verification failed")
        resource = self._bind_resource(rec.resource, requested_resource)
        if isinstance(resource, AuthorizeError):
            return resource
        return self._mint_tokens(
            user_id=rec.user_id,
            client_id=rec.client_id,
            scopes=rec.scopes,
            surface=rec.surface,
            resource=resource,
        )

    def _exchange_refresh_token(
        self,
        refresh_token: Optional[str],
        client_id: Optional[str],
        requested_resource: Optional[str] = None,
    ) -> TokenResponse | AuthorizeError:
        rec = self.store.lookup_refresh_token(refresh_token)
        if rec is None:
            return AuthorizeError("invalid_grant", "refresh token unknown or expired")
        if rec.client_id != client_id:
            return AuthorizeError("invalid_grant", "client_id mismatch")
        resource = self._bind_resource(rec.resource, requested_resource)
        if isinstance(resource, AuthorizeError):
            return resource
        # Rotate: revoke the presented refresh token, issue a new one.
        new_refresh = new_refresh_token()
        self.store.rotate_refresh_token(refresh_token, new_refresh, resource=resource)
        access = self._issue_access(rec.user_id, rec.client_id, rec.scopes, rec.surface, resource)
        return TokenResponse(
            access_token=access,
            refresh_token=new_refresh,
            scope=" ".join(sorted(rec.scopes)),
        )

    def _mint_tokens(
        self,
        *,
        user_id: str,
        client_id: str,
        scopes: FrozenSet[str],
        surface: str,
        resource: Optional[str] = None,
    ) -> TokenResponse:
        refresh = new_refresh_token()
        self.store.save_refresh_token(
            refresh,
            client_id=client_id,
            user_id=user_id,
            scopes=scopes,
            surface=surface,
            resource=resource,
        )
        access = self._issue_access(user_id, client_id, scopes, surface, resource)
        return TokenResponse(
            access_token=access,
            refresh_token=refresh,
            scope=" ".join(sorted(scopes)),
        )

    def _issue_access(
        self,
        user_id: str,
        client_id: str,
        scopes: FrozenSet[str],
        surface: str,
        resource: Optional[str] = None,
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
                resource=resource,
            )
        )

    def _grantable(self, scopes: FrozenSet[str]) -> FrozenSet[str]:
        known = validate_scopes(scopes)
        return known if self.allowed_scopes is None else known & self.allowed_scopes

    # ---- bearer verification (resource servers) ----

    def verify_access(self, token: str, *, resource: Optional[str] = None) -> Optional[AccessTokenClaims]:
        """Verify an access token and reject ones revoked via /oauth/revoke."""
        claims = verify_access_token(token, resource=resource)
        if claims is None or self.store.is_access_token_revoked(claims.jti):
            return None
        return claims

    # ---- /oauth/revoke ----

    def revoke(self, token: str, *, token_type_hint: Optional[str] = None) -> None:
        """RFC 7009 — silently succeeds even if token unknown."""
        self.store.revoke_refresh_token(token)

    def revoke_for_client(
        self, token: str, *, client_id: str, token_type_hint: Optional[str] = None
    ) -> None:
        """RFC 7009 revocation by an authenticated client.

        Revokes a refresh token or an access token, but only one issued to
        ``client_id``. Unknown tokens and tokens of other clients are a silent
        no-op, so the response never reveals whether a token exists.
        """
        kinds = ("access_token", "refresh_token")
        if token_type_hint != "access_token":
            kinds = kinds[::-1]
        for kind in kinds:
            if kind == "refresh_token":
                rec = self.store.lookup_refresh_token(token)
                if rec is not None:
                    if rec.client_id == client_id:
                        self.store.revoke_refresh_token(token)
                    return
            else:
                claims = verify_access_token(token)
                if claims is not None:
                    if claims.client_id == client_id:
                        self.store.revoke_access_token(claims.jti, claims.exp)
                    return

    # ---- /oauth/grants ----

    def list_grants(self, user_id: str) -> list[GrantSummary]:
        rows = self.store.list_user_grants(user_id)
        return [
            GrantSummary(surface=r["surface"], scopes=r["scopes"], expires_at=r["expires_at"])
            for r in rows
        ]

    def revoke_surface(self, user_id: str, surface: str) -> int:
        return self.store.revoke_all_for_surface(user_id, surface)
