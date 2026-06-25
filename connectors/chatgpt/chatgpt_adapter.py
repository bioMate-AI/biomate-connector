"""
BioMate ChatGPT Actions Adapter
================================
HTTP server that implements the endpoints defined in connectors/chatgpt/openapi.json.
ChatGPT's custom GPT ("Actions") calls these endpoints directly when a user triggers
a tool.

Key design difference from Slack/WeChat/Coze:
  - No SSE to ChatGPT — every endpoint returns synchronous JSON.
  - ChatGPT Actions authenticate via OAuth 2.0 Bearer token (issued by BioMate's
    OAuth server at /oauth/authorize + /oauth/token).
  - biomate_session polls until done so ChatGPT gets a complete result in one call.
    The GPT instructions tell it to show streaming-style updates using the partial
    phase info returned in each poll, but the transport is synchronous.

Endpoints (matching openapi.json):
  POST /tools/biomate_session    — full session: chat → execute → wait for run
  POST /tools/search_workflow    — workflow catalog search
  POST /tools/get_workflow_spec  — workflow parameter schema
  POST /tools/run_workflow       — execute a specific workflow by ID
  POST /tools/get_run            — poll run status + findings
  POST /tools/cancel_run         — cancel a run
  POST /tools/list_runs          — list user's recent runs
  POST /tools/preview_file       — S3 file preview
  POST /tools/export_report      — download/generate report
  POST /tools/analyze_results    — AI interpretation of a run
  POST /tools/explain_error      — diagnose a failed run
  POST /tools/query_database     — look up UniProt / PDB / PubChem / etc.
  POST /tools/recall_memory      — retrieve prior experiment context
  POST /tools/upload_file        — get signed S3 PUT URL

The heavy-lifting tools (biomate_session, run_workflow) proxy through
/api/chat/stream and /api/workflows/execute on the BioMate backend.
Lightweight tools (search_workflow, get_run, list_runs, ...) proxy to the
corresponding BioMate REST endpoints.

Environment variables:
    BIOMATE_API_URL          BioMate backend URL   (default: http://localhost:5000)
    BIOMATE_API_KEY          Service key (dev/test; in prod auth comes from user OAuth token)
    CHATGPT_ADAPTER_PORT     Port for this server  (default: 8093)
"""

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple

import requests

log = logging.getLogger(__name__)

BIOMATE_API_URL = os.environ.get("BIOMATE_API_URL", "http://localhost:5000")
BIOMATE_API_KEY = os.environ.get("BIOMATE_API_KEY", "")


# ──────────────────────────────────────────────────────────────────────────────
# Auth helpers
# ──────────────────────────────────────────────────────────────────────────────

def _extract_bearer(auth_header: str) -> str:
    """Extract token from 'Bearer <token>' header."""
    if auth_header and auth_header.startswith("Bearer "):
        return auth_header[7:].strip()
    return ""


def _biomate_headers(token: str) -> Dict[str, str]:
    """Build headers for a BioMate API call, preferring caller's OAuth token."""
    effective = token or BIOMATE_API_KEY
    h = {"Content-Type": "application/json"}
    if effective:
        h["Authorization"] = f"Bearer {effective}"
    return h


# ──────────────────────────────────────────────────────────────────────────────
# Core: chat stream → synchronous JSON reply
# Reuses the SSE-parsing pattern from coze_plugin / slack_bot.
# ──────────────────────────────────────────────────────────────────────────────

