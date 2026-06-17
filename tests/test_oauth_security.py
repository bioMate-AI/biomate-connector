"""Security tests for the OAuth 2.1 + PKCE server.

Covers scenarios from TEST_PLAN.md §7. Cases for behaviors that aren't yet
implemented (rate limiting §7.10, refresh-family revocation §7.11) are
marked xfail with the issue reference so they're tracked but don't block CI.
"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
import time
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from biomate_connector.oauth import (
    Client,
    OAuthServer,
    OAuthStore,
    verify_access_token,
    issue_access_token,
    AccessTokenClaims,
)
from biomate_connector.oauth import router as oauth_router_mod
from biomate_connector.oauth.tokens import (
    ACCESS_TOKEN_TTL_SECONDS,
    AUTHZ_CODE_TTL_SECONDS,
    now,
)


CURSOR_ID = "biomate-cursor"
CURSOR_URI = "http://127.0.0.1:53684/callback"
CODEX_ID = "biomate-codex"
CODEX_URI = "http://127.0.0.1:53685/callback"


def _pkce_pair() -> tuple[str, str]:
    v = secrets.token_urlsafe(64)[:128]
    c = base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).rstrip(b"=").decode()
    return v, c


@pytest.fixture()
def two_clients_server(tmp_path) -> OAuthServer:
    store = OAuthStore(db_path=str(tmp_path / "oauth.db"))
    store.register_client(Client(CURSOR_ID, "Cursor", "cursor", [CURSOR_URI], True))
    store.register_client(Client(CODEX_ID, "Codex", "codex", [CODEX_URI], True))
    return OAuthServer(store)


@pytest.fixture()
def app_client(two_clients_server, monkeypatch):
    app = FastAPI()
    app.include_router(oauth_router_mod.router)
    app.dependency_overrides[oauth_router_mod.get_server] = lambda: two_clients_server
    app.dependency_overrides[oauth_router_mod.current_user_id] = lambda: "user-42"
    monkeypatch.setattr(oauth_router_mod, "current_user_id", lambda request: "user-42")
    return TestClient(app), two_clients_server


# --- §7.2 — code replay across clients ---


def test_code_issued_for_cursor_rejected_when_presented_by_codex(app_client):
    """Client A obtains a code; client B tries to redeem it.

    The store binds the authorization code to client_id at issue time; redeeming
    with a different client_id returns invalid_grant.
    """
    client, _ = app_client
    v, c = _pkce_pair()
    r = client.post(
        "/oauth/authorize",
        data={
            "response_type": "code",
            "client_id": CURSOR_ID,
            "redirect_uri": CURSOR_URI,
            "code_challenge": c,
            "code_challenge_method": "S256",
            "decision": "allow",
        },
        follow_redirects=False,
    )
    code = parse_qs(urlparse(r.headers["location"]).query)["code"][0]

    r2 = client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": CODEX_URI,
            "client_id": CODEX_ID,
            "code_verifier": v,
        },
    )
    assert r2.status_code == 400
    assert r2.json()["error"] == "invalid_grant"


# --- §7.3 — refresh token leak resistance ---


def test_refresh_tokens_stored_hashed_not_plaintext(two_clients_server, tmp_path):
    """A DB dump must not expose the raw refresh tokens."""
    server = two_clients_server
    v, c = _pkce_pair()
    # Drive a token issue.
    from biomate_connector.oauth import AuthorizeRequest
    req = AuthorizeRequest(
        response_type="code",
        client_id=CURSOR_ID,
        redirect_uri=CURSOR_URI,
        code_challenge=c,
        code_challenge_method="S256",
        scope="runs:read",
    )
    server.begin_authorize(req)
    code = server.complete_authorize(req, user_id="u", granted_scopes=frozenset({"runs:read"}))
    tok = server.exchange_code(
        grant_type="authorization_code",
        code=code,
        redirect_uri=CURSOR_URI,
        client_id=CURSOR_ID,
        code_verifier=v,
    )

    # Inspect the raw DB.
    import sqlite3
    db_path = server.store._db_path
    conn = sqlite3.connect(db_path)
    rows = conn.execute("SELECT token_hash FROM refresh_tokens").fetchall()
    assert rows
    raw_token = tok.refresh_token
    for (stored,) in rows:
        assert stored != raw_token, "raw refresh token must never be stored"
        # The stored hash should be hex (64 chars from HMAC-SHA256).
        assert len(stored) == 64
        int(stored, 16)  # raises if not hex


# --- §7.4 — JWT signing key rotation ---


def test_old_jwt_invalidated_after_signing_key_rotation(monkeypatch):
    """Rotating BIOMATE_OAUTH_SIGNING_KEY invalidates previously issued tokens."""
    claims = AccessTokenClaims(
        sub="u", surface="cursor",
        scopes=frozenset({"runs:read"}), client_id="biomate-cursor",
        iat=now(), exp=now() + 3600, jti=secrets.token_urlsafe(12),
    )
    old_token = issue_access_token(claims)
    assert verify_access_token(old_token) is not None

    # Rotate the signing key.
    new_key = base64.urlsafe_b64encode(secrets.token_bytes(64)).decode()
    monkeypatch.setenv("BIOMATE_OAUTH_SIGNING_KEY", new_key)
    assert verify_access_token(old_token) is None, "rotated key must invalidate old JWTs"


# --- §7.5 — open redirect ---


def test_unregistered_redirect_uri_does_not_leak_code(app_client):
    """Even though the error param is sent to the supplied URI, no code is issued."""
    client, _ = app_client
    _, c = _pkce_pair()
    r = client.get(
        "/oauth/authorize",
        params={
            "response_type": "code",
            "client_id": CURSOR_ID,
            "redirect_uri": "https://attacker.example/cb",
            "code_challenge": c,
            "code_challenge_method": "S256",
        },
        follow_redirects=False,
    )
    assert r.status_code == 302
    qs = parse_qs(urlparse(r.headers["location"]).query)
    assert "error" in qs
    assert "code" not in qs


# --- §7.6 — PKCE downgrade (already covered in router suite, re-asserted here) ---


def test_pkce_plain_method_rejected(app_client):
    client, _ = app_client
    _, c = _pkce_pair()
    r = client.get(
        "/oauth/authorize",
        params={
            "response_type": "code",
            "client_id": CURSOR_ID,
            "redirect_uri": CURSOR_URI,
            "code_challenge": c,
            "code_challenge_method": "plain",
        },
        follow_redirects=False,
    )
    qs = parse_qs(urlparse(r.headers["location"]).query)
    assert qs["error"] == ["invalid_request"]


# --- §7.7 — scope escalation on refresh ---


def test_refresh_cannot_widen_scope(app_client):
    """Refresh tokens are bound to their original scope set; clients cannot
    request 'scope=runs:write' on /oauth/token if they were granted only
    'runs:read'."""
    client, _ = app_client
    v, c = _pkce_pair()
    # Authorize for runs:read only.
    r = client.post(
        "/oauth/authorize",
        data={
            "response_type": "code",
            "client_id": CURSOR_ID,
            "redirect_uri": CURSOR_URI,
            "code_challenge": c,
            "code_challenge_method": "S256",
            "scope": "runs:read",
            "decision": "allow",
        },
        follow_redirects=False,
    )
    code = parse_qs(urlparse(r.headers["location"]).query)["code"][0]
    tok = client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": CURSOR_URI,
            "client_id": CURSOR_ID,
            "code_verifier": v,
        },
    ).json()
    assert "runs:read" in tok["scope"]
    assert "runs:write" not in tok["scope"]
    # Refresh — even if the client asks for more, we return only the bound scope.
    r2 = client.post(
        "/oauth/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": tok["refresh_token"],
            "client_id": CURSOR_ID,
            "scope": "runs:read runs:write memory:write",  # attempt to widen
        },
    )
    assert r2.status_code == 200
    assert "runs:write" not in r2.json()["scope"]


# --- §7.8 — stale authorization code ---


def test_expired_authz_code_rejected(two_clients_server, monkeypatch):
    """Codes are valid for 60 seconds; an older code is rejected even before
    being consumed."""
    server = two_clients_server
    v, c = _pkce_pair()
    from biomate_connector.oauth import AuthorizeRequest
    req = AuthorizeRequest(
        response_type="code", client_id=CURSOR_ID, redirect_uri=CURSOR_URI,
        code_challenge=c, code_challenge_method="S256", scope="runs:read",
    )
    server.begin_authorize(req)
    code = server.complete_authorize(req, user_id="u", granted_scopes=frozenset({"runs:read"}))

    # Fast-forward the clock.
    future = now() + AUTHZ_CODE_TTL_SECONDS + 10
    monkeypatch.setattr("biomate_connector.oauth.store.now", lambda: future)

    result = server.exchange_code(
        grant_type="authorization_code",
        code=code, redirect_uri=CURSOR_URI, client_id=CURSOR_ID, code_verifier=v,
    )
    from biomate_connector.oauth import AuthorizeError
    assert isinstance(result, AuthorizeError)
    assert result.error == "invalid_grant"


# --- §7.9 — surface impersonation ---


def test_access_token_surface_claim_matches_issued_client(app_client):
    """Token's surface claim is set by the server from the client record, not
    user-supplied — so a client can't claim to be a different surface."""
    client, _ = app_client
    v, c = _pkce_pair()
    r = client.post(
        "/oauth/authorize",
        data={
            "response_type": "code",
            "client_id": CURSOR_ID,  # cursor
            "redirect_uri": CURSOR_URI,
            "code_challenge": c,
            "code_challenge_method": "S256",
            "decision": "allow",
        },
        follow_redirects=False,
    )
    code = parse_qs(urlparse(r.headers["location"]).query)["code"][0]
    tok = client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": CURSOR_URI,
            "client_id": CURSOR_ID,
            "code_verifier": v,
        },
    ).json()
    claims = verify_access_token(tok["access_token"])
    assert claims.surface == "cursor", "surface must come from the client record, not request body"


