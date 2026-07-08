#!/bin/bash
# Start BioMate MCP HTTP server for Claude Science / ChatGPT
# Usage: ./start_http_server.sh [--port 8001]
#
# Required env vars (set before running, or edit this file):
#   BIOMATE_API_URL     — default: https://app.biomate.ai
#   BIOMATE_AUTH_TOKEN  — your BioMate JWT token
#
# To connect Claude Science: add URL http://localhost:8001/mcp as a connector

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Credentials — keep out of git
if [[ -z "$BIOMATE_AUTH_TOKEN" && -z "$BIOMATE_API_KEY" ]]; then
    echo "ERROR: Set BIOMATE_AUTH_TOKEN or BIOMATE_API_KEY before running."
    echo "Example:"
    echo "  export BIOMATE_AUTH_TOKEN=<your-token>"
    echo "  ./start_http_server.sh"
    exit 1
fi

export BIOMATE_API_URL="${BIOMATE_API_URL:-https://app.biomate.ai}"

echo "Starting BioMate MCP HTTP server..."
echo "  API:  $BIOMATE_API_URL"
echo "  Port: ${1:-8001}"
echo "  MCP:  http://localhost:${1:-8001}/mcp"
echo ""
echo "Register in Claude Science or ChatGPT:"
echo "  URL: http://localhost:${1:-8001}/mcp"
echo ""

exec python3.11 "$SCRIPT_DIR/biomate_http_server.py" "$@"
