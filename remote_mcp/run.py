"""Entry point — ``python -m remote_mcp.run`` (or ``python remote_mcp/run.py``).

We pass the ASGI app *object* to uvicorn (not the ``"remote_mcp.app:app"``
import string) on purpose: ``bootstrap`` removes the repo root from ``sys.path``
to defeat the ``mcp`` shadow, which would break uvicorn re-importing the app by
string. The trade-off is no ``--reload``; irrelevant for a service process.
"""

from __future__ import annotations

import logging
import os

from . import bootstrap  # noqa: F401

import uvicorn

from .app import app


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="[biomate-remote-mcp] %(levelname)s %(name)s %(message)s",
    )
    host = os.environ.get("BIOMATE_MCP_HOST", "127.0.0.1")
    port = int(os.environ.get("BIOMATE_MCP_PORT", "8848"))
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