# --- §7.10, §7.11 — unimplemented, tracked as xfail ---


@pytest.mark.xfail(reason="Rate limiting not yet implemented — see TEST_PLAN.md §13 open issue #1")
def test_rate_limit_on_token_endpoint(app_client):
    client, _ = app_client
    for _ in range(100):
        client.post("/oauth/token", data={"grant_type": "authorization_code"})
    r = client.post("/oauth/token", data={"grant_type": "authorization_code"})
    assert r.status_code == 429


@pytest.mark.xfail(reason="Refresh-token family revocation not yet implemented — see TEST_PLAN.md §13 open issue #2")
def test_refresh_token_reuse_detected_revokes_family(app_client):
    """Replaying a rotated (already-used) refresh token should revoke the
    *entire* token family — the attacker may have stolen the token, but so has
    the legitimate client. Killing both forces re-auth and prevents silent
    persistence."""
    client, _ = app_client
    v, c = _pkce_pair()
    r = client.post(
        "/oauth/authorize",
        data={
            "response_type": "code", "client_id": CURSOR_ID, "redirect_uri": CURSOR_URI,
            "code_challenge": c, "code_challenge_method": "S256", "decision": "allow",
        },
        follow_redirects=False,
    )
    code = parse_qs(urlparse(r.headers["location"]).query)["code"][0]
    t1 = client.post("/oauth/token", data={
        "grant_type": "authorization_code", "code": code, "redirect_uri": CURSOR_URI,
        "client_id": CURSOR_ID, "code_verifier": v,
    }).json()
    t2 = client.post("/oauth/token", data={
        "grant_type": "refresh_token", "refresh_token": t1["refresh_token"],
        "client_id": CURSOR_ID,
    }).json()
    # Replay the old token — should kill t2 as well.
    client.post("/oauth/token", data={
        "grant_type": "refresh_token", "refresh_token": t1["refresh_token"],
        "client_id": CURSOR_ID,
    })
    r3 = client.post("/oauth/token", data={
        "grant_type": "refresh_token", "refresh_token": t2["refresh_token"],
        "client_id": CURSOR_ID,
    })
    assert r3.status_code == 400, "the rotated token should also be invalidated"
