"""sys.path sanitation — MUST be imported before any `import mcp` (SDK) call.

The connector ships a directory literally named ``mcp/`` (the canonical stdio
server + tools manifest). That directory shadows the *installed* MCP SDK
package, also named ``mcp``, whenever the repo root (or an empty/``cwd`` entry
that resolves to it) is on ``sys.path`` — which is exactly what happens under
``python -m remote_mcp`` or ``uvicorn`` launched from the repo root.

This module fixes both halves of the collision, idempotently:

1. Remove the repo root (and ``''`` / cwd if they resolve to it) from
   ``sys.path`` so ``import mcp`` binds to the SDK in site-packages, never the
   local ``mcp/`` package. Intra-package imports in ``remote_mcp`` are all
   relative, so they keep working via the package ``__path__`` regardless.
2. Add the connector's ``mcp/`` directory to ``sys.path`` so its
   ``tools_manifest`` and ``biomate_mcp_server`` modules import as *top-level*
   modules (never via the shadowing ``mcp.`` prefix).
"""

from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))           # .../remote_mcp
_REPO_ROOT = os.path.dirname(_HERE)                          # repo root (has mcp/)
_CONNECTOR_MCP = os.path.join(_REPO_ROOT, "mcp")             # .../mcp (the shadow)


def _norm(path: str) -> str:
    return os.path.realpath(os.path.abspath(path or os.getcwd()))


def sanitize() -> None:
    repo_norm = _norm(_REPO_ROOT)
    # 1) Drop any sys.path entry that would let the local `mcp/` package shadow
    #    the installed MCP SDK (repo root, or ''/cwd resolving to it).
    sys.path[:] = [p for p in sys.path if _norm(p) != repo_norm]
    # 2) Expose the connector's manifest + stdio dispatch as top-level modules.
    if _CONNECTOR_MCP not in sys.path:
        sys.path.insert(0, _CONNECTOR_MCP)
    # 3) Re-add the repo root at the *end* — lowest priority — so `oauth_server`
    #    and `remote_mcp` remain importable while `import mcp` still resolves to
    #    the SDK in site-packages (which precedes this entry).
    if _REPO_ROOT not in sys.path:
        sys.path.append(_REPO_ROOT)


sanitize()
