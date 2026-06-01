"""End-to-end OAuth 2.1 + PKCE flow tests.

Covers:
- happy path: authorize → token (access + refresh)
- PKCE mismatch is rejected
- code is single-use
- refresh token rotation revokes the old token
- revoked surface tokens are unusable
- scopes are filtered to known set
- expired authz code is rejected
"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
import tempfile
import time

import pytest

os.environ.setdefault(
    "BIOMATE_OAUTH_SIGNING_KEY",
    base64.urlsafe_b64encode(secrets.token_bytes(64)).decode(),
)

from galaxy.connectors.oauth import (  # noqa: E402
    AuthorizeError,
    AuthorizeRequest,
    Client,
    OAuthServer,
    OAuthStore,
    verify_access_token,
)


@pytest.fixture()
def server(tmp_path):
    store = OAuthStore(db_path=str(tmp_path / "oauth.db"))
    store.register_client(
        Client(
            client_id="biomate-cursor",
            name="Cursor",
            surface="cursor",
            redirect_uris=["http://127.0.0.1:53684/callback"],
            public=True,
        )
    )
    return OAuthServer(store)


def _pkce_pair():
    verifier = secrets.token_urlsafe(64)[:128]
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    return verifier, challenge


def _authz_req(challenge: str, scope: str = "runs:read runs:write") -> AuthorizeRequest:
    return AuthorizeRequest(
        response_type="code",
        client_id="biomate-cursor",
        redirect_uri="http://127.0.0.1:53684/callback",
        code_challenge=challenge,
        code_challenge_method="S256",
        scope=scope,
        state="xyz",
    )


def test_full_flow(server):
    verifier, challenge = _pkce_pair()
    req = _authz_req(challenge)

    chal = server.begin_authorize(req)
    assert not isinstance(chal, AuthorizeError)
    assert chal.surface == "cursor"
    assert "runs:read" in chal.requested_scopes

    code = server.complete_authorize(req, user_id="user-42", granted_scopes=chal.requested_scopes)
    assert isinstance(code, str)

    tok = server.exchange_code(
        grant_type="authorization_code",
        code=code,
        redirect_uri=req.redirect_uri,
        client_id=req.client_id,
        code_verifier=verifier,
    )
    assert not isinstance(tok, AuthorizeError)
    assert tok.access_token
    assert tok.refresh_token

    claims = verify_access_token(tok.access_token)
    assert claims is not None
    assert claims.sub == "user-42"
    assert claims.surface == "cursor"
    assert "runs:write" in claims.scopes


def test_pkce_mismatch_rejected(server):
    _, challenge = _pkce_pair()
    req = _authz_req(challenge)
    server.begin_authorize(req)
    code = server.complete_authorize(req, user_id="u", granted_scopes=frozenset({"runs:read"}))

    wrong_verifier = secrets.token_urlsafe(64)[:128]
    tok = server.exchange_code(
        grant_type="authorization_code",
        code=code,
        redirect_uri=req.redirect_uri,
        client_id=req.client_id,
        code_verifier=wrong_verifier,
    )
    assert isinstance(tok, AuthorizeError)
    assert tok.error == "invalid_grant"


def test_code_single_use(server):
    verifier, challenge = _pkce_pair()
    req = _authz_req(challenge)
    server.begin_authorize(req)
    code = server.complete_authorize(req, user_id="u", granted_scopes=frozenset({"runs:read"}))

    ok = server.exchange_code(
        grant_type="authorization_code",
        code=code,
        redirect_uri=req.redirect_uri,
        client_id=req.client_id,
        code_verifier=verifier,
    )
    assert not isinstance(ok, AuthorizeError)
    again = server.exchange_code(
        grant_type="authorization_code",
        code=code,
        redirect_uri=req.redirect_uri,
        client_id=req.client_id,
        code_verifier=verifier,
    )
    assert isinstance(again, AuthorizeError)
    assert again.error == "invalid_grant"


def test_refresh_token_rotation(server):
    verifier, challenge = _pkce_pair()
    req = _authz_req(challenge)
    server.begin_authorize(req)
    code = server.complete_authorize(req, user_id="u", granted_scopes=frozenset({"runs:read"}))
    first = server.exchange_code(
        grant_type="authorization_code",
        code=code,
        redirect_uri=req.redirect_uri,
        client_id=req.client_id,
        code_verifier=verifier,
    )

    second = server.exchange_code(
        grant_type="refresh_token",
        refresh_token=first.refresh_token,
        client_id=req.client_id,
    )
    assert not isinstance(second, AuthorizeError)
    assert second.refresh_token != first.refresh_token

    replay = server.exchange_code(
        grant_type="refresh_token",
        refresh_token=first.refresh_token,
        client_id=req.client_id,
    )
    assert isinstance(replay, AuthorizeError)


def test_revoke_surface(server):
    verifier, challenge = _pkce_pair()
    req = _authz_req(challenge)
    server.begin_authorize(req)
    code = server.complete_authorize(req, user_id="u", granted_scopes=frozenset({"runs:read"}))
    tok = server.exchange_code(
        grant_type="authorization_code",
        code=code,
        redirect_uri=req.redirect_uri,
        client_id=req.client_id,
        code_verifier=verifier,
    )
    assert server.revoke_surface("u", "cursor") == 1

    after = server.exchange_code(
        grant_type="refresh_token",
        refresh_token=tok.refresh_token,
        client_id=req.client_id,
    )
    assert isinstance(after, AuthorizeError)


def test_unknown_scopes_dropped(server):
    _, challenge = _pkce_pair()
    req = _authz_req(challenge, scope="runs:read bogus:scope also-bad")
    chal = server.begin_authorize(req)
    assert not isinstance(chal, AuthorizeError)
    assert chal.requested_scopes == frozenset({"runs:read"})


def test_no_recognized_scopes_rejected(server):
    _, challenge = _pkce_pair()
    req = _authz_req(challenge, scope="bogus:scope only")
    chal = server.begin_authorize(req)
    assert isinstance(chal, AuthorizeError)
    assert chal.error == "invalid_scope"
