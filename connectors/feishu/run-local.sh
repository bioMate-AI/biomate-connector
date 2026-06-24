#!/usr/bin/env bash
# Restart the BioMate Feishu bot locally with the saved environment.
#
# Secrets live in ./.env (gitignored) — this script carries none. The .env was
# captured from the previously-running process (PID 86382, port 8093).
#
# Usage:  ./run-local.sh [--port 8093]
#   Runs in the foreground. To background it:  nohup ./run-local.sh &>/tmp/feishu.log &
set -euo pipefail
cd "$(dirname "$0")"

if [[ ! -f .env ]]; then
  echo "missing .env — restore the Feishu app secrets first" >&2
  exit 1
fi

set -a; source .env; set +a
exec python feishu_bot.py "${@:---port 8093}"
