"""JWT access tokens + opaque refresh tokens for BioMate connectors.

Access tokens: signed JWT, 30-minute lifetime, carries user_id + scopes +
surface fingerprint. Verified offline by API gateway.

Refresh tokens: opaque random strings, stored hashed in DB, rotated on use
(RFC 6749 §10.4 best practice / OAuth 2.1 §4.3.1).
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from dataclasses import dataclass
from typing import FrozenSet, Optional

try:
    import jwt as pyjwt  # PyJWT
except ImportError:  # pragma: no cover - dev-time fallback
    pyjwt = None  # type: ignore

ACCESS_TOKEN_TTL_SECONDS = 30 * 60
REFRESH_TOKEN_TTL_SECONDS = 30 * 24 * 60 * 60  # 30 days
AUTHZ_CODE_TTL_SECONDS = 60  # OAuth 2.1 recommends short (≤10 min); we use 60s

# Base audience every access token carries. A token bound to an RFC 8707
# resource additionally lists that resource URI in `aud`.
BASE_AUDIENCE = "biomate-api"
_CLIENT_SECRET_PREFIX = "sha256$"


def _signing_key() -> str:
    key = os.environ.get("BIOMATE_OAUTH_SIGNING_KEY")
    if not key:
        raise RuntimeError("BIOMATE_OAUTH_SIGNING_KEY env var is required")
    return key


@dataclass(frozen=True)
class AccessTokenClaims:
    sub: str  # user_id
    surface: str  # claude-code | cursor | codex | chatgpt | claude-desktop | open-claw
    scopes: FrozenSet[str]
    client_id: str
    iat: int
    exp: int
    jti: str  # for revocation lists if needed
    resource: Optional[str] = None  # RFC 8707 resource the token is bound to


def issue_access_token(claims: AccessTokenClaims) -> str:
    if pyjwt is None:
        raise RuntimeError("PyJWT not installed")
    payload = {
        "sub": claims.sub,
        "surface": claims.surface,
        "scope": " ".join(sorted(claims.scopes)),
        "client_id": claims.client_id,
        "iat": claims.iat,
        "exp": claims.exp,
        "jti": claims.jti,
        "iss": "https://biomate.ai",
        "aud": [BASE_AUDIENCE, claims.resource] if claims.resource else BASE_AUDIENCE,
    }
    return pyjwt.encode(payload, _signing_key(), algorithm="HS256")


def verify_access_token(
    token: str, *, resource: Optional[str] = None
) -> Optional[AccessTokenClaims]:
    """Verify signature, expiry, issuer and the base audience.

    When ``resource`` is given (the caller is a resource server), a token bound
    to a *different* RFC 8707 resource is rejected. Tokens issued without a
    resource indicator carry only the base audience and remain valid.
    """
    if pyjwt is None:
        return None
    try:
        payload = pyjwt.decode(
            token,
            _signing_key(),
            algorithms=["HS256"],
            audience=BASE_AUDIENCE,
            issuer="https://biomate.ai",
        )
    except Exception:
        return None
    aud = payload.get("aud")
    bound = [a for a in (aud if isinstance(aud, list) else [aud]) if a != BASE_AUDIENCE]
    if len(bound) > 1:
        return None
    token_resource = str(bound[0]) if bound else None
    if resource is not None and token_resource is not None and token_resource != resource:
        return None
    try:
        return AccessTokenClaims(
            sub=str(payload["sub"]),
            surface=str(payload["surface"]),
            scopes=frozenset(str(payload.get("scope", "")).split()),
            client_id=str(payload["client_id"]),
            iat=int(payload["iat"]),
            exp=int(payload["exp"]),
            jti=str(payload["jti"]),
            resource=token_resource,
        )
    except (KeyError, TypeError, ValueError):
        return None


def new_refresh_token() -> str:
    """Generate a fresh opaque refresh token (43 chars, url-safe)."""
    return secrets.token_urlsafe(32)


def hash_refresh_token(token: str) -> str:
    """HMAC-SHA256 hash for at-rest storage so a DB leak doesn't expose tokens."""
    return hmac.new(_signing_key().encode(), token.encode(), hashlib.sha256).hexdigest()


def new_client_secret() -> str:
    """Generate a confidential-client secret (256 bits, url-safe)."""
    return secrets.token_urlsafe(32)


def hash_client_secret(secret: str) -> str:
    """At-rest hash for client secrets.

    Secrets are server-generated with 256 bits of entropy, so an unsalted fast
    hash is sufficient. It is deliberately not keyed by the signing key, so
    rotating BIOMATE_OAUTH_SIGNING_KEY does not invalidate registered clients.
    """
    return _CLIENT_SECRET_PREFIX + hashlib.sha256(secret.encode()).hexdigest()


def verify_client_secret(secret: Optional[str], stored_hash: Optional[str]) -> bool:
    if not secret or not stored_hash or not stored_hash.startswith(_CLIENT_SECRET_PREFIX):
        return False
    return hmac.compare_digest(hash_client_secret(secret), stored_hash)


def new_authorization_code() -> str:
    return secrets.token_urlsafe(24)


def now() -> int:
    return int(time.time())
