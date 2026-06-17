#!/usr/bin/env python3
"""
BioMate MCP Server
==================
Exposes BioMate's scientific workflow capabilities as MCP (Model Context Protocol) tools,
enabling any MCP-compatible AI client (Claude Desktop, Cursor, etc.) to invoke BioMate
workflows, search databases, and retrieve results.

Transport: stdio (JSON-RPC 2.0, newline-delimited)

Usage:
    python -m biomate_mcp_server
    # or directly:
    python backend/lib/mcp/biomate_mcp_server.py

Configuration (environment variables):
    BIOMATE_API_URL     BioMate server URL (default: http://localhost:5000)
    BIOMATE_API_KEY     User API key for authentication
    BIOMATE_USER_EMAIL  User email (alternative to API key)

MCP tools exposed:
    search_workflow     Search BioMate workflow catalog by natural language query
    run_workflow        Execute a workflow with parameters on BioMate cloud
    get_run_status      Poll status of a running workflow execution
    get_run_results     Retrieve output files from a completed run
    query_database      Query a biological database (UniProt, PDB, NCBI Gene, etc.)
    analyze_file        Run lightweight data analysis on an S3 key or inline data
"""

import json
import sys
import os
import logging
import time
import threading
from typing import Any, Dict, Generator, List, Optional

import requests

# ──────────────────────────────────────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────────────────────────────────────

BIOMATE_API_URL = os.environ.get("BIOMATE_API_URL", "http://localhost:5000")
BIOMATE_API_KEY = os.environ.get("BIOMATE_API_KEY", "")
SERVER_NAME = "biomate"
SERVER_VERSION = "2.0.0"
PROTOCOL_VERSION = "2024-11-05"

# Thread-safe stdout — progress notifications are emitted from worker threads
# while the main loop continues reading stdin.
_send_lock = threading.Lock()

