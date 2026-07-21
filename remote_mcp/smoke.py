"""Local handshake smoke test — proves the Streamable-HTTP transport works.

Connects to a running remote_mcp service with the SDK's own client, performs the
MCP ``initialize`` handshake, and lists tools. ``initialize`` + ``tools/list``
need no BioMate backend, so this validates the transport in isolation (step 1).

    python -m remote_mcp.run &                       # start the server
    python -m remote_mcp.smoke http://127.0.0.1:8848/mcp
"""

from __future__ import annotations

import sys

from . import bootstrap  # noqa: F401

import anyio
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


async def _run(url: str) -> int:
    async with streamablehttp_client(url) as (read, write, _get_session_id):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            print(f"initialize OK — server: {init.serverInfo.name} v{init.serverInfo.version}")
            print(f"protocolVersion: {init.protocolVersion}")
            tools = await session.list_tools()
            print(f"tools/list OK — {len(tools.tools)} tool(s):")
            for t in tools.tools:
                ro = bool(getattr(t.annotations, "readOnlyHint", None)) if t.annotations else False
                print(f"  - {t.name}{'  [read-only]' if ro else ''}")
    return 0


def main() -> None:
    url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8848/mcp"
    raise SystemExit(anyio.run(_run, url))


if __name__ == "__main__":
    main()
