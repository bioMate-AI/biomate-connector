#!/usr/bin/env python3.11
"""
BioMate MCP server — streamable HTTP transport.

Supports Claude Science (local) and ChatGPT / any external MCP client
via HTTPS when deployed on a public server.

Authentication (per-request):
  Priority 1: Authorization: Bearer <token>   ← ChatGPT, external clients
  Priority 2: X-BioMate-Token: <token>        ← alternative header
  Priority 3: BIOMATE_AUTH_TOKEN env var       ← local single-user (Claude Science)

Usage (local, Claude Science):
  BIOMATE_AUTH_TOKEN=<token> python3.11 biomate_http_server.py
  → Register in Claude Science: http://localhost:8001/mcp

Usage (public server, ChatGPT):
  BIOMATE_API_URL=https://app.biomate.ai python3.11 biomate_http_server.py --host 0.0.0.0
  → Register in ChatGPT connector: https://<your-server>/mcp
  → Users authenticate by providing their BioMate token in the connector settings
"""

import argparse
import asyncio
import contextvars
import logging
import os
import sys
import time
from typing import Any, Dict, Optional

import uvicorn
from mcp.server.fastmcp import FastMCP
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from biomate_mcp_server import BioMateClient, SERVER_INSTRUCTIONS  # noqa: E402

log = logging.getLogger("biomate_http")

POLL_INTERVAL = 8
WATCH_TIMEOUT = 600

# Per-request auth token (set by middleware, read by tool handlers)
_request_token: contextvars.ContextVar[str] = contextvars.ContextVar("biomate_token", default="")


class AuthMiddleware(BaseHTTPMiddleware):
    """Extract BioMate auth token from incoming request headers."""

    def __init__(self, app, fallback_token: str = ""):
        super().__init__(app)
        self._fallback = fallback_token

    async def dispatch(self, request: Request, call_next) -> Response:
        # Extract token: Authorization header > X-BioMate-Token > env fallback
        auth_header = request.headers.get("authorization", "")
        if auth_header.lower().startswith("bearer "):
            token = auth_header[7:].strip()
        else:
            token = request.headers.get("x-biomate-token", "").strip() or self._fallback

        if not token:
            return Response(
                '{"error":"Missing BioMate auth token. '
                'Set Authorization: Bearer <token> or configure BIOMATE_AUTH_TOKEN."}',
                status_code=401,
                media_type="application/json",
            )

        _tok = _request_token.set(token)
        try:
            return await call_next(request)
        finally:
            _request_token.reset(_tok)


def _client() -> BioMateClient:
    """Return a BioMateClient authenticated with the current request's token."""
    url   = os.environ.get("BIOMATE_API_URL", "https://app.biomate.ai")
    token = _request_token.get()
    if not token:
        raise RuntimeError("No auth token available for this request.")
    return BioMateClient(url, token)


