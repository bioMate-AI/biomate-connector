"""HTTP-level OAuth tests for the remote MCP endpoint (remote_mcp.app).

Drives the real Starlette app (DCR → authorize → token → refresh → /mcp) the
way a remote MCP host such as Benchling AI Connectors, claude.ai or Cursor
does. Only the BioMate login step is replaced by the built-in dev
auto-consent switch; everything else is the production code path.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from urllib.parse import parse_qs, urlparse

import remote_mcp.bootstrap  # noqa: F401  (must precede the MCP SDK import)

import pytest
from starlette.testclient import TestClient

import remote_mcp.app as app_mod
import remote_mcp.oauth_app as oauth_app
from oauth_server.oauth import AccessTokenClaims, OAuthServer, OAuthStore, issue_access_token
from oauth_server.oauth.tokens import now

BENCHLING_URI = "https://acme.benchling.com/1/api/llm/mcp/oauth/callback"
RESOURCE = oauth_app.mcp_resource()


@pytest.fixture(scope="module")
def _client():
    # The app's StreamableHTTPSessionManager can run only once per process.
    with TestClient(app_mod.app, follow_redirects=False) as client:
        yield client


@pytest.fixture()
def http(_client, tmp_path, monkeypatch):
    monkeypatch.setenv("BIOMATE_OAUTH_DEV_AUTOCONSENT", "1")
    monkeypatch.setenv("BIOMATE_OAUTH_DEV_USER", "user-77")
    monkeypatch.setenv("BIOMATE_MCP_REQUIRE_AUTH", "1")
    monkeypatch.setattr(
        oauth_app, "_server",
        OAuthServer(OAuthStore(str(tmp_path / "oauth.db")), resource=RESOURCE),
    )
    return _client


def _pkce():
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def _register(http, method=None, redirect=BENCHLING_URI, name="Benchling"):
    body = {"client_name": name, "redirect_uris": [redirect]}
    if method:
        body["token_endpoint_auth_method"] = method
    return http.post("/oauth/register", json=body)


def _authorize(http, client_id, redirect=BENCHLING_URI, resource=None, scope="runs:read offline_access"):
    verifier, challenge = _pkce()
    params = {
        "response_type": "code", "client_id": client_id, "redirect_uri": redirect,
        "code_challenge": challenge, "code_challenge_method": "S256",
        "scope": scope, "state": "st-1",
    }
    if resource:
        params["resource"] = resource
    r = http.get("/oauth/authorize", params=params)
    assert r.status_code == 302, r.text
    q = parse_qs(urlparse(r.headers["location"]).query)
    return q, verifier


def _basic(cid, secret):
    return {"Authorization": "Basic " + base64.b64encode(f"{cid}:{secret}".encode()).decode()}


def _mcp_initialize(http, token):
    return http.post(
        "/mcp",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        },
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "benchling-test", "version": "1"}}},
    )


def test_metadata_advertises_confidential_methods(http):
    meta = http.get("/.well-known/oauth-authorization-server").json()
    assert meta["token_endpoint_auth_methods_supported"] == [
        "none", "client_secret_basic", "client_secret_post",
    ]
    prm = http.get("/.well-known/oauth-protected-resource").json()
    assert prm["resource"] == RESOURCE


def test_dcr_client_secret_basic_full_flow_with_resource(http):
    r = _register(http, "client_secret_basic")
    assert r.status_code == 201, r.text
    reg = r.json()
    assert reg["token_endpoint_auth_method"] == "client_secret_basic"
    assert reg["client_secret"] and reg["client_secret_expires_at"] == 0
    cid, secret = reg["client_id"], reg["client_secret"]
    assert oauth_app.server().store.get_client(cid).surface == "benchling"

    q, verifier = _authorize(http, cid, resource=RESOURCE)
    assert q["state"] == ["st-1"]
    tok = http.post("/oauth/token", headers=_basic(cid, secret), data={
        "grant_type": "authorization_code", "code": q["code"][0],
        "redirect_uri": BENCHLING_URI, "code_verifier": verifier, "resource": RESOURCE,
    })
    assert tok.status_code == 200, tok.text
    body = tok.json()
    assert body["refresh_token"] and body["scope"] == "runs:read"

    assert _mcp_initialize(http, body["access_token"]).status_code == 200

    ref = http.post("/oauth/token", headers=_basic(cid, secret), data={
        "grant_type": "refresh_token", "refresh_token": body["refresh_token"], "resource": RESOURCE,
    })
    assert ref.status_code == 200, ref.text
    assert ref.json()["refresh_token"] != body["refresh_token"]

    wrong = http.post("/oauth/token", headers=_basic(cid, "wrong"), data={
        "grant_type": "refresh_token", "refresh_token": ref.json()["refresh_token"],
    })
    assert wrong.status_code == 401
    assert wrong.json()["error"] == "invalid_client"
    assert wrong.headers["www-authenticate"].startswith("Basic")


def test_dcr_client_secret_post_flow(http):
    reg = _register(http, "client_secret_post").json()
    cid, secret = reg["client_id"], reg["client_secret"]
    q, verifier = _authorize(http, cid)
    data = {"grant_type": "authorization_code", "code": q["code"][0],
            "redirect_uri": BENCHLING_URI, "client_id": cid, "code_verifier": verifier}

    missing = http.post("/oauth/token", data=data)
    assert missing.status_code == 401 and missing.json()["error"] == "invalid_client"

    ok = http.post("/oauth/token", data={**data, "client_secret": secret})
    assert ok.status_code == 200, ok.text
    ref = http.post("/oauth/token", data={
        "grant_type": "refresh_token", "refresh_token": ok.json()["refresh_token"],
        "client_id": cid, "client_secret": secret,
    })
    assert ref.status_code == 200, ref.text


def test_public_pkce_client_still_works_without_resource(http):
    r = _register(http, redirect="http://localhost:9999/callback", name="Claude (test)")
    reg = r.json()
    assert r.status_code == 201
    assert reg["token_endpoint_auth_method"] == "none" and "client_secret" not in reg
    q, verifier = _authorize(http, reg["client_id"], redirect="http://localhost:9999/callback")
    tok = http.post("/oauth/token", data={
        "grant_type": "authorization_code", "code": q["code"][0],
        "redirect_uri": "http://localhost:9999/callback", "client_id": reg["client_id"],
        "code_verifier": verifier,
    })
    assert tok.status_code == 200, tok.text
    assert _mcp_initialize(http, tok.json()["access_token"]).status_code == 200


def test_unsupported_auth_method_rejected(http):
    r = _register(http, "private_key_jwt")
    assert r.status_code == 400 and r.json()["error"] == "invalid_client_metadata"


def test_resource_mismatch_at_authorize_redirects_invalid_target(http):
    reg = _register(http).json()
    q, _ = _authorize(http, reg["client_id"], redirect=BENCHLING_URI,
                      resource="https://attacker.test/mcp")
    assert q["error"] == ["invalid_target"] and q["state"] == ["st-1"]
    assert "code" not in q


def test_resource_mismatch_at_token_rejected(http):
    reg = _register(http).json()
    q, verifier = _authorize(http, reg["client_id"], resource=RESOURCE)
    r = http.post("/oauth/token", data={
        "grant_type": "authorization_code", "code": q["code"][0],
        "redirect_uri": BENCHLING_URI, "client_id": reg["client_id"],
        "code_verifier": verifier, "resource": "https://attacker.test/mcp",
    })
    assert r.status_code == 400 and r.json()["error"] == "invalid_target"


def test_bearer_bound_to_other_resource_rejected_on_mcp(http):
    iat = now()
    foreign = issue_access_token(AccessTokenClaims(
        sub="user-77", surface="x", scopes=frozenset({"runs:read"}), client_id="c",
        iat=iat, exp=iat + 600, jti="j", resource="https://other.test/mcp",
    ))
    r = _mcp_initialize(http, foreign)
    assert r.status_code == 401
    assert "resource_metadata=" in r.headers["www-authenticate"]


# ── RFC 7009 revocation, redirect URI policy, instrument scope ──────────────

def _public_tokens(http, redirect="http://127.0.0.1:5555/callback"):
    reg = _register(http, redirect=redirect, name="Cursor (test)").json()
    q, verifier = _authorize(http, reg["client_id"], redirect=redirect)
    tok = http.post("/oauth/token", data={
        "grant_type": "authorization_code", "code": q["code"][0], "redirect_uri": redirect,
        "client_id": reg["client_id"], "code_verifier": verifier,
    }).json()
    return reg["client_id"], tok


def test_metadata_advertises_revocation(http):
    meta = http.get("/.well-known/oauth-authorization-server").json()
    assert meta["revocation_endpoint"].endswith("/oauth/revoke")
    assert "client_secret_basic" in meta["revocation_endpoint_auth_methods_supported"]


def test_revoke_refresh_token(http):
    cid, tok = _public_tokens(http)
    r = http.post("/oauth/revoke", data={
        "token": tok["refresh_token"], "token_type_hint": "refresh_token", "client_id": cid,
    })
    assert r.status_code == 200
    again = http.post("/oauth/token", data={
        "grant_type": "refresh_token", "refresh_token": tok["refresh_token"], "client_id": cid,
    })
    assert again.status_code == 400 and again.json()["error"] == "invalid_grant"


def test_revoke_access_token_blocks_mcp(http):
    cid, tok = _public_tokens(http)
    assert _mcp_initialize(http, tok["access_token"]).status_code == 200
    # No hint: the server must still recognise it as an access token.
    assert http.post("/oauth/revoke", data={"token": tok["access_token"], "client_id": cid}).status_code == 200
    assert _mcp_initialize(http, tok["access_token"]).status_code == 401


def test_revoke_unknown_token_is_200(http):
    cid, _ = _public_tokens(http)
    assert http.post("/oauth/revoke", data={"token": "not-a-token", "client_id": cid}).status_code == 200


def test_revoke_requires_client_and_ignores_other_clients_tokens(http):
    cid_a, tok_a = _public_tokens(http)
    cid_b, _ = _public_tokens(http, redirect="http://localhost:5556/callback")
    assert http.post("/oauth/revoke", data={"token": tok_a["access_token"]}).status_code == 401
    r = http.post("/oauth/revoke", data={"token": tok_a["access_token"], "client_id": cid_b})
    assert r.status_code == 200
    assert _mcp_initialize(http, tok_a["access_token"]).status_code == 200  # not revoked


def test_revoke_confidential_client_authentication(http):
    reg = _register(http, "client_secret_basic").json()
    cid, secret = reg["client_id"], reg["client_secret"]
    q, verifier = _authorize(http, cid)
    tok = http.post("/oauth/token", headers=_basic(cid, secret), data={
        "grant_type": "authorization_code", "code": q["code"][0],
        "redirect_uri": BENCHLING_URI, "code_verifier": verifier,
    }).json()
    bad = http.post("/oauth/revoke", headers=_basic(cid, "wrong"), data={"token": tok["refresh_token"]})
    assert bad.status_code == 401 and bad.headers["www-authenticate"].startswith("Basic")
    ok = http.post("/oauth/revoke", headers=_basic(cid, secret), data={"token": tok["refresh_token"]})
    assert ok.status_code == 200
    after = http.post("/oauth/token", headers=_basic(cid, secret), data={
        "grant_type": "refresh_token", "refresh_token": tok["refresh_token"],
    })
    assert after.status_code == 400


@pytest.mark.parametrize("uri,ok", [
    ("https://claude.ai/api/mcp/auth_callback", True),
    (BENCHLING_URI, True),
    ("http://127.0.0.1:53684/callback", True),
    ("http://localhost:9999/callback", True),
    ("http://[::1]:9999/callback", True),
    ("http://example.com/callback", False),
    ("http://10.0.0.5/callback", False),
    ("cursor://anysphere.cursor-mcp/oauth/callback", False),
    ("javascript:alert(1)", False),
    ("https://example.com/cb#frag", False),
])
def test_dcr_redirect_uri_policy(http, uri, ok):
    r = _register(http, redirect=uri)
    assert (r.status_code == 201) is ok, r.text
    if not ok:
        assert r.json()["error"] == "invalid_redirect_uri"


def test_instrument_scope_not_advertised_or_granted_in_cloud(http, monkeypatch):
    monkeypatch.delenv("BIOMATE_MCP_ALLOW_LOCAL_TOOLS", raising=False)
    from remote_mcp.local_tools import grantable_scopes

    monkeypatch.setattr(
        oauth_app, "_server",
        OAuthServer(OAuthStore(oauth_app.server().store._db_path), resource=RESOURCE,
                    allowed_scopes=grantable_scopes()),
    )
    assert "instruments:control" not in http.get("/.well-known/oauth-authorization-server").json()["scopes_supported"]
    assert "instruments:control" not in http.get("/.well-known/oauth-protected-resource").json()["scopes_supported"]
    reg = _register(http).json()
    assert "instruments:control" not in reg["scope"].split()
    q, verifier = _authorize(http, reg["client_id"], scope="instruments:control runs:read")
    tok = http.post("/oauth/token", data={
        "grant_type": "authorization_code", "code": q["code"][0], "redirect_uri": BENCHLING_URI,
        "client_id": reg["client_id"], "code_verifier": verifier,
    })
    assert tok.json()["scope"] == "runs:read"
    q, _ = _authorize(http, reg["client_id"], scope="instruments:control")
    assert q["error"] == ["invalid_scope"]
