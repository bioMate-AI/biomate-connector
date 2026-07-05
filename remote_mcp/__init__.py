"""BioMate remote MCP endpoint (Streamable HTTP transport).

A standalone ASGI service that exposes the same BioMate tools as the canonical
stdio connector (``mcp/biomate_mcp_server.py``) over the MCP Streamable-HTTP
transport, so claude.ai / Claude Desktop custom connectors and the Claude
Directory can reach BioMate at a stable HTTPS URL.

It runs as its own process (NOT grafted into Galaxy's FastAPI app) on purpose:
Galaxy's gunicorn recycles its single worker every ~3 min, which would wipe any
in-process MCP session state. This service owns its own lifecycle and reuses the
connector's tool manifest + dispatch verbatim.
"""

from __future__ import annotations

# Fix the `mcp` package-name collision before anything imports the SDK.
from . import bootstrap  # noqa: F401  (import-for-side-effect)

__all__ = ["bootstrap"]