logging.basicConfig(
    stream=sys.stderr,
    level=logging.DEBUG if os.environ.get("MCP_DEBUG") else logging.WARNING,
    format="[BioMate MCP] %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Tool definitions — sourced from the canonical manifest
# ──────────────────────────────────────────────────────────────────────────────
# Single source of truth lives in backend/lib/mcp/tools_manifest.py. All other
# surfaces (Open Claw, Slack, WeChat, ChatGPT GPT, Claude Skill catalog) are
# generated from the same module. See docs/20260513_CONNECTOR_ARCHITECTURE_V2.md.

try:
    from backend.lib.mcp.tools_manifest import (  # when run from repo root
        to_mcp as _manifest_to_mcp,
        get_tool as _manifest_get_tool,
    )
except ModuleNotFoundError:
    from tools_manifest import (  # when run as standalone package
        to_mcp as _manifest_to_mcp,
        get_tool as _manifest_get_tool,
    )

TOOLS: List[Dict[str, Any]] = _manifest_to_mcp()

# Schema definitions live exclusively in backend/lib/mcp/tools_manifest.py.

# ──────────────────────────────────────────────────────────────────────────────
# HTTP client to BioMate API
# ──────────────────────────────────────────────────────────────────────────────

class BioMateClient:
    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        if api_key:
            self.session.headers["Authorization"] = f"Bearer {api_key}"
        self.session.headers["Content-Type"] = "application/json"
        self.session.headers["User-Agent"] = f"BioMate-MCP/{SERVER_VERSION}"

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def search_workflow(self, query: str, limit: int = 5) -> Dict[str, Any]:
        try:
            r = self.session.post(
                self._url("/api/workflows/search"),
                json={"query": query, "limit": limit},
                timeout=15,
            )
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            return {"error": str(exc), "results": []}

    def run_workflow(self, workflow_id: str, params: dict, session_message: Optional[str] = None) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"workflow_id": workflow_id, "params": params or {}}
        if session_message:
            payload["message"] = session_message
        try:
            r = self.session.post(
                self._url("/api/workflows/execute"),
                json=payload,
                timeout=30,
            )
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            return {"error": str(exc)}

    def get_run_status(self, run_id: str) -> Dict[str, Any]:
        try:
            r = self.session.get(
                self._url(f"/api/pipeline/runs/{run_id}/status"),
                timeout=10,
            )
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            return {"error": str(exc), "run_id": run_id}

    def get_run_results(self, run_id: str) -> Dict[str, Any]:
        try:
            r = self.session.get(
                self._url(f"/api/pipeline/runs/{run_id}/outputs"),
                timeout=15,
            )
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            return {"error": str(exc), "run_id": run_id}

    def query_database(self, database: str, query: str) -> Dict[str, Any]:
        try:
            r = self.session.post(
                self._url("/api/databases/query"),
                json={"database": database, "query": query},
                timeout=20,
            )
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            return {"error": str(exc), "database": database, "query": query}

    def analyze_file(self, s3_key: Optional[str], inline_data: Optional[str], file_type: str = "auto") -> Dict[str, Any]:
        payload: Dict[str, Any] = {"file_type": file_type}
        if s3_key:
            payload["s3_key"] = s3_key
        if inline_data:
            payload["inline_data"] = inline_data
        try:
            r = self.session.post(self._url("/api/data/analyze"), json=payload, timeout=60)
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            return {"error": str(exc)}

    def cancel_run(self, run_id: str) -> Dict[str, Any]:
        try:
            r = self.session.post(self._url(f"/api/workflows/runs/{run_id}/cancel"), json={}, timeout=15)
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            return {"error": str(exc), "run_id": run_id}

    def list_runs(self, limit: int = 10, status: str = "all") -> Dict[str, Any]:
        params: Dict[str, str] = {"limit": str(limit)}
        if status != "all":
            params["status"] = status
        try:
            r = self.session.get(self._url("/api/workflows/runs"), params=params, timeout=15)
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            return {"error": str(exc)}

    def analyze_results(self, run_id: str, question: str) -> Dict[str, Any]:
        try:
            r = self.session.post(
                self._url(f"/api/workflows/runs/{run_id}/ai/analyze"),
                json={"question": question},
                timeout=60,
            )
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            return {"error": str(exc), "run_id": run_id}

    def explain_error(self, run_id: str, error_log: str = "") -> Dict[str, Any]:
        try:
            r = self.session.post(
                self._url("/api/workflows/explain_error"),
                json={"run_id": run_id, "error_log": error_log},
                timeout=30,
            )
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            return {"error": str(exc), "run_id": run_id}

    # ── Tier 2/3 (v2 manifest) ────────────────────────────────────────────────

    def get_workflow_spec(self, workflow_id: str) -> Dict[str, Any]:
        try:
            r = self.session.get(
                self._url("/api/workflows/spec"),
                params={"workflow_id": workflow_id},
                timeout=15,
            )
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            return {"error": str(exc), "workflow_id": workflow_id}

    def get_run(self, run_id: str, include_findings: bool = True) -> Dict[str, Any]:
        try:
            r = self.session.get(
                self._url(f"/api/workflows/runs/{run_id}"),
                params={"include_findings": "true" if include_findings else "false"},
                timeout=15,
            )
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            return {"error": str(exc), "run_id": run_id}

    def preview_file(self, s3_key: str, run_id: Optional[str] = None, max_rows: int = 100) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"s3_key": s3_key, "max_rows": max_rows}
        if run_id:
            payload["run_id"] = run_id
        try:
            r = self.session.post(self._url("/api/files/preview"), json=payload, timeout=30)
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            return {"error": str(exc), "s3_key": s3_key}

    def export_report(self, run_id: str, fmt: str = "pdf", sections: Optional[List[str]] = None) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"format": fmt}
        if sections:
            payload["sections"] = sections
        try:
            r = self.session.post(
                self._url(f"/api/workflows/runs/{run_id}/findings/report"),
                json=payload,
                timeout=120,
            )
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            return {"error": str(exc), "run_id": run_id}

    def recall_memory(self, query: str, scope: str = "all", limit: int = 5) -> Dict[str, Any]:
        try:
            r = self.session.get(
                self._url("/api/memory/relevant"),
                params={"query": query, "scope": scope, "limit": limit},
                timeout=20,
            )
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            return {"error": str(exc)}

    def upload_signed_url(self, filename: str, size_bytes: Optional[int], content_type: Optional[str]) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"filename": filename}
        if size_bytes is not None:
            payload["size_bytes"] = size_bytes
        if content_type:
            payload["content_type"] = content_type
        try:
            r = self.session.post(self._url("/api/uploads/signed_url"), json=payload, timeout=15)
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            return {"error": str(exc), "filename": filename}

    @staticmethod
    def _iter_sse(resp) -> "Generator[Dict[str, Any], None, None]":
        """Shared SSE line parser for any streaming HTTP response."""
        current_event = "message"
        for raw_line in resp.iter_lines(decode_unicode=True):
            if not raw_line:
                continue
            if raw_line.startswith(":"):
                continue  # SSE comment / keep-alive / ping
            if raw_line.startswith("event:"):
                current_event = raw_line[6:].strip()
                continue
            if raw_line.startswith("data:"):
                data_str = raw_line[5:].strip()
                try:
                    data = json.loads(data_str)
                except json.JSONDecodeError:
                    data = data_str
                yield {"event": current_event, "data": data}
                current_event = "message"  # reset after each complete event

    def open_claw_stream(self, goal: str, inputs: Optional[Dict[str, Any]], experiment_id: Optional[str]):
        """Generator yielding SSE events from /api/chat/stream (BioMate's main chat endpoint).

        Each yielded dict has shape: {event: <name>, data: <parsed json or str>}.
        Emits: 'delta', 'tool_event', 'workflow_ready', 'final', 'done'.
        """
        # /api/chat/stream expects {message, context}
        payload: Dict[str, Any] = {"message": goal}
        context: Dict[str, Any] = {}
        if inputs:
            context["inputs"] = inputs
        if experiment_id:
            context["experimentId"] = experiment_id
        if context:
            payload["context"] = context

        headers = dict(self.session.headers)
        headers["Accept"] = "text/event-stream"

        with self.session.post(
            self._url("/api/chat/stream"),
            json=payload,
            headers=headers,
            stream=True,
            timeout=(30, 600),  # 10-min idle limit
        ) as resp:
            resp.raise_for_status()
            yield from self._iter_sse(resp)

    def execute_workflow(self, workflow_def: Dict[str, Any]) -> Dict[str, Any]:
        """POST /api/workflows/execute with the workflow definition.

        Returns {runId, runInvocationId, pipelineRunId, status, ...} on success
        or {error: ...} on failure.
        """
        payload: Dict[str, Any] = {
            "workflowDefinition": workflow_def,
            "inputs": [],
            "executionConfig": {
                # Bypass the scientific confidence gate — the AI already assessed
                # the workflow is appropriate for this goal.
                "allowLowConfidence": True,
                "pipeline_profile": os.environ.get("BIOMATE_NEXTFLOW_PROFILE", "aws"),
            },
        }
        try:
            r = self.session.post(
                self._url("/api/workflows/execute"),
                json=payload,
                timeout=60,
            )
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            return {"error": str(exc)}

    def workflow_events_stream(self, invocation_id: str):
        """Generator yielding SSE events from /api/workflows/:id/events.

        The Node.js frontend relays phase/step/QC events injected by the Python
        backend via /internal/sse-inject.  Each yielded dict is:
          {event: 'chat_progress'|'qc_gate_triggered'|'qc_warning'|..., data: {...}}
        """
        headers = dict(self.session.headers)
        headers["Accept"] = "text/event-stream"

        with self.session.get(
            self._url(f"/api/workflows/{invocation_id}/events"),
            headers=headers,
            stream=True,
            timeout=(30, 3600),  # up to 1h for long workflows
        ) as resp:
            resp.raise_for_status()
            yield from self._iter_sse(resp)


