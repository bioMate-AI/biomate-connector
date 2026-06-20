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
        "aud": "biomate-api",
    }
    return pyjwt.encode(payload, _signing_key(), algorithm="HS256")


def verify_access_token(token: str) -> Optional[AccessTokenClaims]:
    if pyjwt is None:
        return None
    try:
        payload = pyjwt.decode(
            token,
            _signing_key(),
            algorithms=["HS256"],
            audience="biomate-api",
            issuer="https://biomate.ai",
        )
    except Exception:
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
        )
    except (KeyError, TypeError, ValueError):
        return None


def new_refresh_token() -> str:
    """Generate a fresh opaque refresh token (43 chars, url-safe)."""
    return secrets.token_urlsafe(32)


def hash_refresh_token(token: str) -> str:
    """HMAC-SHA256 hash for at-rest storage so a DB leak doesn't expose tokens."""
    return hmac.new(_signing_key().encode(), token.encode(), hashlib.sha256).hexdigest()


def new_authorization_code() -> str:
    return secrets.token_urlsafe(24)


def now() -> int:
    return int(time.time())
