"""Starlette ASGI app: Streamable-HTTP ``/mcp`` + OAuth discovery + health.

Routes
------
    GET  /healthz                                       liveness
    POST/GET/DELETE /mcp                                MCP Streamable-HTTP transport
    GET  /.well-known/oauth-protected-resource[/mcp]    RFC 9728 (resource metadata)
    GET  /.well-known/oauth-authorization-server        RFC 8414 (AS metadata)

The two ``.well-known`` documents are JSON *stubs* at this stage (step 0/1) —
they establish the routing contract (edge must send these paths here, not to
the SPA) and advertise where the authorization server *will* live. Step 2 wires
their values to the real ``oauth_server`` and adds the ``/oauth/register`` DCR
endpoint + bearer validation on ``/mcp``.
"""

from __future__ import annotations

import contextlib
import os

from . import bootstrap  # noqa: F401  — sanitizes sys.path before the SDK import

from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from .server import SERVER_NAME, SERVER_VERSION, build_server, enabled_tool_names
from oauth_server.oauth import SCOPES

# Public HTTPS origin of THIS resource server (the MCP endpoint). Prod: https://app.biomate.ai
PUBLIC_URL = os.environ.get("BIOMATE_MCP_PUBLIC_URL", "http://localhost:8848").rstrip("/")
# OAuth issuer / authorization-server origin. Defaults to same origin (co-hosted).
AUTH_SERVER_URL = os.environ.get("BIOMATE_OAUTH_ISSUER", PUBLIC_URL).rstrip("/")

# Advertise exactly the scopes the OAuth core recognizes (single source of truth).
SCOPES_SUPPORTED = sorted(SCOPES.keys())


def _security_settings() -> TransportSecuritySettings:
    """DNS-rebinding / Host-header protection for the Streamable-HTTP transport.

    Prod MUST set BIOMATE_MCP_ALLOWED_HOSTS (comma-separated, e.g.
    ``app.biomate.ai``) so the transport only accepts requests whose Host/Origin
    match. When unset we fall back to protection-off for local dev, and log it.
    """
    hosts = os.environ.get("BIOMATE_MCP_ALLOWED_HOSTS", "").strip()
    if not hosts:
        return TransportSecuritySettings(enable_dns_rebinding_protection=False)
    host_list = [h.strip() for h in hosts.split(",") if h.strip()]
    origins: list[str] = []
    for h in host_list:
        origins.append(f"https://{h}")
        origins.append(f"http://{h}")
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=host_list,
        allowed_origins=origins,
    )


_session_manager = StreamableHTTPSessionManager(
    app=build_server(),
    event_store=None,
    json_response=False,   # respond with SSE stream (Claude's client expects this)
    stateless=True,        # each request independent → survives process restarts / scale-out
    security_settings=_security_settings(),
)


async def _handle_mcp(scope, receive, send) -> None:
    await _session_manager.handle_request(scope, receive, send)


# Bearer-guarded MCP transport (no-op unless BIOMATE_MCP_REQUIRE_AUTH=1).
from .oauth_app import BearerGuard  # noqa: E402  (after _handle_mcp is defined)

_guarded_mcp = BearerGuard(_handle_mcp)


async def healthz(request: Request) -> JSONResponse:
    return JSONResponse(
        {
            "ok": True,
            "server": SERVER_NAME,
            "version": SERVER_VERSION,
            "tools": sorted(enabled_tool_names()),
        }
    )


async def protected_resource_metadata(request: Request) -> JSONResponse:
    # RFC 9728 — OAuth 2.0 Protected Resource Metadata.
    return JSONResponse(
        {
            "resource": f"{PUBLIC_URL}/mcp",
            "authorization_servers": [AUTH_SERVER_URL],
            "scopes_supported": SCOPES_SUPPORTED,
            "bearer_methods_supported": ["header"],
            "resource_documentation": "https://github.com/bioMate-AI/biomate-connector",
        }
    )


async def authorization_server_metadata(request: Request) -> JSONResponse:
    # RFC 8414 — OAuth 2.0 Authorization Server Metadata.
    # NOTE: step-0 stub. Endpoints point at where oauth_server will be mounted;
    # values are finalized against the real server in step 2.
    return JSONResponse(
        {
            "issuer": AUTH_SERVER_URL,
            "authorization_endpoint": f"{AUTH_SERVER_URL}/oauth/authorize",
            "token_endpoint": f"{AUTH_SERVER_URL}/oauth/token",
            "registration_endpoint": f"{AUTH_SERVER_URL}/oauth/register",
            "revocation_endpoint": f"{AUTH_SERVER_URL}/oauth/revoke",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none"],
            "scopes_supported": SCOPES_SUPPORTED,
        }
    )


@contextlib.asynccontextmanager
async def _lifespan(app: Starlette):
    async with _session_manager.run():
        yield


from .oauth_app import authorize, register, token  # noqa: E402

_starlette_app = Starlette(
    debug=False,
    lifespan=_lifespan,
    routes=[
        Route("/healthz", healthz, methods=["GET"]),
        Route(
            "/.well-known/oauth-protected-resource",
            protected_resource_metadata,
            methods=["GET"],
        ),
        # Some clients probe the path-suffixed variant per RFC 9728 §3.1.
        Route(
            "/.well-known/oauth-protected-resource/mcp",
            protected_resource_metadata,
            methods=["GET"],
        ),
        Route(
            "/.well-known/oauth-authorization-server",
            authorization_server_metadata,
            methods=["GET"],
        ),
        # OAuth 2.1 endpoints (RFC 6749 + RFC 7591 DCR).
        Route("/oauth/register", register, methods=["POST"]),
        Route("/oauth/authorize", authorize, methods=["GET", "POST"]),
        Route("/oauth/token", token, methods=["POST"]),
    ],
)


async def _dispatch(scope, receive, send) -> None:
    """Top-level ASGI dispatcher.

    ``/mcp`` (with or without a trailing slash) goes straight to the bearer-
    guarded Streamable-HTTP transport — no Starlette ``Mount``, so there is no
    307 ``/mcp`` -> ``/mcp/`` redirect for remote clients to trip over. Every
    other path (health, discovery, OAuth) is handled by the Starlette app, which
    also drives the transport's lifespan (session-manager startup/shutdown).
    """
    if scope["type"] == "http" and scope["path"].rstrip("/") == "/mcp":
        await _guarded_mcp(scope, receive, send)
        return
    await _starlette_app(scope, receive, send)


# Browser-based MCP clients (MCP Inspector, some connector flows) fetch discovery
# + do token exchange cross-origin, and send CORS preflights. Preflight must NOT
# require a bearer, so CORS wraps the whole app (above the /mcp guard). Bearer
# auth carries no cookies, so a wildcard origin is safe (no credentials mode).
from starlette.middleware.cors import CORSMiddleware  # noqa: E402

app = CORSMiddleware(
    _dispatch,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["WWW-Authenticate", "Mcp-Session-Id", "mcp-protocol-version"],
    max_age=600,
)
