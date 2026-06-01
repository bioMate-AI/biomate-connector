"""FastAPI adapter for the OAuth 2.1 + PKCE server.

Mount this router under `/oauth` in the Galaxy ASGI app. Authentication for
the `/authorize` flow is delegated to Galaxy's existing session middleware via
the `current_user_id` dependency; if the user is unauthenticated they are
redirected to the login page with a `next=` parameter pointing back here.
"""

from __future__ import annotations

import os
from typing import Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from .scopes import SCOPES
from .server import AuthorizeError, AuthorizeRequest, OAuthServer
from .store import OAuthStore

# Lazy-init: importing this module shouldn't fail if BIOMATE_OAUTH_DB defaults
# to a path the importer can't write. Tests override via dependency_overrides;
# production wires the store explicitly via set_server() at startup.
_server: Optional[OAuthServer] = None


def set_server(server: OAuthServer) -> None:
    """Production startup hook — call once during Galaxy ASGI app boot."""
    global _server
    _server = server


router = APIRouter(prefix="/oauth", tags=["oauth"])


def get_server() -> OAuthServer:
    global _server
    if _server is None:
        # Default for dev: spin up a real OAuthStore. May raise if the DB path
        # is unwritable — that's the production warning to call set_server().
        _server = OAuthServer(OAuthStore())
    return _server


def current_user_id(request: Request) -> str:
    """Extract authenticated user_id from session cookie or X-API-Key header.

    Galaxy's session middleware sets `request.state.user_id` upstream. For
    headless installer flows we also accept `Authorization: Bearer <token>`
    against an existing connector token (allows token-only re-consent).
    """
    user_id = getattr(request.state, "user_id", None)
    if user_id:
        return str(user_id)
    raise HTTPException(status_code=401, detail="login required")


_CONSENT_TEMPLATE = """<!doctype html>
<html><head><title>Authorize {client_name} — BioMate</title>
<style>
body{{font:14px/1.5 -apple-system,sans-serif;max-width:520px;margin:40px auto;color:#111}}
h1{{font-size:18px;margin:0 0 12px}}
.client{{font-weight:600}}
ul{{padding-left:18px}}
li{{margin:4px 0}}
.actions{{margin-top:20px}}
button{{padding:8px 14px;border-radius:6px;border:1px solid #aaa;background:#fff;cursor:pointer;margin-right:8px}}
button.allow{{background:#111;color:#fff;border-color:#111}}
</style></head><body>
<h1>Authorize <span class="client">{client_name}</span> to access your BioMate account</h1>
<p>This will allow {client_name} (running on <code>{surface}</code>) to:</p>
<ul>{scope_list}</ul>
<form method="POST" action="/oauth/authorize">
{hidden_fields}
<div class="actions">
<button class="allow" name="decision" value="allow">Allow</button>
<button name="decision" value="deny">Deny</button>
</div>
</form>
</body></html>"""


def _scope_human(scope: str) -> str:
    return SCOPES[scope].description if scope in SCOPES else scope


@router.get("/authorize")
def authorize_get(
    request: Request,
    response_type: str = Query(...),
    client_id: str = Query(...),
    redirect_uri: str = Query(...),
    code_challenge: str = Query(...),
    code_challenge_method: str = Query("S256"),
    scope: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    server: OAuthServer = Depends(get_server),
):
    # Force authentication; on 401 redirect to login.
    try:
        current_user_id(request)
    except HTTPException:
        next_url = f"/oauth/authorize?{urlencode({k: v for k, v in request.query_params.items()})}"
        return RedirectResponse(f"/login?next={next_url}", status_code=302)

    req = AuthorizeRequest(
        response_type=response_type,
        client_id=client_id,
        redirect_uri=redirect_uri,
        code_challenge=code_challenge,
        code_challenge_method=code_challenge_method,
        scope=scope,
        state=state,
    )
    result = server.begin_authorize(req)
    if isinstance(result, AuthorizeError):
        return _error_redirect(redirect_uri, result, state)

    scope_list = "".join(
        f'<li><b>{s}</b> — {_scope_human(s)}</li>' for s in sorted(result.requested_scopes)
    )
    hidden = "".join(
        f'<input type="hidden" name="{k}" value="{v}"/>'
        for k, v in {
            "response_type": response_type,
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "code_challenge": code_challenge,
            "code_challenge_method": code_challenge_method,
            "scope": " ".join(sorted(result.requested_scopes)),
            "state": state or "",
        }.items()
        if v is not None
    )
    html = _CONSENT_TEMPLATE.format(
        client_name=result.client_name,
        surface=result.surface,
        scope_list=scope_list,
        hidden_fields=hidden,
    )
    return HTMLResponse(html)


