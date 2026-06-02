"""
BioMate Coze Plugin
===================
HTTP adapter that exposes BioMate's scientific AI as a Coze plugin.

Coze bots call POST /query → plugin proxies to /api/chat/stream → returns JSON.
No SSE to Coze — it receives a single complete JSON response.

Coze plugin setup (one-time):
    1. Go to https://www.coze.com/open/plugin (or coze.cn for China)
    2. Create plugin → set server URL to https://<your-domain>/coze-plugin
    3. Import openapi.yaml from this directory
    4. Set auth type: Service Level
       Header name:  X-BioMate-Plugin-Key
       Header value: <COZE_PLUGIN_SECRET env var>
    5. Create a Bot → add this plugin → publish to Doubao channel

Environment variables:
    COZE_PLUGIN_SECRET    Shared secret Coze sends in X-BioMate-Plugin-Key header
    BIOMATE_API_URL       BioMate API base URL  (default: http://localhost:5000)
    BIOMATE_API_KEY       BioMate service-account API key
    BIOMATE_DEEP_LINK_BASE  Base URL for "Run in BioMate" deep links
                            (default: https://app.biomate.ai)

API endpoints (defined in openapi.yaml):
    POST /coze-plugin/query   — scientific query → answer + workflow suggestion
    GET  /coze-plugin/health  — liveness check (used by Coze console)
"""

import json
import logging
import os
import threading
import time
import uuid
from collections import deque
from typing import Any, Deque, Dict, List, Optional, Tuple

import requests

log = logging.getLogger(__name__)

COZE_PLUGIN_SECRET = os.environ.get("COZE_PLUGIN_SECRET", "")
BIOMATE_API_URL = os.environ.get("BIOMATE_API_URL", "http://localhost:5000")
BIOMATE_API_KEY = os.environ.get("BIOMATE_API_KEY", "")
BIOMATE_DEEP_LINK_BASE = os.environ.get("BIOMATE_DEEP_LINK_BASE", "https://app.biomate.ai")


# ──────────────────────────────────────────────────────────────────────────────
# Auth
# ──────────────────────────────────────────────────────────────────────────────

def verify_plugin_key(provided_key: str) -> bool:
    """
    Verify the X-BioMate-Plugin-Key header sent by Coze.
    Returns True if the key matches or if no secret is configured (dev mode).
    """
    if not COZE_PLUGIN_SECRET:
        return True  # dev mode — no key required
    return provided_key == COZE_PLUGIN_SECRET


# ──────────────────────────────────────────────────────────────────────────────
# Per-session conversation history
# Keyed by session_id (caller-supplied or auto-generated UUID).
# Keeps last 10 turns for multi-turn continuity.
# ──────────────────────────────────────────────────────────────────────────────

_MAX_HISTORY = 10
_session_history: Dict[str, Deque[Dict[str, str]]] = {}
_history_lock = threading.Lock()


def _get_history(session_id: str) -> List[Dict[str, str]]:
    with _history_lock:
        return list(_session_history.get(session_id, deque()))


def _push_history(session_id: str, role: str, content: str) -> None:
    with _history_lock:
        if session_id not in _session_history:
            _session_history[session_id] = deque(maxlen=_MAX_HISTORY)
        _session_history[session_id].append({"role": role, "content": content})


# ──────────────────────────────────────────────────────────────────────────────
# BioMate chat stream query
# ──────────────────────────────────────────────────────────────────────────────

