"""OAuth 2.1 layer for the remote MCP endpoint.

Drives the framework-agnostic ``oauth_server.oauth.OAuthServer`` (2.1 + PKCE)
from Starlette handlers, and adds the two pieces the connector's OAuth core was
missing for a *remote* MCP endpoint:

* **Dynamic Client Registration** (RFC 7591) at ``POST /oauth/register`` —
  claude.ai registers its client here at connect time.
* **Bearer validation** on ``/mcp`` — a 401 with an RFC 9728
  ``WWW-Authenticate: Bearer resource_metadata=...`` header points unauthenticated
  clients at the protected-resource metadata so they can discover the AS.

Identity: on a valid bearer the JWT ``sub`` (BioMate user_id) and granted scopes
are stashed in a contextvar the tool layer reads, so each call runs as the
authenticated user. See ``identity.py`` for how the user_id maps to a backend
credential.

Local-dev shims (all OFF unless the env var is set):
    BIOMATE_OAUTH_DEV_AUTOCONSENT=1  auto-approve /authorize as BIOMATE_OAUTH_DEV_USER
    BIOMATE_OAUTH_DEV_USER=<id>      the user_id granted in dev auto-consent
"""

from __future__ import annotations

import os
import secrets
import time
from typing import Optional
from urllib.parse import urlencode

from . import bootstrap  # noqa: F401  — sanitizes sys.path before the imports below

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from oauth_server.oauth import (
    AuthorizeError,
    AuthorizeRequest,
    Client,
    OAuthServer,
    OAuthStore,
    SCOPES,
    parse_scope_string,
    validate_scopes,
    verify_access_token,
)

from .identity import set_identity, clear_identity

# ── Singleton server ─────────────────────────────────────────────────────────
_server: Optional[OAuthServer] = None


def server() -> OAuthServer:
    global _server
    if _server is None:
        db_path = os.environ.get("BIOMATE_OAUTH_DB")
        _server = OAuthServer(OAuthStore(db_path) if db_path else OAuthStore())
    return _server


