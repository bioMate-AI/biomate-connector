#!/usr/bin/env bash
# Start the BioMate ChatGPT adapter pointed at the dev backend.
# Usage: ./start_dev_adapter.sh [port]
#
# After it starts:
#   1. In another terminal: ngrok http ${PORT}
#   2. Copy the ngrok https URL
#   3. Edit openapi-dev.json — replace NGROK_URL_HERE with that URL
#   4. In ChatGPT gpts/editor → Actions → import openapi-dev.json
#   5. Set auth to "API key" / Bearer → value: $BIOMATE_API_KEY below
#   6. Test: "Search BioMate for RNA-seq workflows"

set -euo pipefail

PORT="${1:-8093}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

export BIOMATE_API_URL="${BIOMATE_API_URL:-https://dev-public.biomate.ai}"
export BIOMATE_API_KEY="${BIOMATE_API_KEY:?Set BIOMATE_API_KEY to your dev API key}"
export CHATGPT_ADAPTER_PORT="$PORT"

# Prefer the venv python from the connector repo if present
PYTHON="${REPO_ROOT}/.venv/bin/python3"
if [[ ! -x "$PYTHON" ]]; then
    PYTHON="$(command -v python3)"
fi

echo "Starting BioMate ChatGPT adapter"
echo "  Backend : $BIOMATE_API_URL"
echo "  Port    : $PORT"
echo "  Python  : $PYTHON"
echo ""
echo "Next step: run 'ngrok http $PORT' in another terminal"
echo ""

cd "$SCRIPT_DIR"
"$PYTHON" chatgpt_adapter.py --port "$PORT"
