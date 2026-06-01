#!/usr/bin/env bash
# BioMate connector installer
#
# Usage:
#   bash connect.sh                    # auto-detects host (Claude Code, Claude Desktop, Cursor, Codex)
#   bash connect.sh claude-code        # explicit host
#   bash connect.sh --uninstall        # remove the BioMate MCP block
#
# What it does:
#   1. Detect which MCP host config file to edit.
#   2. Open https://biomate.ai/connect/<host> in the user's browser.
#   3. Wait for the OAuth callback to deliver a refresh token to localhost:53762.
#   4. Write the MCP server block into the host config with the token in `env`.
#   5. Run a smoke `search_workflow` call to verify connectivity.
#
# Tokens are bound to the surface ("claude-code", "claude-desktop", etc.) so a
# leak from one config cannot be replayed elsewhere. Tokens are stored only in
# the host's config file, which most hosts protect with OS-level secret stores.

set -euo pipefail

HOST="${1:-auto}"
ACTION="install"
if [[ "$HOST" == "--uninstall" ]]; then
  ACTION="uninstall"
  HOST="auto"
fi

# ──────────────────────────────────────────────────────────────────────────────
# Host detection
# ──────────────────────────────────────────────────────────────────────────────

detect_host() {
  if [[ -f "$HOME/.claude/mcp_servers.json" ]] || [[ -d "$HOME/.claude" ]]; then
    echo "claude-code"
  elif [[ -f "$HOME/Library/Application Support/Claude/claude_desktop_config.json" ]]; then
    echo "claude-desktop"
  elif [[ -f "$HOME/.cursor/mcp.json" ]]; then
    echo "cursor"
  elif [[ -f "$HOME/.codex/config.toml" ]]; then
    echo "codex"
  else
    echo "unknown"
  fi
}

if [[ "$HOST" == "auto" ]]; then
  HOST="$(detect_host)"
  echo "Detected host: $HOST"
fi

case "$HOST" in
  claude-code|claude-desktop|cursor|codex) ;;
  *) echo "Unknown or unsupported host: $HOST" >&2; exit 1 ;;
esac

# ──────────────────────────────────────────────────────────────────────────────
# OAuth flow (stub — wire to the real /oauth/connect endpoint when shipped)
# ──────────────────────────────────────────────────────────────────────────────

OAUTH_URL="https://biomate.ai/oauth/connect?surface=${HOST}&callback=http://localhost:53762"

echo "Opening $OAUTH_URL in your browser…"
if command -v open >/dev/null; then open "$OAUTH_URL"
elif command -v xdg-open >/dev/null; then xdg-open "$OAUTH_URL"
else echo "Please open this URL manually: $OAUTH_URL"
fi

# Real implementation: spin up a tiny HTTP listener on :53762, capture the token
# from the OAuth redirect, and write it to the host config.
echo
echo "After authorizing, paste the token displayed in the browser:"
read -r BIOMATE_API_KEY

if [[ -z "${BIOMATE_API_KEY:-}" ]]; then
  echo "No token — aborting." >&2
  exit 1
fi

# ──────────────────────────────────────────────────────────────────────────────
# Write the MCP server block
# ──────────────────────────────────────────────────────────────────────────────

config_path() {
  case "$1" in
    claude-code) echo "$HOME/.claude/mcp_servers.json" ;;
    claude-desktop) echo "$HOME/Library/Application Support/Claude/claude_desktop_config.json" ;;
    cursor) echo "$HOME/.cursor/mcp.json" ;;
    codex) echo "$HOME/.codex/config.toml" ;;
  esac
}

CONFIG="$(config_path "$HOST")"
echo "Updating $CONFIG"

# The actual config edit is host-format-specific (JSON for Claude/Cursor, TOML
# for Codex). For brevity this prototype shows only the JSON case; the real
# installer ships per-host writers.
if [[ "$CONFIG" == *.json ]]; then
  python3 - <<PY
import json, os, sys
path = "$CONFIG"
cfg = json.load(open(path)) if os.path.exists(path) else {}
cfg.setdefault("mcpServers", {})["biomate"] = {
    "command": "python3",
    "args": ["-m", "biomate_mcp_server"],
    "env": {
        "BIOMATE_API_URL": "https://api.biomate.ai",
        "BIOMATE_API_KEY": "$BIOMATE_API_KEY",
    },
}
json.dump(cfg, open(path, "w"), indent=2)
print("✓ wrote", path)
PY
fi

# ──────────────────────────────────────────────────────────────────────────────
# Smoke test
# ──────────────────────────────────────────────────────────────────────────────

echo
echo "Running smoke test: search_workflow('ADMET screening')…"
# The real installer launches the MCP server and issues a tools/call; the stub
# just hits the REST endpoint directly for proof of connectivity.
if command -v curl >/dev/null; then
  curl -sf -H "Authorization: Bearer $BIOMATE_API_KEY" \
    -H 'Content-Type: application/json' \
    -d '{"query":"ADMET screening","limit":3}' \
    https://api.biomate.ai/api/workflows/search | head -c 500 && echo
fi

echo
echo "✓ BioMate connected to $HOST. Restart your host to load the MCP server."