def _bad_request(error: str, desc: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": error, "error_description": desc}, status_code=status)


# ── Dynamic Client Registration — RFC 7591 ───────────────────────────────────
async def register(request: Request) -> Response:
    try:
        body = await request.json()
    except Exception:
        return _bad_request("invalid_client_metadata", "body must be JSON")

    redirect_uris = body.get("redirect_uris")
    if not isinstance(redirect_uris, list) or not redirect_uris:
        return _bad_request("invalid_redirect_uri", "redirect_uris (non-empty array) is required")
    if not all(isinstance(u, str) and u.startswith(("http://", "https://")) for u in redirect_uris):
        return _bad_request("invalid_redirect_uri", "each redirect_uri must be an absolute http(s) URL")

    client_name = str(body.get("client_name") or "MCP Client")
    # Public PKCE client — the only auth method we accept for remote MCP.
    auth_method = str(body.get("token_endpoint_auth_method") or "none")
    if auth_method != "none":
        return _bad_request(
            "invalid_client_metadata",
            "only public PKCE clients (token_endpoint_auth_method=none) are supported",
        )

    client_id = "dcr_" + secrets.token_urlsafe(24)
    surface = "claude" if "claude" in client_name.lower() else "mcp-remote"
    server().store.register_client(
        Client(
            client_id=client_id,
            name=client_name,
            surface=surface,
            redirect_uris=list(redirect_uris),
            public=True,
            client_secret_hash=None,
        )
    )
    return JSONResponse(
        {
            "client_id": client_id,
            "client_id_issued_at": int(time.time()),
            "client_name": client_name,
            "redirect_uris": redirect_uris,
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
            "scope": " ".join(sorted(SCOPES.keys())),
        },
        status_code=201,
    )


# ── Authorization endpoint — RFC 6749 §4.1 + PKCE ────────────────────────────
def _authorize_request(params) -> AuthorizeRequest:
    return AuthorizeRequest(
        response_type=params.get("response_type", ""),
        client_id=params.get("client_id", ""),
        redirect_uri=params.get("redirect_uri", ""),
        code_challenge=params.get("code_challenge", ""),
        code_challenge_method=params.get("code_challenge_method", ""),
        scope=params.get("scope"),
        state=params.get("state"),
    )


def _redirect_with(query: dict, base: str) -> RedirectResponse:
    sep = "&" if "?" in base else "?"
    return RedirectResponse(f"{base}{sep}{urlencode(query)}", status_code=302)


_OAUTH_PARAM_FIELDS = (
    "response_type",
    "client_id",
    "redirect_uri",
    "code_challenge",
    "code_challenge_method",
    "scope",
    "state",
)


def _login_url() -> str:
    """BioMate backend base used to authenticate the user (POST /api/auth/login)."""
    import os as _os

    return _os.environ.get("BIOMATE_API_URL", "https://test.stage-public.biomate.ai").rstrip("/")


def _consent_page(req: AuthorizeRequest, client_name: str, scopes, error: str | None = None) -> HTMLResponse:
    from html import escape

    scope_items = "".join(f"<li>{escape(s)}</li>" for s in sorted(scopes))
    hidden = "".join(
        f'<input type="hidden" name="{f}" value="{escape(getattr(req, f) or "")}">'
        for f in _OAUTH_PARAM_FIELDS
    )
    err_html = f'<p class="err">{escape(error)}</p>' if error else ""
    html = f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Authorize {escape(client_name)} · BioMate</title>
<style>
  body{{font-family:-apple-system,Segoe UI,Roboto,sans-serif;background:#0f1115;color:#e8eaed;
       display:flex;min-height:100vh;align-items:center;justify-content:center;margin:0}}
  .card{{background:#1a1d24;border:1px solid #2a2f3a;border-radius:12px;padding:28px 30px;width:360px;
        box-shadow:0 8px 30px rgba(0,0,0,.4)}}
  h1{{font-size:18px;margin:0 0 4px}} .sub{{color:#9aa0aa;font-size:13px;margin:0 0 18px}}
  ul{{background:#12151b;border-radius:8px;padding:10px 10px 10px 28px;font-size:13px;color:#c7ccd4}}
  label{{display:block;font-size:12px;color:#9aa0aa;margin:12px 0 4px}}
  input[type=email],input[type=password]{{width:100%;box-sizing:border-box;padding:10px;border-radius:8px;
        border:1px solid #2a2f3a;background:#12151b;color:#e8eaed;font-size:14px}}
  .row{{display:flex;gap:10px;margin-top:18px}}
  button{{flex:1;padding:11px;border-radius:8px;border:0;font-size:14px;font-weight:600;cursor:pointer}}
  .allow{{background:#3b82f6;color:#fff}} .deny{{background:#272b33;color:#c7ccd4}}
  .err{{color:#f87171;font-size:13px;margin:10px 0 0}}
  .foot{{color:#6b7280;font-size:11px;margin-top:16px;text-align:center}}
</style></head><body>
<form class="card" method="post" action="/oauth/authorize">
  <h1>Authorize {escape(client_name)}</h1>
  <p class="sub">Sign in to BioMate to grant access</p>
  <div class="sub">This app is requesting:</div>
  <ul>{scope_items}</ul>
  {err_html}
  <label>Email</label><input type="email" name="email" autocomplete="username" required>
  <label>Password</label><input type="password" name="password" autocomplete="current-password" required>
  {hidden}
  <div class="row">
    <button class="deny" name="action" value="deny" type="submit">Deny</button>
    <button class="allow" name="action" value="allow" type="submit">Allow</button>
  </div>
  <div class="foot">You are signing in to mcp.stage-public.biomate.ai</div>
</form></body></html>"""
    return HTMLResponse(html, status_code=200)


def _authorize_error_response(req: AuthorizeRequest, err: AuthorizeError) -> Response:
    """Report an authorize error to the client's redirect_uri if valid, else 400."""
    client = server().store.get_client(req.client_id)
    if client and req.redirect_uri in client.redirect_uris:
        q = {"error": err.error, "error_description": err.error_description}
        if req.state:
            q["state"] = req.state
        return _redirect_with(q, req.redirect_uri)
    return _bad_request(err.error, err.error_description, err.http_status)


async def _biomate_login(email: str, password: str) -> dict:
    """Authenticate against BioMate. Returns {'ok', 'user_id', 'token' | 'error'}."""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=15) as http:
            r = await http.post(
                f"{_login_url()}/api/auth/login",
                json={"email": email, "password": password},
            )
    except Exception as exc:  # network/timeout
        return {"ok": False, "error": f"Could not reach BioMate to sign in ({exc.__class__.__name__})."}
    try:
        body = r.json()
    except Exception:
        body = {}
    if r.status_code == 200 and body.get("success") and body.get("user", {}).get("id") is not None:
        return {"ok": True, "user_id": str(body["user"]["id"]), "token": body.get("token")}
    if body.get("mfaRequired"):
        return {"ok": False, "error": "This account has MFA enabled, which isn't supported via the connector yet."}
    if body.get("error") == "TRIAL_EXPIRED":
        return {"ok": False, "error": "Your BioMate trial has expired. Please subscribe and try again."}
    if r.status_code == 429:
        return {"ok": False, "error": "Too many attempts — please wait a moment and try again."}
    return {"ok": False, "error": "Invalid email or password."}


async def _ensure_user_api_key(user_id: str, session_token: str | None) -> None:
    """Mint + store a per-user bm_live_ API key so tool calls run as this user.

    Idempotent-ish: reuses an already-stored key. Best-effort — a failure here
    leaves the shared-key fallback in place rather than blocking authorization.
    """
    import logging

    import httpx

    from .credentials import has_api_key, put_api_key
    from oauth_server.oauth.tokens import now

    log = logging.getLogger("biomate.remote_mcp.oauth")
    if not session_token or has_api_key(user_id):
        return
    try:
        async with httpx.AsyncClient(timeout=15) as http:
            r = await http.post(
                f"{_login_url()}/api/user/api-keys",
                headers={"Authorization": f"Bearer {session_token}"},
                json={"name": "Claude MCP connector"},
            )
        key = r.json().get("key") if r.status_code == 200 else None
        if key:
            put_api_key(user_id, key, now())
        else:
            log.warning("api-key mint returned no key (status=%s) — using shared key fallback", r.status_code)
    except Exception as exc:  # noqa: BLE001
        log.warning("api-key mint failed (%s) — using shared key fallback", exc.__class__.__name__)


async def authorize(request: Request) -> Response:
    """GET → render login+consent page; POST → authenticate + issue the code.

    This service is itself the OAuth 2.1 authorization server, so it hosts login
    and consent on its own first-party origin and authenticates the user
    server-side against BioMate's POST /api/auth/login. No cross-domain cookie
    or main-app change is required.
    """
    if request.method == "POST":
        form = await request.form()
        req = _authorize_request(form)
    else:
        req = _authorize_request(request.query_params)

    outcome = server().begin_authorize(req)
    if isinstance(outcome, AuthorizeError):
        return _authorize_error_response(req, outcome)

    # Local-dev shortcut only.
    if request.method == "GET" and os.environ.get("BIOMATE_OAUTH_DEV_AUTOCONSENT") == "1":
        user_id = os.environ.get("BIOMATE_OAUTH_DEV_USER", "dev-user")
        return _issue_code(req, user_id, outcome.requested_scopes)

    if request.method == "GET":
        return _consent_page(req, outcome.client_name, outcome.requested_scopes)

    # POST — user submitted the login+consent form.
    form = await request.form()
    if form.get("action") == "deny":
        return _redirect_with(
            {"error": "access_denied", **({"state": req.state} if req.state else {})},
            req.redirect_uri,
        )
    login = await _biomate_login(str(form.get("email", "")), str(form.get("password", "")))
    if not login["ok"]:
        return _consent_page(req, outcome.client_name, outcome.requested_scopes, error=login["error"])
    # Mint + store a per-user API key so tool calls run as this user (best-effort).
    await _ensure_user_api_key(login["user_id"], login.get("token"))
    return _issue_code(req, login["user_id"], outcome.requested_scopes)


def _issue_code(req: AuthorizeRequest, user_id: str, granted_scopes) -> Response:
    code = server().complete_authorize(req, user_id=user_id, granted_scopes=granted_scopes)
    if isinstance(code, AuthorizeError):
        return _bad_request(code.error, code.error_description, code.http_status)
    q = {"code": code}
    if req.state:
        q["state"] = req.state
    return _redirect_with(q, req.redirect_uri)


# ── Token endpoint — RFC 6749 §4.1.3 / §6 ────────────────────────────────────
async def token(request: Request) -> Response:
    form = await request.form()
    grant_type = form.get("grant_type", "")
    result = server().exchange_code(
        grant_type=grant_type,
        code=form.get("code"),
        redirect_uri=form.get("redirect_uri"),
        client_id=form.get("client_id"),
        code_verifier=form.get("code_verifier"),
        refresh_token=form.get("refresh_token"),
    )
    if isinstance(result, AuthorizeError):
        # invalid_grant / invalid_request → 400 per RFC 6749 §5.2
        return _bad_request(result.error, result.error_description, result.http_status)
    payload = {
        "access_token": result.access_token,
        "token_type": result.token_type,
        "expires_in": result.expires_in,
        "scope": result.scope,
    }
    if result.refresh_token:
        payload["refresh_token"] = result.refresh_token
    return JSONResponse(payload, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})


# ── Bearer guard for /mcp ────────────────────────────────────────────────────
def _prm_url() -> str:
    from .app import PUBLIC_URL  # late import to avoid cycle at module load

    return f"{PUBLIC_URL}/.well-known/oauth-protected-resource"


def require_auth_enabled() -> bool:
    return os.environ.get("BIOMATE_MCP_REQUIRE_AUTH", "0") == "1"


def _extract_bearer(scope) -> Optional[str]:
    for k, v in scope.get("headers", []):
        if k == b"authorization":
            val = v.decode("latin-1")
            if val.lower().startswith("bearer "):
                return val[7:].strip()
    return None


class BearerGuard:
    """ASGI wrapper enforcing OAuth bearer auth on the wrapped MCP transport.

    No-op when BIOMATE_MCP_REQUIRE_AUTH != "1" (dev/handshake mode). Otherwise a
    missing/invalid token yields 401 with an RFC 9728 resource_metadata hint.
    """

    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not require_auth_enabled():
            await self.inner(scope, receive, send)
            return

        token_str = _extract_bearer(scope)
        claims = verify_access_token(token_str) if token_str else None
        if claims is None:
            challenge = f'Bearer resource_metadata="{_prm_url()}"'
            body = b'{"error":"invalid_token","error_description":"missing or invalid bearer token"}'
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [
                        (b"www-authenticate", challenge.encode("latin-1")),
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode()),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return

        set_identity(user_id=claims.sub, scopes=frozenset(claims.scopes))
        try:
            await self.inner(scope, receive, send)
        finally:
            clear_identity()
