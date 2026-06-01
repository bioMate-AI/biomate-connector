#!/usr/bin/env bash
# Regenerate the connector tools manifest + ChatGPT OpenAPI spec.
#
# Source of truth: backend/lib/mcp/tools_manifest.py
# Outputs:
#   backend/lib/mcp/tools_manifest.json
#   connectors/chatgpt/openapi.json
#
# Run this whenever you edit tools_manifest.py. CI will fail the drift check
# (.github/workflows/tools-manifest-drift.yml) if you forget.

set -euo pipefail
cd "$(dirname "$0")/.."

PYTHONPATH="$(pwd)" python3 -m backend.lib.mcp.tools_manifest

echo
echo "Verifying drift…"
if ! git diff --exit-code --quiet -- backend/lib/mcp/tools_manifest.json connectors/chatgpt/openapi.json; then
  echo "✓ regenerated — git status:"
  git diff --stat -- backend/lib/mcp/tools_manifest.json connectors/chatgpt/openapi.json
  echo "  Don't forget to commit both files."
else
  echo "✓ already in sync."
fi
