#!/bin/bash
# Start BioMate MCP HTTP server (HTTPS) for Claude Science
# Usage: ./start_http_server.sh [--port 8001] [--ssl-cert /path/to.crt --ssl-key /path/to.key]
#
# Required env vars:
#   BIOMATE_AUTH_TOKEN  — your BioMate JWT token
#   BIOMATE_API_URL     — default: https://app.biomate.ai
#
# Claude Science requires HTTPS. By default --ssl is passed and a self-signed
# cert is auto-generated at mcp/localhost.crt + mcp/localhost.key on first run.
# To trust the cert on macOS so the browser warning disappears:
#   sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain mcp/localhost.crt
#
# To connect Claude Science: Settings → MCP Servers → Add → https://localhost:8001/mcp

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if [[ -z "$BIOMATE_AUTH_TOKEN" && -z "$BIOMATE_API_KEY" ]]; then
    echo "ERROR: Set BIOMATE_AUTH_TOKEN or BIOMATE_API_KEY before running."
    echo "  export BIOMATE_AUTH_TOKEN=<your-token>"
    echo "  ./start_http_server.sh"
    exit 1
fi

export BIOMATE_API_URL="${BIOMATE_API_URL:-https://app.biomate.ai}"

PORT="${BIOMATE_MCP_PORT:-8001}"

echo "Starting BioMate MCP HTTPS server..."
echo "  API:  $BIOMATE_API_URL"
echo "  MCP:  https://localhost:${PORT}/mcp"
echo ""
echo "Register in Claude Science:"
echo "  Settings → MCP Servers → Add → https://localhost:${PORT}/mcp"
echo ""

exec python3.11 "$SCRIPT_DIR/biomate_http_server.py" --ssl --port "$PORT" "$@"
