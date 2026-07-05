"""End-to-end OAuth + transport proof for the remote MCP endpoint.

Exercises the full flow a remote MCP client (claude.ai) performs:

    1. DCR      POST /oauth/register                 -> client_id
    2. PKCE     generate verifier + S256 challenge
    3. authorize GET /oauth/authorize (dev auto-consent) -> 302 ?code=
    4. token    POST /oauth/token (code+verifier)    -> access_token (JWT)
    5. MCP      initialize + tools/list with Bearer  -> OK
    6. negative POST /mcp with no token              -> 401 + WWW-Authenticate

Run against a server started with:
    BIOMATE_MCP_REQUIRE_AUTH=1 BIOMATE_OAUTH_DEV_AUTOCONSENT=1 \
    BIOMATE_OAUTH_DEV_USER=test-user-123 \
    BIOMATE_OAUTH_SIGNING_KEY=... BIOMATE_OAUTH_DB=/tmp/... \
    python -m remote_mcp.run
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import sys
from urllib.parse import parse_qs, urlparse

from . import bootstrap  # noqa: F401

import anyio
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

REDIRECT_URI = "http://localhost:9999/callback"


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:96]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


async def _run(base: str) -> int:
    ok = True
    async with httpx.AsyncClient(follow_redirects=False, timeout=10) as http:
        # 1. DCR
        r = await http.post(
            f"{base}/oauth/register",
            json={"client_name": "Claude (test)", "redirect_uris": [REDIRECT_URI]},
        )
        assert r.status_code == 201, f"register -> {r.status_code} {r.text}"
        client_id = r.json()["client_id"]
        print(f"1. DCR OK           client_id={client_id}")

        # 2. PKCE
        verifier, challenge = _pkce()
        print(f"2. PKCE OK          challenge={challenge[:16]}...")

        # 3. authorize (dev auto-consent) — expect 302 with ?code=
        r = await http.get(
            f"{base}/oauth/authorize",
            params={
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": REDIRECT_URI,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "scope": "runs:read workflows:search",
                "state": "xyz-state",
            },
        )
        assert r.status_code == 302, f"authorize -> {r.status_code} {r.text[:200]}"
        loc = r.headers["location"]
        q = parse_qs(urlparse(loc).query)
        assert q.get("state") == ["xyz-state"], f"state mismatch: {q}"
        code = q["code"][0]
        print(f"3. authorize OK     code={code[:12]}...  state preserved")

        # 4. token exchange
        r = await http.post(
            f"{base}/oauth/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": REDIRECT_URI,
                "client_id": client_id,
                "code_verifier": verifier,
            },
        )
        assert r.status_code == 200, f"token -> {r.status_code} {r.text}"
        tok = r.json()
        access = tok["access_token"]
        assert tok["token_type"] == "Bearer" and tok.get("refresh_token")
        print(f"4. token OK         scope='{tok['scope']}'  refresh_token issued")

        # 6. negative — no bearer must be 401 + resource_metadata hint
        r = await http.post(
            f"{base}/mcp",
            headers={"Accept": "application/json, text/event-stream", "Content-Type": "application/json"},
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                  "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "x", "version": "1"}}},
        )
        www = r.headers.get("www-authenticate", "")
        assert r.status_code == 401 and "resource_metadata=" in www, f"no-auth -> {r.status_code} {www}"
        print(f"6. negative OK      401 + WWW-Authenticate: {www}")

    # 5. MCP handshake WITH bearer
    async with streamablehttp_client(f"{base}/mcp", headers={"Authorization": f"Bearer {access}"}) as (rd, wr, _):
        async with ClientSession(rd, wr) as session:
            init = await session.initialize()
            tools = await session.list_tools()
            print(f"5. MCP OK (bearer)  {init.serverInfo.name} v{init.serverInfo.version} — {len(tools.tools)} tools")

    print("\nALL OAUTH-FLOW CHECKS PASSED" if ok else "\nFAILURES ABOVE")
    return 0 if ok else 1


def main() -> None:
    base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8848"
    raise SystemExit(anyio.run(_run, base.rstrip("/")))


if __name__ == "__main__":
    main()