def chat_stream_query(
    session_id: str,
    query: str,
    api_key: Optional[str] = None,
    timeout: int = 55,
    _base_url_override: Optional[str] = None,
) -> Tuple[str, Optional[str], Optional[str]]:
    """
    Send a query through BioMate's /api/chat/stream endpoint.
    Returns (answer_text, workflow_name_or_None, view_url_or_None).

    SSE event taxonomy for /api/chat/stream:
        event: delta          data: {"text": "..."}          — AI narration chunks
        event: workflow_ready data: {"workflow_name": "..."}  — runnable workflow found
        event: final          data: {"workflow": {...}, ...}  — stream summary
        event: done           data: {}                        — stream ended
    """
    headers: Dict[str, str] = {
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    effective_key = api_key or BIOMATE_API_KEY
    if effective_key:
        headers["Authorization"] = f"Bearer {effective_key}"

    text_parts: List[str] = []
    workflow_name: Optional[str] = None

    base_url = _base_url_override or BIOMATE_API_URL

    history = _get_history(session_id)
    payload: Dict[str, Any] = {"message": query}
    if history:
        payload["context"] = {"priorMessages": history[-6:]}

    try:
        with requests.post(
            f"{base_url}/api/chat/stream",
            json=payload,
            headers=headers,
            stream=True,
            timeout=timeout,
        ) as resp:
            if resp.status_code == 503:
                return "BioMate AI engine is unavailable (API key not configured). Contact admin.", None, None
            if resp.status_code == 400:
                return "Bad request format — please retry.", None, None
            if resp.status_code != 200:
                return f"BioMate returned error {resp.status_code}, please retry later.", None, None

            current_event = "message"
            for raw_line in resp.iter_lines(decode_unicode=True):
                if not raw_line:
                    continue
                if raw_line.startswith(":"):
                    continue
                if raw_line.startswith("event:"):
                    current_event = raw_line[6:].strip()
                    continue
                if raw_line.startswith("data:"):
                    data_str = raw_line[5:].strip()
                    try:
                        data = json.loads(data_str)
                    except json.JSONDecodeError:
                        current_event = "message"
                        continue

                    if current_event == "delta" and isinstance(data, dict):
                        text_parts.append(data.get("text", ""))

                    elif current_event == "workflow_ready" and isinstance(data, dict):
                        workflow_name = (
                            data.get("workflow_name")
                            or data.get("name")
                            or data.get("chain_display_name")
                        )

                    elif current_event == "final" and isinstance(data, dict):
                        if not workflow_name:
                            wf = data.get("workflow") or {}
                            workflow_name = (
                                wf.get("workflow_ga", {}).get("name")
                                or wf.get("workflow_name")
                                or wf.get("chain_display_name")
                            )

                    elif current_event in ("done", "complete"):
                        break

                    current_event = "message"

    except requests.exceptions.Timeout:
        return "BioMate response timed out. Please retry.", None, None
    except Exception as exc:
        log.exception(f"BioMate chat stream query failed for session {session_id}: {exc}")
        return f"BioMate query failed: {exc}", None, None

    answer = "".join(text_parts).strip()
    if not answer:
        answer = "BioMate is processing your request. Check the app for results."

    view_url: Optional[str] = None
    if workflow_name:
        view_url = f"{BIOMATE_DEEP_LINK_BASE}?workflow={workflow_name}"

    _push_history(session_id, "user", query)
    _push_history(session_id, "assistant", answer)

    return answer, workflow_name, view_url


# ──────────────────────────────────────────────────────────────────────────────
# Request / response helpers
# ──────────────────────────────────────────────────────────────────────────────

def handle_query(
    query: str,
    session_id: Optional[str] = None,
    plugin_key: str = "",
    _base_url_override: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Main entry point for the POST /query endpoint.
    Returns a dict that becomes the JSON response body.
    """
    if not verify_plugin_key(plugin_key):
        return {"error": "Unauthorized", "status": 401}

    if not query or not query.strip():
        return {"error": "query is required", "status": 400}

    sid = session_id or str(uuid.uuid4())
    answer, workflow_name, view_url = chat_stream_query(
        sid, query.strip(), _base_url_override=_base_url_override
    )

    result: Dict[str, Any] = {
        "answer": answer,
        "session_id": sid,
    }
    if workflow_name:
        result["workflow_name"] = workflow_name
    if view_url:
        result["view_url"] = view_url

    return result


# ──────────────────────────────────────────────────────────────────────────────
# Flask app (standalone deployment)
# ──────────────────────────────────────────────────────────────────────────────

def create_flask_app():
    """
    Minimal Flask app for the Coze plugin HTTP server.
    Mount at /coze-plugin (or any path matching openapi.yaml servers[0].url).

    GET  /coze-plugin/health  — Coze console validation + uptime monitoring
    POST /coze-plugin/query   — scientific query handler
    """
    from flask import Flask, request, jsonify

    app = Flask("biomate-coze")

    @app.route("/coze-plugin/health", methods=["GET"])
    def health():
        return jsonify({"status": "ok", "service": "biomate-coze-plugin"})

    @app.route("/coze-plugin/query", methods=["POST"])
    def query():
        plugin_key = request.headers.get("X-BioMate-Plugin-Key", "")
        body = request.get_json(silent=True) or {}

        result = handle_query(
            query=body.get("query", ""),
            session_id=body.get("session_id"),
            plugin_key=plugin_key,
        )

        status = result.pop("status", 200)
        return jsonify(result), status

    return app


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="BioMate Coze Plugin server")
    parser.add_argument("--port", type=int, default=8092)
    args = parser.parse_args()
    app = create_flask_app()
    log.warning(f"BioMate Coze plugin listening on port {args.port}")
    app.run(host="0.0.0.0", port=args.port)