def _build_mcp(host: str, port: int) -> FastMCP:
    mcp = FastMCP(
        "biomate",
        instructions=SERVER_INSTRUCTIONS,
        host=host,
        port=port,
    )

    @mcp.tool()
    async def search_workflow(query: str, limit: int = 5) -> Dict[str, Any]:
        """Search the BioMate workflow catalog (2,455+ pipelines across 34 domains).
        Returns the best-matching workflow with its full definition, required and optional
        parameters, and scientific rationale."""
        return _client().search_workflow(query=query, limit=limit)

    @mcp.tool()
    async def run_workflow(
        workflow_id: str,
        params: Optional[Dict[str, Any]] = None,
        session_message: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Submit a BioMate workflow to AWS Batch.
        Resolves the workflow definition automatically from its name or ID.
        Returns {runId, status}. Then call watch_run(run_id) to wait for results."""
        return _client().run_workflow(
            workflow_id=workflow_id,
            params=params or {},
            session_message=session_message,
        )

    @mcp.tool()
    async def watch_run(run_id: str) -> Dict[str, Any]:
        """Poll a workflow run until completion, then return all output files and AI findings.
        Returns {run_status, outputs: [{name, extension, size, download_url}], findings}."""
        c = _client()
        deadline = time.time() + WATCH_TIMEOUT
        terminal = {"completed", "failed", "cancelled", "error"}

        while time.time() < deadline:
            try:
                info = c.get_run_status(run_id=run_id)
                status = (info.get("status") or info.get("run_status") or "unknown").lower()
            except Exception as exc:
                log.warning("poll error: %s", exc)
                await asyncio.sleep(POLL_INTERVAL)
                continue

            if status in terminal:
                result: Dict[str, Any] = {"run_status": status}
                try:
                    result["outputs"] = c.get_run_results(run_id=run_id)
                except Exception:
                    result["outputs"] = []
                if status == "completed":
                    try:
                        result["findings"] = c.analyze_results(
                            run_id=run_id, question="Summarize key findings."
                        )
                    except Exception:
                        result["findings"] = {}
                return result

            await asyncio.sleep(POLL_INTERVAL)

        return {"error": True, "code": "TIMEOUT", "run_id": run_id,
                "human_message": f"Timed out after {WATCH_TIMEOUT}s — run may still be in progress."}

    @mcp.tool()
    async def get_run_status(run_id: str) -> Dict[str, Any]:
        """Check the current status of a workflow run.
        Returns {status, progress, metrics}. Status: pending|running|completed|failed."""
        return _client().get_run_status(run_id=run_id)

    @mcp.tool()
    async def get_run_results(run_id: str) -> Any:
        """Retrieve output files for a completed workflow run.
        Returns list of {name, extension, size, download_url}."""
        return _client().get_run_results(run_id=run_id)

    @mcp.tool()
    async def analyze_results(
        run_id: str,
        question: str = "Summarize key findings.",
    ) -> Dict[str, Any]:
        """Ask BioMate AI to interpret a completed run's outputs.
        Returns {analysis: {summary, key_findings, qc_metrics, recommendations}}."""
        return _client().analyze_results(run_id=run_id, question=question)

    @mcp.tool()
    async def list_runs(
        limit: int = 10,
        status: Optional[str] = None,
    ) -> Dict[str, Any]:
        """List your recent workflow runs with status and timestamps.
        Optional status filter: running | completed | failed."""
        return _client().list_runs(limit=limit, status=status or "all")

    @mcp.tool()
    async def upload_file(
        filename: str,
        size_bytes: int,
        content_type: str = "application/octet-stream",
    ) -> Dict[str, Any]:
        """Get a presigned S3 PUT URL for uploading a data file to BioMate.
        Returns {upload_url, s3_key, expires_in}.
        PUT your file bytes directly to upload_url (no auth header needed).
        Then pass the s3_key as a workflow parameter."""
        return _client().upload_signed_url(
            filename=filename, size_bytes=size_bytes, content_type=content_type
        )

    @mcp.tool()
    async def cancel_run(run_id: str) -> Dict[str, Any]:
        """Cancel a running or pending workflow run on AWS Batch."""
        return _client().cancel_run(run_id=run_id)

    return mcp


def main() -> None:
    parser = argparse.ArgumentParser(description="BioMate MCP HTTP server")
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--host", default="127.0.0.1",
                        help="Bind host. Use 0.0.0.0 for public deployment.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    fallback_token = os.environ.get("BIOMATE_AUTH_TOKEN", "")
    api_url = os.environ.get("BIOMATE_API_URL", "https://app.biomate.ai")

    log.info("BioMate MCP HTTP server")
    log.info("  API:  %s", api_url)
    log.info("  MCP:  http://%s:%d/mcp", args.host, args.port)
    if fallback_token:
        log.info("  Auth: env token (fallback for unauthenticated requests)")
    else:
        log.info("  Auth: header-only (Authorization: Bearer <token> required)")

    mcp = _build_mcp(host=args.host, port=args.port)

    # Wrap the Starlette app with auth middleware
    starlette_app = mcp.streamable_http_app()
    from starlette.applications import Starlette
    from starlette.routing import Route, Mount
    import starlette.applications

    # Add auth middleware to the existing app
    starlette_app.add_middleware(AuthMiddleware, fallback_token=fallback_token)

    uvicorn.run(starlette_app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
