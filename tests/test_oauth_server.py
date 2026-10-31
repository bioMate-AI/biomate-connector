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

from biomate_connector.oauth import (  # noqa: E402
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


# ── Confidential clients, RFC 8707 resource, offline_access, Benchling ──────

import base64 as _b64  # noqa: E402
import sqlite3  # noqa: E402

from biomate_connector.oauth import canonical_resource, client_credentials  # noqa: E402
from oauth_server.oauth.admin import register_confidential_client  # noqa: E402
from oauth_server.oauth.scopes import DEFAULT_SCOPES  # noqa: E402

RESOURCE = "https://mcp.example.test/mcp"
BENCHLING_URI = "https://acme.benchling.com/1/api/llm/mcp/oauth/callback"


@pytest.fixture()
def rserver(tmp_path):
    """Server with an MCP resource configured, one public + one confidential client."""
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
    secret = register_confidential_client(
        store, client_id="benchling-acme", name="Benchling (acme)", redirect_uris=[BENCHLING_URI]
    )
    srv = OAuthServer(store, resource=RESOURCE)
    srv.test_secret = secret
    return srv


def _code(srv, *, client_id, redirect_uri, scope="runs:read", resource=None):
    verifier, challenge = _pkce_pair()
    req = AuthorizeRequest(
        response_type="code",
        client_id=client_id,
        redirect_uri=redirect_uri,
        code_challenge=challenge,
        code_challenge_method="S256",
        scope=scope,
        resource=resource,
    )
    chal = srv.begin_authorize(req)
    assert not isinstance(chal, AuthorizeError), chal
    code = srv.complete_authorize(req, user_id="u-1", granted_scopes=chal.requested_scopes)
    assert isinstance(code, str), code
    return code, verifier


def test_confidential_client_code_and_refresh_require_secret(rserver):
    code, verifier = _code(rserver, client_id="benchling-acme", redirect_uri=BENCHLING_URI)
    tok = rserver.exchange_code(
        grant_type="authorization_code", code=code, redirect_uri=BENCHLING_URI,
        client_id="benchling-acme", code_verifier=verifier, client_secret=rserver.test_secret,
    )
    assert not isinstance(tok, AuthorizeError), tok
    assert tok.refresh_token

    for bad in (None, "wrong-secret"):
        r = rserver.exchange_code(
            grant_type="refresh_token", refresh_token=tok.refresh_token,
            client_id="benchling-acme", client_secret=bad,
        )
        assert isinstance(r, AuthorizeError) and r.error == "invalid_client"
        assert r.http_status == 401

    ok = rserver.exchange_code(
        grant_type="refresh_token", refresh_token=tok.refresh_token,
        client_id="benchling-acme", client_secret=rserver.test_secret,
    )
    assert not isinstance(ok, AuthorizeError), ok
    assert ok.refresh_token != tok.refresh_token


def test_confidential_wrong_secret_does_not_burn_code(rserver):
    code, verifier = _code(rserver, client_id="benchling-acme", redirect_uri=BENCHLING_URI)
    bad = rserver.exchange_code(
        grant_type="authorization_code", code=code, redirect_uri=BENCHLING_URI,
        client_id="benchling-acme", code_verifier=verifier, client_secret="nope",
    )
    assert isinstance(bad, AuthorizeError) and bad.error == "invalid_client"
    good = rserver.exchange_code(
        grant_type="authorization_code", code=code, redirect_uri=BENCHLING_URI,
        client_id="benchling-acme", code_verifier=verifier, client_secret=rserver.test_secret,
    )
    assert not isinstance(good, AuthorizeError), good


def test_client_secret_stored_hashed(rserver):
    rec = rserver.store.get_client("benchling-acme")
    assert rec.public is False
    assert rec.client_secret_hash.startswith("sha256$")
    assert rserver.test_secret not in rec.client_secret_hash


def test_public_pkce_client_unaffected_and_ignores_stray_secret(rserver):
    code, verifier = _code(
        rserver, client_id="biomate-cursor", redirect_uri="http://127.0.0.1:53684/callback"
    )
    tok = rserver.exchange_code(
        grant_type="authorization_code", code=code,
        redirect_uri="http://127.0.0.1:53684/callback", client_id="biomate-cursor",
        code_verifier=verifier, client_secret="whatever-chatgpt-sends",
    )
    assert not isinstance(tok, AuthorizeError), tok
    claims = verify_access_token(tok.access_token)
    assert claims.resource is None  # no resource requested → unbound, base audience only


def test_unknown_client_at_token_is_invalid_client(rserver):
    r = rserver.exchange_code(
        grant_type="refresh_token", refresh_token="x", client_id="nobody", client_secret="s"
    )
    assert isinstance(r, AuthorizeError) and r.error == "invalid_client"


def test_benchling_redirect_uri_exact_match(rserver):
    _code(rserver, client_id="benchling-acme", redirect_uri=BENCHLING_URI)
    for other in (
        "https://evil.benchling.com/1/api/llm/mcp/oauth/callback",
        BENCHLING_URI + "/extra",
        BENCHLING_URI.replace("https://", "http://"),
    ):
        _, challenge = _pkce_pair()
        r = rserver.begin_authorize(AuthorizeRequest(
            response_type="code", client_id="benchling-acme", redirect_uri=other,
            code_challenge=challenge, code_challenge_method="S256",
        ))
        assert isinstance(r, AuthorizeError) and r.error == "invalid_request", other


def test_admin_rejects_non_https_redirect(tmp_path):
    store = OAuthStore(db_path=str(tmp_path / "o.db"))
    with pytest.raises(ValueError):
        register_confidential_client(
            store, client_id="x", name="x", redirect_uris=["http://acme.benchling.com/cb"]
        )


def test_resource_absent_matching_and_trailing_slash(rserver):
    code, verifier = _code(
        rserver, client_id="benchling-acme", redirect_uri=BENCHLING_URI,
        resource="https://MCP.example.test:443/mcp/",
    )
    tok = rserver.exchange_code(
        grant_type="authorization_code", code=code, redirect_uri=BENCHLING_URI,
        client_id="benchling-acme", code_verifier=verifier,
        client_secret=rserver.test_secret, resource=RESOURCE,
    )
    assert not isinstance(tok, AuthorizeError), tok
    claims = verify_access_token(tok.access_token, resource=RESOURCE)
    assert claims is not None and claims.resource == RESOURCE
    # A resource server for a different resource must reject the bound token.
    assert verify_access_token(tok.access_token, resource="https://other.test/mcp") is None

    # Refresh without resource keeps the binding.
    ref = rserver.exchange_code(
        grant_type="refresh_token", refresh_token=tok.refresh_token,
        client_id="benchling-acme", client_secret=rserver.test_secret,
    )
    assert verify_access_token(ref.access_token).resource == RESOURCE


def test_resource_mismatch_rejected_at_authorize(rserver):
    _, challenge = _pkce_pair()
    r = rserver.begin_authorize(AuthorizeRequest(
        response_type="code", client_id="benchling-acme", redirect_uri=BENCHLING_URI,
        code_challenge=challenge, code_challenge_method="S256",
        resource="https://attacker.test/mcp",
    ))
    assert isinstance(r, AuthorizeError) and r.error == "invalid_target"


def test_resource_mismatch_rejected_at_token_and_refresh(rserver):
    code, verifier = _code(
        rserver, client_id="benchling-acme", redirect_uri=BENCHLING_URI, resource=RESOURCE
    )
    bad = rserver.exchange_code(
        grant_type="authorization_code", code=code, redirect_uri=BENCHLING_URI,
        client_id="benchling-acme", code_verifier=verifier,
        client_secret=rserver.test_secret, resource="https://other.test/mcp",
    )
    assert isinstance(bad, AuthorizeError) and bad.error == "invalid_target"

    code, verifier = _code(rserver, client_id="benchling-acme", redirect_uri=BENCHLING_URI)
    tok = rserver.exchange_code(
        grant_type="authorization_code", code=code, redirect_uri=BENCHLING_URI,
        client_id="benchling-acme", code_verifier=verifier, client_secret=rserver.test_secret,
    )
    bad_ref = rserver.exchange_code(
        grant_type="refresh_token", refresh_token=tok.refresh_token,
        client_id="benchling-acme", client_secret=rserver.test_secret,
        resource=["https://other.test/mcp"],
    )
    assert isinstance(bad_ref, AuthorizeError) and bad_ref.error == "invalid_target"


def test_resource_rejected_when_server_has_no_resource(server):
    _, challenge = _pkce_pair()
    req = _authz_req(challenge)
    req.resource = RESOURCE
    r = server.begin_authorize(req)
    assert isinstance(r, AuthorizeError) and r.error == "invalid_target"


def test_canonical_resource():
    assert canonical_resource("https://Host.Test/mcp/") == "https://host.test/mcp"
    assert canonical_resource("https://host.test:8443/mcp") == "https://host.test:8443/mcp"
    assert canonical_resource("https://host.test/mcp#frag") is None
    assert canonical_resource("mcp") is None


def test_offline_access_maps_to_refresh_without_extra_scopes(rserver):
    _, challenge = _pkce_pair()
    base = dict(
        response_type="code", client_id="biomate-cursor",
        redirect_uri="http://127.0.0.1:53684/callback",
        code_challenge=challenge, code_challenge_method="S256",
    )
    only = rserver.begin_authorize(AuthorizeRequest(scope="offline_access", **base))
    assert only.requested_scopes == DEFAULT_SCOPES  # same as omitting scope
    mixed = rserver.begin_authorize(AuthorizeRequest(scope="runs:read offline_access", **base))
    assert mixed.requested_scopes == frozenset({"runs:read"})
    # Unknown non-standard scopes alone still fail closed.
    bad = rserver.begin_authorize(AuthorizeRequest(scope="openid admin:all", **base))
    assert isinstance(bad, AuthorizeError) and bad.error == "invalid_scope"


def test_client_credentials_parsing():
    def basic(cid, sec):
        return "Basic " + _b64.b64encode(f"{cid}:{sec}".encode()).decode()

    assert client_credentials(None, "c", "s") == ("c", "s", False)
    assert client_credentials(basic("c", "s%3Ax"), None, None) == ("c", "s:x", True)
    assert client_credentials(basic("c", "s"), "c", None) == ("c", "s", True)
    assert client_credentials(basic("c", "s"), "c", "s").error == "invalid_request"
    assert client_credentials(basic("c", "s"), "other", None).error == "invalid_request"
    assert client_credentials("Basic !!!", None, None).error == "invalid_client"


def test_store_migrates_pre_resource_schema(tmp_path):
    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE clients (client_id TEXT PRIMARY KEY, name TEXT NOT NULL, surface TEXT NOT NULL,
            redirect_uris TEXT NOT NULL, public INTEGER NOT NULL, client_secret_hash TEXT);
        CREATE TABLE authz_codes (code TEXT PRIMARY KEY, client_id TEXT NOT NULL, user_id TEXT NOT NULL,
            redirect_uri TEXT NOT NULL, scopes TEXT NOT NULL, code_challenge TEXT NOT NULL,
            code_challenge_method TEXT NOT NULL, surface TEXT NOT NULL, expires_at INTEGER NOT NULL);
        CREATE TABLE refresh_tokens (token_hash TEXT PRIMARY KEY, client_id TEXT NOT NULL,
            user_id TEXT NOT NULL, scopes TEXT NOT NULL, surface TEXT NOT NULL,
            expires_at INTEGER NOT NULL, revoked INTEGER NOT NULL DEFAULT 0);
        """
    )
    conn.close()
    store = OAuthStore(db_path=str(db))
    store.register_client(Client("biomate-cursor", "Cursor", "cursor",
                                 ["http://127.0.0.1:53684/callback"], True))
    srv = OAuthServer(store, resource=RESOURCE)
    code, verifier = _code(srv, client_id="biomate-cursor",
                           redirect_uri="http://127.0.0.1:53684/callback", resource=RESOURCE)
    tok = srv.exchange_code(grant_type="authorization_code", code=code,
                            redirect_uri="http://127.0.0.1:53684/callback",
                            client_id="biomate-cursor", code_verifier=verifier)
    assert not isinstance(tok, AuthorizeError), tok
    assert verify_access_token(tok.access_token).resource == RESOURCE


# ── Revocation, scope restriction, redirect policy (core) ───────────────────

from biomate_connector.oauth import INSTRUMENT_SCOPE, redirect_uri_problem  # noqa: E402


def _tokens(srv, client_id="biomate-cursor", redirect="http://127.0.0.1:53684/callback", secret=None):
    code, verifier = _code(srv, client_id=client_id, redirect_uri=redirect)
    return srv.exchange_code(grant_type="authorization_code", code=code, redirect_uri=redirect,
                             client_id=client_id, code_verifier=verifier, client_secret=secret)


def test_revoke_for_client_access_and_refresh(rserver):
    tok = _tokens(rserver)
    assert rserver.verify_access(tok.access_token) is not None
    rserver.revoke_for_client(tok.access_token, client_id="biomate-cursor")
    assert rserver.verify_access(tok.access_token) is None
    assert verify_access_token(tok.access_token) is not None  # signature still valid; deny-list decides

    rserver.revoke_for_client(tok.refresh_token, client_id="biomate-cursor", token_type_hint="access_token")
    r = rserver.exchange_code(grant_type="refresh_token", refresh_token=tok.refresh_token,
                              client_id="biomate-cursor")
    assert isinstance(r, AuthorizeError) and r.error == "invalid_grant"


def test_revoke_for_client_ignores_other_clients(rserver):
    tok = _tokens(rserver)
    rserver.revoke_for_client(tok.access_token, client_id="benchling-acme")
    rserver.revoke_for_client(tok.refresh_token, client_id="benchling-acme")
    rserver.revoke_for_client("garbage", client_id="biomate-cursor")
    assert rserver.verify_access(tok.access_token) is not None
    r = rserver.exchange_code(grant_type="refresh_token", refresh_token=tok.refresh_token,
                              client_id="biomate-cursor")
    assert not isinstance(r, AuthorizeError)


def test_revoked_access_list_purges_expired(rserver):
    store = rserver.store
    store.revoke_access_token("old", 1)
    store.revoke_access_token("new", 2**31)
    assert store.is_access_token_revoked("new")
    assert not store.is_access_token_revoked("old")  # purged on the second insert


def test_instrument_scope_not_default_and_restrictable(tmp_path):
    assert INSTRUMENT_SCOPE not in DEFAULT_SCOPES
    store = OAuthStore(db_path=str(tmp_path / "o.db"))
    store.register_client(Client("biomate-cursor", "Cursor", "cursor",
                                 ["http://127.0.0.1:53684/callback"], True))
    full = OAuthServer(store)
    cloud = OAuthServer(store, allowed_scopes=DEFAULT_SCOPES)
    _, challenge = _pkce_pair()
    base = dict(response_type="code", client_id="biomate-cursor",
                redirect_uri="http://127.0.0.1:53684/callback",
                code_challenge=challenge, code_challenge_method="S256")
    assert full.begin_authorize(AuthorizeRequest(**base)).requested_scopes == DEFAULT_SCOPES
    asked = AuthorizeRequest(scope="instruments:control runs:read", **base)
    assert INSTRUMENT_SCOPE in full.begin_authorize(asked).requested_scopes
    assert cloud.begin_authorize(asked).requested_scopes == frozenset({"runs:read"})


def test_redirect_uri_problem():
    assert redirect_uri_problem("https://acme.benchling.com/1/api/llm/mcp/oauth/callback") is None
    assert redirect_uri_problem("http://127.0.0.1:53682/callback") is None
    assert redirect_uri_problem("http://[::1]:1/cb") is None
    for bad in ("http://evil.test/cb", "cursor://x/cb", "https://x/cb#f", "https://x:bad/cb", "", None):
        assert redirect_uri_problem(bad), bad