# ──────────────────────────────────────────────────────────────────────────────
# Streaming bridge — BioMate SSE → MCP notifications/progress
# ──────────────────────────────────────────────────────────────────────────────

# Event shape contract (see docs/20260513_CONNECTOR_ARCHITECTURE_V2.md):
#   {kind, summary_md, view_url, thumbnail_png_b64?, delta}
# We normalize whatever the Open Claw stream emits into this shape so every
# downstream surface (Claude Code, Codex poll, Slack relay) shares one schema.

_PROGRESS_KINDS = {
    "phase_started", "phase_completed",
    "step_started", "step_completed", "step_failed",
    "qc_gate", "auto_loop_remediation",
    "finding", "report_ready",
    "text_delta", "done",
}


def _normalize_sse_event(evt: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Map an SSE event (from chat stream OR workflow events endpoint) into the
    canonical progress payload understood by SessionRunner._emit_progress().

    Two sources have distinct event shapes:

    1. /api/chat/stream  — events: delta, workflow_ready, final, done
    2. /api/workflows/:id/events  — events: connected, chat_progress,
       qc_gate_triggered, qc_warning

    The workflow events endpoint is the one that carries real phase/step/QC
    progress; the chat stream events are used only to surface AI narration.
    """
    name = evt.get("event") or ""
    data = evt.get("data") or {}
    if not isinstance(data, dict):
        data = {"raw": data}

    # ── Workflow events SSE (primary source of structured progress) ───────────

    if name == "chat_progress":
        # Emitted by workflow_chat_narrator.emit() via /internal/sse-inject.
        # data = {kind, invocation_id, friendly_message, ts, technical}
        kind = data.get("kind", "")
        friendly = data.get("friendly_message") or kind
        tech = data.get("technical") or {}

        if kind == "workflow.phase_started":
            phase_name = tech.get("phase_name") or tech.get("name") or "?"
            return {
                "kind": "phase_started",
                "summary_md": f"> **Phase: {phase_name}** — started",
                "delta": {"name": phase_name, **tech},
            }
        if kind == "workflow.phase_completed":
            phase_name = tech.get("phase_name") or tech.get("name") or "?"
            return {
                "kind": "phase_completed",
                "summary_md": f"> **Phase: {phase_name}** — completed",
                "delta": {"name": phase_name, **tech},
            }
        if kind == "workflow.phase_failed":
            phase_name = tech.get("phase_name") or tech.get("name") or "?"
            reason = tech.get("reason") or ""
            return {
                "kind": "phase_failed",
                "summary_md": f"> ⚠ **Phase: {phase_name}** — failed\n> {reason}",
                "delta": {"name": phase_name, **tech},
            }
        if kind in ("workflow.qc_gate_warning", "workflow.qc_gate_blocked"):
            verdict = "advisory" if kind == "workflow.qc_gate_warning" else "halt"
            metric = tech.get("metric") or "?"
            return {
                "kind": "qc_gate",
                "summary_md": friendly,
                "delta": {"verdict": verdict, "metric": metric, **tech},
            }
        if kind == "workflow.autoloop_started":
            return {
                "kind": "auto_loop_remediation",
                "summary_md": friendly,
                "delta": tech,
            }
        if kind == "workflow.completed":
            return {
                "kind": "done",
                "summary_md": friendly,
                "delta": {**tech, "kind": kind},
            }
        if kind in ("workflow.failed", "workflow.cancelled"):
            return {
                "kind": "phase_failed",
                "summary_md": friendly,
                "delta": {**tech, "kind": kind},
            }
        if kind and kind.startswith("workflow."):
            # queued, running, submitted, paused, resumed, etc. — narrate as text
            return {
                "kind": "text_delta",
                "summary_md": friendly,
                "delta": data,
            }
        return None

    if name == "qc_gate_triggered":
        # Emitted directly by pipelineQCGateHandler when a gate fires.
        verdict = data.get("verdict", "halt")
        metric = data.get("metric") or "?"
        value = data.get("value") or "?"
        threshold = data.get("threshold") or "?"
        return {
            "kind": "qc_gate",
            "summary_md": (
                f"**QC gate — {metric}:** measured `{value}` vs threshold `{threshold}` → **{verdict}**"
            ),
            "thumbnail_png_b64": data.get("thumbnail_png_b64"),
            "delta": {
                "metric": metric, "value": value,
                "threshold": threshold, "verdict": verdict,
                "suggestions": data.get("suggestions", []),
            },
        }

    if name == "qc_warning":
        metric = data.get("metric") or "?"
        return {
            "kind": "qc_gate",
            "summary_md": f"**QC warning — {metric}:** near threshold",
            "delta": {"verdict": "advisory", **data},
        }

    # ── Chat stream events (secondary — AI narration only) ────────────────────

    if name == "delta":
        text = data.get("text", "")
        if not text:
            return None
        return {"kind": "text_delta", "summary_md": text, "delta": data}

    if name == "workflow_ready":
        wf_name = data.get("workflow_name") or ""
        msg = f"**Workflow ready:** {wf_name}" if wf_name else "**Workflow ready** — preparing to run…"
        return {"kind": "phase_started", "summary_md": msg, "delta": data}

    # finding / report_ready (from older Open Claw bridge — kept for compat)
    if name == "finding":
        return {
            "kind": "finding",
            "summary_md": data.get("summary_md") or f"**Finding:** {data.get('title','(untitled)')}",
            "view_url": data.get("view_url"),
            "thumbnail_png_b64": data.get("thumbnail_png_b64"),
            "delta": data,
        }

    # Ignore: connected, done, final, tool_event, ready, needs_input, error
    # (not user-facing progress; handled directly in SessionRunner)
    return None


class SessionRunner(threading.Thread):
    """Runs biomate_session (or run_workflow stream=true) on a worker thread.

    Reads SSE from Open Claw, emits MCP notifications/progress for each event,
    and finally sends the tools/call response itself. The main loop continues
    handling other requests in the meantime.
    """

    def __init__(
        self,
        client: "BioMateClient",
        req_id: Any,
        tool_name: str,
        tool_args: Dict[str, Any],
        progress_token: Optional[Any],
    ):
        super().__init__(daemon=True)
        self.client = client
        self.req_id = req_id
        self.tool_name = tool_name
        self.tool_args = tool_args
        self.progress_token = progress_token
        self._final_run_id: Optional[str] = None
        self._final_view_url: Optional[str] = None
        self._final_summary_md: List[str] = []

    def _emit_progress(self, payload: Dict[str, Any], n: int, total: Optional[int] = None) -> None:
        if self.progress_token is None:
            return  # Client didn't ask for progress notifications
        params: Dict[str, Any] = {
            "progressToken": self.progress_token,
            "progress": n,
            "message": payload.get("summary_md", ""),
            "_meta": {
                "kind": payload.get("kind"),
                "view_url": payload.get("view_url"),
                "thumbnail_png_b64": payload.get("thumbnail_png_b64"),
                "delta": payload.get("delta"),
            },
        }
        if total is not None:
            params["total"] = total
        send({"jsonrpc": "2.0", "method": "notifications/progress", "params": params})

    def run(self) -> None:
        try:
            self._run_impl()
        except Exception as exc:
            log.exception(f"SessionRunner error for {self.tool_name}")
            send(make_error(self.req_id, -32603, "Streaming tool error", str(exc)))

    def _run_impl(self) -> None:
        goal = self.tool_args.get("goal") or self.tool_args.get("session_message") or ""
        inputs = self.tool_args.get("inputs")
        experiment_id = self.tool_args.get("experiment_id")

        n = 0  # progress counter

        # ── Phase 1: AI chat stream → workflow suggestion ─────────────────────
        # Consume /api/chat/stream to get the AI response + generated workflow.
        # Emit text_delta notifications for AI narration; capture the workflow
        # definition from the 'final' event so we can execute it.

        workflow_def: Optional[Dict[str, Any]] = None
        ai_text_parts: List[str] = []

        for evt in self.client.open_claw_stream(goal=goal, inputs=inputs, experiment_id=experiment_id):
            ename = evt.get("event", "")
            edata = evt.get("data") or {}

            if ename == "delta" and isinstance(edata, dict):
                text = edata.get("text", "")
                if text:
                    ai_text_parts.append(text)
                    n += 1
                    self._emit_progress({"kind": "text_delta", "summary_md": text, "delta": edata}, n)

            elif ename == "final" and isinstance(edata, dict):
                # The full AI response + generated workflow lives in 'final'.
                wf = edata.get("workflow")
                if isinstance(wf, dict) and wf.get("success") and not wf.get("error"):
                    workflow_def = wf
                    wf_name = (
                        wf.get("workflow_ga", {}).get("name")
                        or wf.get("workflow_name")
                        or wf.get("chain_display_name")
                        or "(workflow)"
                    )
                    n += 1
                    self._emit_progress(
                        {
                            "kind": "phase_started",
                            "summary_md": f"**Workflow ready:** {wf_name} — launching…",
                            "delta": {"workflow_name": wf_name},
                        },
                        n,
                    )

            elif ename == "done":
                break

        if not workflow_def:
            # No runnable workflow — return the AI text response as-is.
            final_text = "".join(ai_text_parts).strip() or "Session complete."
            send(make_response(self.req_id, {
                "content": [{"type": "text", "text": final_text}],
                "isError": False,
            }))
            return

        # ── Phase 2: Execute the workflow ─────────────────────────────────────
        # POST /api/workflows/execute and get back an invocationId.

        exec_result = self.client.execute_workflow(workflow_def)
        log.debug("execute_workflow response: %s", json.dumps(exec_result, default=str)[:300])

        if exec_result.get("error"):
            err = exec_result["error"]
            log.warning("Workflow execution failed: %s", err)
            send(make_error(self.req_id, -32603, "Workflow execution failed", err))
            return

        invocation_id: Optional[str] = (
            exec_result.get("runInvocationId")
            or exec_result.get("runId")
        )
        run_id: Optional[str] = (
            exec_result.get("pipelineRunId")
            or exec_result.get("runId")
        )
        self._final_run_id = invocation_id or run_id

        # Build canonical view URL into BioMate panel
        base = self.client.base_url
        if invocation_id:
            self._final_view_url = f"{base}/workflows/{invocation_id}"
        elif run_id:
            self._final_view_url = f"{base}/workflows/{run_id}"

        n += 1
        self._emit_progress(
            {
                "kind": "phase_started",
                "summary_md": f"**Phase 1: Workflow launched** — run `{self._final_run_id}`",
                "view_url": self._final_view_url,
                "delta": {"invocation_id": invocation_id, "run_id": run_id},
            },
            n,
        )

        if not invocation_id:
            # Got a run but no invocationId to subscribe to events — return early.
            final_text = "".join(ai_text_parts).strip() or "Workflow submitted."
            send(make_response(self.req_id, {
                "content": [{"type": "text", "text": json.dumps({
                    "summary_md": final_text,
                    "run_id": run_id,
                    "view_url": self._final_view_url,
                    "status": exec_result.get("status"),
                }, indent=2)}],
                "isError": False,
            }))
            return

        # ── Phase 3: Stream workflow events ───────────────────────────────────
        # Subscribe to /api/workflows/:invocationId/events SSE.
        # The Python backend pushes phase/step/QC/finding events via
        # /internal/sse-inject → sseManager.injectRawEvent().

        terminal_kinds = {"done", "phase_failed"}
        terminal_workflow_kinds = {"workflow.completed", "workflow.failed", "workflow.cancelled"}

        for evt in self.client.workflow_events_stream(invocation_id):
            ename = evt.get("event", "")
            edata = evt.get("data") or {}

            # Detect terminal event directly before normalization
            is_terminal = False
            if ename == "chat_progress" and isinstance(edata, dict):
                if edata.get("kind") in terminal_workflow_kinds:
                    is_terminal = True

            payload = _normalize_sse_event(evt)
            if payload is not None:
                n += 1
                if not payload.get("view_url"):
                    payload["view_url"] = self._final_view_url
                self._emit_progress(payload, n)

                kind = payload.get("kind")
                if kind == "text_delta":
                    self._final_summary_md.append(payload.get("summary_md", ""))
                if payload.get("delta", {}).get("run_id"):
                    self._final_run_id = payload["delta"]["run_id"]
                if payload.get("view_url"):
                    self._final_view_url = payload["view_url"]

            if is_terminal or (payload and payload.get("kind") in terminal_kinds):
                break

        # ── Final response ────────────────────────────────────────────────────
        ai_summary = "".join(ai_text_parts).strip()
        wf_summary = " ".join(self._final_summary_md).strip()
        final_text = wf_summary or ai_summary or "Session complete."

        result_payload: Dict[str, Any] = {
            "summary_md": final_text,
            "run_id": self._final_run_id,
            "view_url": self._final_view_url,
        }
        if self._final_view_url:
            result_payload["summary_md"] += (
                f"\n\n---\nLive panel: <{self._final_view_url}>"
                f" · BioMate run id `{self._final_run_id}`"
            )

        send(make_response(self.req_id, {
            "content": [{"type": "text", "text": json.dumps(result_payload, indent=2)}],
            "isError": False,
        }))


# ──────────────────────────────────────────────────────────────────────────────
# Tool dispatcher
# ──────────────────────────────────────────────────────────────────────────────

def dispatch_tool(client: BioMateClient, tool_name: str, args: Dict[str, Any]) -> Any:
    """Execute a non-streaming tool call. Returns JSON-serializable result.

    Streaming tools (biomate_session, run_workflow with stream=true) are
    dispatched via SessionRunner from handle_request — they do not flow
    through this function.
    """
    if tool_name == "search_workflow":
        return client.search_workflow(query=args["query"], limit=int(args.get("limit", 5)))

    if tool_name == "get_workflow_spec":
        return client.get_workflow_spec(workflow_id=args["workflow_id"])

    if tool_name == "run_workflow":
        return client.run_workflow(
            workflow_id=args["workflow_id"],
            params=args.get("params", {}),
            session_message=args.get("session_message"),
        )

    if tool_name == "get_run":
        return client.get_run(
            run_id=args["run_id"],
            include_findings=bool(args.get("include_findings", True)),
        )

    if tool_name == "cancel_run":
        return client.cancel_run(run_id=args["run_id"])

    if tool_name == "list_runs":
        return client.list_runs(
            limit=int(args.get("limit", 10)),
            status=args.get("status", "all"),
        )

    if tool_name == "preview_file":
        return client.preview_file(
            s3_key=args["s3_key"],
            run_id=args.get("run_id"),
            max_rows=int(args.get("max_rows", 100)),
        )

    if tool_name == "export_report":
        return client.export_report(
            run_id=args["run_id"],
            fmt=args.get("format", "pdf"),
            sections=args.get("sections"),
        )

    if tool_name == "analyze_results":
        return client.analyze_results(
            run_id=args["run_id"],
            question=args.get("question", "Summarize the key findings and scientific interpretation."),
        )

    if tool_name == "explain_error":
        return client.explain_error(
            run_id=args["run_id"],
            error_log=args.get("error_log", ""),
        )

    if tool_name == "query_database":
        return client.query_database(database=args["database"], query=args["query"])

    if tool_name == "recall_memory":
        return client.recall_memory(
            query=args["query"],
            scope=args.get("scope", "all"),
            limit=int(args.get("limit", 5)),
        )

    if tool_name == "upload_file":
        return client.upload_signed_url(
            filename=args["filename"],
            size_bytes=args.get("size_bytes"),
            content_type=args.get("content_type"),
        )

    # ── Legacy aliases (deprecated; remove after Phase 2 rollout) ─────────────
    if tool_name == "get_run_status":
        return client.get_run_status(run_id=args["run_id"])
    if tool_name == "get_run_results":
        return client.get_run_results(run_id=args["run_id"])
    if tool_name == "analyze_file":
        return client.analyze_file(
            s3_key=args.get("s3_key"),
            inline_data=args.get("inline_data"),
            file_type=args.get("file_type", "auto"),
        )

    raise ValueError(f"Unknown tool: {tool_name}")


# Tools that emit notifications/progress instead of a single sync result.
_STREAMING_TOOLS = {"biomate_session", "run_workflow"}


def is_streaming_call(tool_name: str, tool_args: Dict[str, Any]) -> bool:
    if tool_name == "biomate_session":
        return bool(tool_args.get("stream", True))
    if tool_name == "run_workflow":
        return bool(tool_args.get("stream", False))
    return False


# ──────────────────────────────────────────────────────────────────────────────
# JSON-RPC 2.0 / MCP protocol handling
# ──────────────────────────────────────────────────────────────────────────────

def make_response(req_id: Any, result: Any) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}

def make_error(req_id: Any, code: int, message: str, data: Any = None) -> Dict[str, Any]:
    error: Dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": req_id, "error": error}

def send(obj: Dict[str, Any]) -> None:
    """Thread-safe stdout write — SessionRunner threads emit notifications via this."""
    line = json.dumps(obj, separators=(",", ":"))
    with _send_lock:
        sys.stdout.write(line + "\n")
        sys.stdout.flush()

def handle_request(client: BioMateClient, msg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Handle one JSON-RPC message. Returns response or None for notifications."""
    method = msg.get("method", "")
    req_id = msg.get("id")  # None for notifications
    params = msg.get("params", {}) or {}

    log.debug(f"← {method} id={req_id}")

    # ── Lifecycle ─────────────────────────────────────────────────────────────
    if method == "initialize":
        return make_response(req_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {
                "tools": {"listChanged": False},
            },
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        })

    if method == "notifications/initialized":
        # Fire-and-forget notification from client
        return None

    if method == "ping":
        return make_response(req_id, {})

    # ── Tools ─────────────────────────────────────────────────────────────────
    if method == "tools/list":
        return make_response(req_id, {"tools": TOOLS})

    if method == "tools/call":
        tool_name = params.get("name", "")
        tool_args = params.get("arguments", {}) or {}
        meta = params.get("_meta", {}) or {}
        progress_token = meta.get("progressToken")

        # Streaming tools: spawn a worker that emits notifications/progress and
        # sends the final tools/call response itself. Main loop returns None.
        if is_streaming_call(tool_name, tool_args):
            runner = SessionRunner(client, req_id, tool_name, tool_args, progress_token)
            runner.start()
            return None

        try:
            result = dispatch_tool(client, tool_name, tool_args)
            content_text = json.dumps(result, indent=2, default=str)
            return make_response(req_id, {
                "content": [{"type": "text", "text": content_text}],
                "isError": "error" in result if isinstance(result, dict) else False,
            })
        except ValueError as exc:
            return make_error(req_id, -32601, str(exc))
        except Exception as exc:
            log.exception(f"Tool call error: {tool_name}")
            return make_error(req_id, -32603, "Internal tool error", str(exc))

    # ── Resources / Prompts (not implemented, return capability not found) ────
    if method in ("resources/list", "resources/read", "prompts/list", "prompts/get"):
        return make_error(req_id, -32601, f"Method not implemented: {method}")

    # ── Unknown method ────────────────────────────────────────────────────────
    if req_id is not None:
        return make_error(req_id, -32601, f"Method not found: {method}")

    return None  # Unknown notification — ignore


# ──────────────────────────────────────────────────────────────────────────────
# Main loop (stdio transport)
# ──────────────────────────────────────────────────────────────────────────────

def run_server() -> None:
    client = BioMateClient(BIOMATE_API_URL, BIOMATE_API_KEY)
    log.warning(f"BioMate MCP Server {SERVER_VERSION} starting (api={BIOMATE_API_URL})")

    for raw_line in sys.stdin:
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            msg = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            send(make_error(None, -32700, f"Parse error: {exc}"))
            continue

        response = handle_request(client, msg)
        if response is not None:
            log.debug(f"→ {json.dumps(response)[:120]}")
            send(response)


if __name__ == "__main__":
    run_server()