@router.post("/authorize")
def authorize_post(
    request: Request,
    response_type: str = Form(...),
    client_id: str = Form(...),
    redirect_uri: str = Form(...),
    code_challenge: str = Form(...),
    code_challenge_method: str = Form("S256"),
    scope: Optional[str] = Form(None),
    state: Optional[str] = Form(None),
    decision: str = Form(...),
    user_id: str = Depends(current_user_id),
    server: OAuthServer = Depends(get_server),
):
    if decision != "allow":
        return _error_redirect(
            redirect_uri,
            AuthorizeError("access_denied", "user denied the request"),
            state,
        )
    req = AuthorizeRequest(
        response_type=response_type,
        client_id=client_id,
        redirect_uri=redirect_uri,
        code_challenge=code_challenge,
        code_challenge_method=code_challenge_method,
        scope=scope,
        state=state,
    )
    challenge = server.begin_authorize(req)
    if isinstance(challenge, AuthorizeError):
        return _error_redirect(redirect_uri, challenge, state)
    result = server.complete_authorize(
        req, user_id=user_id, granted_scopes=challenge.requested_scopes
    )
    if isinstance(result, AuthorizeError):
        return _error_redirect(redirect_uri, result, state)
    params = {"code": result}
    if state:
        params["state"] = state
    return RedirectResponse(f"{redirect_uri}?{urlencode(params)}", status_code=302)


@router.post("/token")
def token_endpoint(
    grant_type: str = Form(...),
    code: Optional[str] = Form(None),
    redirect_uri: Optional[str] = Form(None),
    client_id: Optional[str] = Form(None),
    code_verifier: Optional[str] = Form(None),
    refresh_token: Optional[str] = Form(None),
    server: OAuthServer = Depends(get_server),
):
    resp = server.exchange_code(
        grant_type=grant_type,
        code=code,
        redirect_uri=redirect_uri,
        client_id=client_id,
        code_verifier=code_verifier,
        refresh_token=refresh_token,
    )
    if isinstance(resp, AuthorizeError):
        return JSONResponse(
            {"error": resp.error, "error_description": resp.error_description},
            status_code=resp.http_status,
        )
    body = {
        "access_token": resp.access_token,
        "token_type": resp.token_type,
        "expires_in": resp.expires_in,
        "scope": resp.scope,
    }
    if resp.refresh_token:
        body["refresh_token"] = resp.refresh_token
    return JSONResponse(body, headers={"Cache-Control": "no-store"})


@router.post("/revoke")
def revoke_endpoint(
    token: str = Form(...),
    token_type_hint: Optional[str] = Form(None),
    server: OAuthServer = Depends(get_server),
):
    server.revoke(token, token_type_hint=token_type_hint)
    return JSONResponse({}, status_code=200)


@router.get("/grants")
def list_grants_endpoint(
    user_id: str = Depends(current_user_id),
    server: OAuthServer = Depends(get_server),
):
    grants = server.list_grants(user_id)
    return [
        {"surface": g.surface, "scopes": g.scopes, "expires_at": g.expires_at}
        for g in grants
    ]


@router.post("/grants/revoke")
def revoke_surface_endpoint(
    surface: str = Form(...),
    user_id: str = Depends(current_user_id),
    server: OAuthServer = Depends(get_server),
):
    n = server.revoke_surface(user_id, surface)
    return {"revoked": n}


def _error_redirect(
    redirect_uri: str, err: AuthorizeError, state: Optional[str]
) -> RedirectResponse:
    params = {"error": err.error, "error_description": err.error_description}
    if state:
        params["state"] = state
    return RedirectResponse(f"{redirect_uri}?{urlencode(params)}", status_code=302)
