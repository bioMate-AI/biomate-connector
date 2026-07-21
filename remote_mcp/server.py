"""Low-level MCP ``Server`` built from the canonical connector manifest.

This module adapts the *existing* stdio server to the SDK's async Server
contract — it does not reimplement any tool logic:

* tool schemas come from ``tools_manifest.to_mcp()`` (single source of truth)
* tool execution goes through ``biomate_mcp_server.dispatch_tool()`` verbatim

Both are imported as top-level modules thanks to ``remote_mcp.bootstrap``.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import anyio

from . import bootstrap  # noqa: F401  — sanitizes sys.path before the imports below

from mcp.server.lowlevel import Server
import mcp.types as types

import tools_manifest  # connector: mcp/tools_manifest.py
import biomate_mcp_server as stdio  # connector: mcp/biomate_mcp_server.py

from .identity import current_identity, resolve_api_key

log = logging.getLogger("biomate.remote_mcp")

SERVER_NAME = "biomate"
SERVER_VERSION = getattr(stdio, "SERVER_VERSION", "2.0.0")

# Minimum OAuth scope required to invoke each tool. Enforced only when a caller
# identity is present (i.e. bearer auth is on); absent for the no-auth handshake.
_TOOL_SCOPES: dict[str, str] = {
    "search_workflow": "workflows:search",
    "get_workflow_spec": "workflows:search",
    "get_run": "runs:read",
    "list_runs": "runs:read",
    "run_workflow": "runs:write",
    "cancel_run": "runs:write",
    "preview_file": "runs:read",
    "export_report": "reports:export",
    "analyze_results": "runs:read",
    "explain_error": "runs:read",
    "search_literature": "workflows:search",
    "query_database": "workflows:search",
    "resolve_accession": "workflows:search",
    "browse_data": "workflows:search",
    "fetch_public_data": "files:upload",
    "recall_memory": "memory:read",
    "upload_file": "files:upload",
    "biomate_session": "runs:write",  # end-to-end session may start runs
}

# Tools that stream progress (SSE) rather than returning a single sync result.
_STREAMING_TOOLS = {"biomate_session"}

# The remote endpoint starts with the read-only, non-streaming trio so the
# handshake can be proven (step 1) before OAuth + streaming transport land.
# Override with BIOMATE_MCP_TOOLS="a,b,c" or "*" for the full manifest.
_DEFAULT_TOOLS = ("search_workflow", "get_run", "list_runs")


def _all_tool_defs() -> list[dict[str, Any]]:
    return tools_manifest.to_mcp()


def enabled_tool_names() -> set[str]:
    raw = os.environ.get("BIOMATE_MCP_TOOLS", "").strip()
    if raw == "*":
        return {t["name"] for t in _all_tool_defs()}
    if raw:
        return {n.strip() for n in raw.split(",") if n.strip()}
    return set(_DEFAULT_TOOLS)


def _tool_defs() -> list[dict[str, Any]]:
    enabled = enabled_tool_names()
    return [t for t in _all_tool_defs() if t["name"] in enabled]


# Default REST client (no caller identity / auth off) — reads BIOMATE_API_URL /
# BIOMATE_API_KEY from the environment at import time.
_client = stdio.BioMateClient(stdio.BIOMATE_API_URL, stdio.BIOMATE_API_KEY)

# Per-user clients cached by resolved API key so we act as the authenticated
# BioMate user without rebuilding a Session on every call.
_client_cache: dict[str, "stdio.BioMateClient"] = {}


def _client_for_call() -> "stdio.BioMateClient":
    ident = current_identity()
    if ident is None:
        return _client
    api_key = resolve_api_key(ident.user_id)
    if not api_key:
        return _client
    client = _client_cache.get(api_key)
    if client is None:
        client = stdio.BioMateClient(stdio.BIOMATE_API_URL, api_key)
        _client_cache[api_key] = client
    return client


def build_server() -> Server:
    server: Server = Server(name=SERVER_NAME, version=SERVER_VERSION)

    @server.list_tools()
    async def _list_tools() -> list[types.Tool]:
        return [types.Tool.model_validate(t) for t in _tool_defs()]

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict[str, Any]) -> list[types.ContentBlock]:
        if name not in enabled_tool_names():
            raise ValueError(f"Tool not enabled on the remote endpoint: {name}")
        # Enforce scope only when a caller identity is present (bearer auth on).
        ident = current_identity()
        if ident is not None:
            required = _TOOL_SCOPES.get(name)
            if required and required not in ident.scopes:
                raise ValueError(
                    f"insufficient_scope: '{name}' requires the '{required}' scope"
                )
        client = _client_for_call()

        if name in _STREAMING_TOOLS:
            return await _run_streaming(server, client, name, arguments)

        # dispatch_tool is synchronous/blocking (requests); run it off the loop.
        result = await anyio.to_thread.run_sync(
            lambda: stdio.dispatch_tool(client, name, arguments)
        )
        text = json.dumps(result, default=str, ensure_ascii=False)
        return [types.TextContent(type="text", text=text)]

    return server


async def _run_streaming(
    server: Server, client, name: str, arguments: dict[str, Any]
) -> list[types.ContentBlock]:
    """Run biomate_session's SSE stream, forwarding each event as an MCP progress
    notification, then return the aggregated final result.

    The connector's ``open_claw_stream`` is a blocking generator (``requests``
    SSE). We iterate it on a worker thread and bridge each event back to the
    event loop with ``anyio.from_thread.run`` to emit
    ``notifications/progress`` on this request's stream. Mirrors the stdio
    server's SessionRunner, adapted to the SDK session API.
    """
    ctx = server.request_context
    session = ctx.session
    progress_token = ctx.meta.progressToken if ctx.meta else None
    related_id = str(ctx.request_id)

    goal = arguments.get("goal") or arguments.get("session_message") or ""
    inputs = arguments.get("inputs")
    experiment_id = arguments.get("experiment_id")

    final: dict[str, Any] = {"summary_parts": [], "run_id": None, "view_url": None}

    def _emit(n: int, message: str) -> None:
        if progress_token is None:
            return
        anyio.from_thread.run(
            session.send_progress_notification,
            progress_token,
            float(n),
            None,          # total (unknown for an open-ended session)
            message,
            related_id,
        )

    def _worker() -> None:
        n = 0
        for evt in client.open_claw_stream(goal=goal, inputs=inputs, experiment_id=experiment_id):
            edata = evt.get("data")
            if isinstance(edata, dict):
                if edata.get("run_id"):
                    final["run_id"] = edata["run_id"]
                if edata.get("view_url") and not final["view_url"]:
                    final["view_url"] = edata["view_url"]
            payload = stdio._normalize_sse_event(evt)
            if payload is not None:
                n += 1
                if payload.get("view_url") and not final["view_url"]:
                    final["view_url"] = payload["view_url"]
                if payload.get("kind") == "text_delta":
                    final["summary_parts"].append(payload.get("summary_md", ""))
                _emit(n, payload.get("summary_md", ""))
            if evt.get("event") in ("done", "complete"):
                break

    await anyio.to_thread.run_sync(_worker)

    summary = " ".join(final["summary_parts"]).strip() or "Session complete."
    result = {"summary_md": summary, "run_id": final["run_id"], "view_url": final["view_url"]}
    return [types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]