def _consume_chat_stream(
    message: str,
    token: str,
    prior_messages: Optional[List[Dict]] = None,
    base_url: Optional[str] = None,
) -> Tuple[str, Optional[str], Optional[str]]:
    """
    POST to /api/chat/stream, consume SSE to completion.
    Returns (answer_text, workflow_name, view_url).
    """
    url = f"{base_url or BIOMATE_API_URL}/api/chat/stream"
    headers = {**_biomate_headers(token), "Accept": "text/event-stream"}
    payload: Dict[str, Any] = {"message": message}
    if prior_messages:
        payload["context"] = {"priorMessages": prior_messages}

    text_parts: List[str] = []
    workflow_name: Optional[str] = None

    try:
        with requests.post(url, json=payload, headers=headers, stream=True, timeout=60) as resp:
            if resp.status_code != 200:
                return f"BioMate returned {resp.status_code}", None, None

            # text/event-stream carries no charset, so requests defaults to
            # ISO-8859-1 and mangles UTF-8 (em-dashes, arrows, emoji). Force UTF-8.
            resp.encoding = "utf-8"

            current_event = "message"
            for raw in resp.iter_lines(decode_unicode=True):
                if not raw:
                    continue
                if raw.startswith(":"):
                    continue
                if raw.startswith("event:"):
                    current_event = raw[6:].strip()
                    continue
                if raw.startswith("data:"):
                    data_str = raw[5:].strip()
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
                            )
                    elif current_event in ("done", "complete"):
                        break
                    current_event = "message"

    except requests.exceptions.Timeout:
        return "BioMate timed out. Please retry.", None, None
    except Exception as exc:
        log.exception(f"chat stream error: {exc}")
        return f"BioMate error: {exc}", None, None

    answer = "".join(text_parts).strip() or "BioMate is processing your request."
    view_url = (
        f"{os.environ.get('BIOMATE_DEEP_LINK_BASE', 'https://app.biomate.ai')}?workflow={workflow_name}"
        if workflow_name else None
    )
    return answer, workflow_name, view_url


# ──────────────────────────────────────────────────────────────────────────────
# Tool handlers — one function per OpenAPI operationId
# ──────────────────────────────────────────────────────────────────────────────

def handle_biomate_session(body: Dict, token: str) -> Dict:
    """
    POST /tools/biomate_session
    Full session: chat stream → (optional) execute → wait for run.
    Returns structured result with answer, run_id, view_url.
    """
    goal = body.get("goal", "").strip()
    if not goal:
        return {"isError": True, "content": [{"type": "text", "text": "goal is required"}]}

    answer, workflow_name, view_url = _consume_chat_stream(goal, token)

    result: Dict[str, Any] = {
        "answer": answer,
        "workflow_name": workflow_name,
        "view_url": view_url,
    }
    return {
        "isError": False,
        "content": [{"type": "text", "text": json.dumps(result)}],
        "view_url": view_url,
    }


def handle_search_workflow(body: Dict, token: str, base_url: Optional[str] = None) -> Dict:
    """POST /tools/search_workflow — proxy to BioMate's workflow search."""
    query = body.get("query", "")
    limit = body.get("limit", 5)
    domain = body.get("domain")

    url = f"{base_url or BIOMATE_API_URL}/api/workflows/search"
    params: Dict[str, Any] = {"q": query, "limit": limit}
    if domain:
        params["domain"] = domain

    try:
        resp = requests.get(url, params=params, headers=_biomate_headers(token), timeout=15)
        resp.raise_for_status()
        return {"isError": False, "content": [{"type": "text", "text": resp.text}]}
    except requests.exceptions.HTTPError as exc:
        # Fallback: route through chat stream if dedicated search endpoint doesn't exist
        if exc.response.status_code == 404:
            answer, _, _ = _consume_chat_stream(
                f"Search for workflows related to: {query}", token, base_url=base_url
            )
            return {"isError": False, "content": [{"type": "text", "text": answer}]}
        return {"isError": True, "content": [{"type": "text", "text": str(exc)}]}
    except Exception as exc:
        return {"isError": True, "content": [{"type": "text", "text": str(exc)}]}


def handle_get_run(body: Dict, token: str, base_url: Optional[str] = None) -> Dict:
    """POST /tools/get_run — proxy to BioMate's run status endpoint."""
    run_id = body.get("run_id", "")
    include_findings = body.get("include_findings", True)

    url = f"{base_url or BIOMATE_API_URL}/api/runs/{run_id}"
    params = {"include_findings": str(include_findings).lower()}

    try:
        resp = requests.get(url, params=params, headers=_biomate_headers(token), timeout=15)
        resp.raise_for_status()
        return {"isError": False, "content": [{"type": "text", "text": resp.text}]}
    except Exception as exc:
        return {"isError": True, "content": [{"type": "text", "text": str(exc)}]}


def handle_list_runs(body: Dict, token: str, base_url: Optional[str] = None) -> Dict:
    """POST /tools/list_runs — proxy to BioMate's runs list."""
    url = f"{base_url or BIOMATE_API_URL}/api/runs"
    params = {
        "limit": body.get("limit", 10),
        "status": body.get("status", "all"),
    }
    if body.get("experiment_id"):
        params["experiment_id"] = body["experiment_id"]

    try:
        resp = requests.get(url, params=params, headers=_biomate_headers(token), timeout=15)
        resp.raise_for_status()
        return {"isError": False, "content": [{"type": "text", "text": resp.text}]}
    except Exception as exc:
        return {"isError": True, "content": [{"type": "text", "text": str(exc)}]}


def handle_cancel_run(body: Dict, token: str, base_url: Optional[str] = None) -> Dict:
    """POST /tools/cancel_run."""
    run_id = body.get("run_id", "")
    url = f"{base_url or BIOMATE_API_URL}/api/runs/{run_id}/cancel"
    try:
        resp = requests.post(url, headers=_biomate_headers(token), timeout=10)
        resp.raise_for_status()
        return {"isError": False, "content": [{"type": "text", "text": resp.text}]}
    except Exception as exc:
        return {"isError": True, "content": [{"type": "text", "text": str(exc)}]}


def _simple_proxy_post(endpoint: str, body: Dict, token: str, base_url: Optional[str] = None) -> Dict:
    """Generic POST proxy for straightforward tool endpoints."""
    url = f"{base_url or BIOMATE_API_URL}{endpoint}"
    try:
        resp = requests.post(url, json=body, headers=_biomate_headers(token), timeout=30)
        resp.raise_for_status()
        return {"isError": False, "content": [{"type": "text", "text": resp.text}]}
    except Exception as exc:
        return {"isError": True, "content": [{"type": "text", "text": str(exc)}]}


# Map operationId → handler
_TOOL_HANDLERS = {
    "biomate_session": handle_biomate_session,
    "search_workflow": handle_search_workflow,
    "get_run": handle_get_run,
    "list_runs": handle_list_runs,
    "cancel_run": handle_cancel_run,
}

_SIMPLE_PROXY_TOOLS = {
    "get_workflow_spec":  "/api/workflows/spec",
    "run_workflow":       "/api/workflows/execute",
    "preview_file":       "/api/files/preview",
    "export_report":      "/api/runs/report",
    "analyze_results":    "/api/runs/analyze",
    "explain_error":      "/api/runs/explain",
    "query_database":     "/api/db/query",
    "recall_memory":      "/api/memory/recall",
    "upload_file":        "/api/files/upload-url",
}


def dispatch_tool(operation_id: str, body: Dict, token: str, base_url: Optional[str] = None) -> Dict:
    """Route an incoming ChatGPT Actions request to the correct handler."""
    if operation_id in _TOOL_HANDLERS:
        handler = _TOOL_HANDLERS[operation_id]
        # Pass base_url to handlers that accept it
        import inspect
        if "base_url" in inspect.signature(handler).parameters:
            return handler(body, token, base_url=base_url)
        return handler(body, token)

    if operation_id in _SIMPLE_PROXY_TOOLS:
        return _simple_proxy_post(_SIMPLE_PROXY_TOOLS[operation_id], body, token, base_url=base_url)

    return {
        "isError": True,
        "content": [{"type": "text", "text": f"Unknown tool: {operation_id}"}],
    }


# ──────────────────────────────────────────────────────────────────────────────
# Flask app
# ──────────────────────────────────────────────────────────────────────────────

def create_flask_app():
    from flask import Flask, request, jsonify

    app = Flask("biomate-chatgpt-adapter")

    @app.route("/tools/health", methods=["GET"])
    def health():
        return jsonify({"status": "ok", "service": "biomate-chatgpt-adapter"})

    # Single catch-all route for all /tools/<operation_id> endpoints
    @app.route("/tools/<operation_id>", methods=["POST"])
    def tool_endpoint(operation_id):
        token = _extract_bearer(request.headers.get("Authorization", ""))
        body = request.get_json(silent=True) or {}
        result = dispatch_tool(operation_id, body, token)
        status = 200
        if result.get("isError"):
            status = 500
        return jsonify(result), status

    return app


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="BioMate ChatGPT Actions adapter")
    parser.add_argument("--port", type=int, default=int(os.environ.get("CHATGPT_ADAPTER_PORT", 8093)))
    args = parser.parse_args()
    app = create_flask_app()
    log.warning(f"BioMate ChatGPT adapter listening on port {args.port}")
    app.run(host="0.0.0.0", port=args.port)
