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
    from backend.lib.mcp.tools_manifest import (  # when run from main repo root
        to_mcp as _manifest_to_mcp,
        get_tool as _manifest_get_tool,
    )
except ModuleNotFoundError:
    try:
        from .tools_manifest import (  # when imported as package (mcp.biomate_mcp_server)
            to_mcp as _manifest_to_mcp,
            get_tool as _manifest_get_tool,
        )
    except ImportError:
        from tools_manifest import (  # when run directly as script from mcp/ dir
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

    @staticmethod
    def _classify_exc(exc: Exception) -> Dict[str, str]:
        """Convert an exception into a structured error dict the AI can act on.

        Returns {"error": True, "code": ..., "human_message": ..., "debug": ...}
        where `code` is a stable machine-readable token and `human_message` is
        what the AI should relay verbatim to the user.
        """
        import requests as _req
        debug = str(exc)

        # Connection-level errors — server not reachable
        if isinstance(exc, _req.exceptions.ConnectionError):
            return {
                "error": True,
                "code": "SERVER_UNREACHABLE",
                "human_message": (
                    "BioMate could not connect to the analysis server. "
                    "Please try again in a moment, or check your network connection."
                ),
                "debug": debug,
            }

        if isinstance(exc, _req.exceptions.Timeout):
            return {
                "error": True,
                "code": "TIMEOUT",
                "human_message": (
                    "The BioMate server did not respond in time. "
                    "Long-running analyses can take a few minutes — "
                    "use get_run to check the status, or try again shortly."
                ),
                "debug": debug,
            }

        if isinstance(exc, _req.exceptions.HTTPError) and exc.response is not None:
            status = exc.response.status_code
            try:
                body = exc.response.json()
                server_msg = body.get("message") or body.get("error") or ""
            except Exception:
                server_msg = exc.response.text[:200]

            if status == 401:
                return {
                    "error": True,
                    "code": "AUTH_FAILED",
                    "human_message": (
                        "BioMate rejected the API key. "
                        "Please check that BIOMATE_API_KEY is set correctly. "
                        "You can generate a key at Settings → API Keys in the BioMate UI."
                    ),
                    "debug": f"HTTP 401: {server_msg}",
                }
            if status == 403:
                return {
                    "error": True,
                    "code": "FORBIDDEN",
                    "human_message": (
                        "Your account does not have permission to perform this action. "
                        f"Server said: {server_msg}" if server_msg else
                        "Your account does not have permission to perform this action."
                    ),
                    "debug": f"HTTP 403: {server_msg}",
                }
            if status == 404:
                return {
                    "error": True,
                    "code": "NOT_FOUND",
                    "human_message": (
                        f"The requested resource was not found. {server_msg}"
                        if server_msg else
                        "The requested resource was not found. "
                        "Check the run_id or workflow_id and try again."
                    ),
                    "debug": f"HTTP 404: {server_msg}",
                }
            if status == 429:
                return {
                    "error": True,
                    "code": "RATE_LIMITED",
                    "human_message": (
                        "BioMate rate limit reached. Please wait a moment before retrying."
                    ),
                    "debug": f"HTTP 429: {server_msg}",
                }
            if status >= 500:
                return {
                    "error": True,
                    "code": "SERVER_ERROR",
                    "human_message": (
                        f"BioMate returned a server error (HTTP {status}). "
                        "This is usually transient — please try again. "
                        f"Details: {server_msg}" if server_msg else
                        f"BioMate returned a server error (HTTP {status}). Please try again."
                    ),
                    "debug": f"HTTP {status}: {server_msg}",
                }

        # Generic fallback
        return {
            "error": True,
            "code": "UNKNOWN_ERROR",
            "human_message": f"An unexpected error occurred: {debug[:300]}",
            "debug": debug,
        }

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
            return {**self._classify_exc(exc), "results": []}

    def run_workflow(self, workflow_id: str, params: dict, session_message: Optional[str] = None) -> Dict[str, Any]:
        # The backend's /api/workflows/execute expects a full `workflowDefinition`
        # object (NOT a bare {workflow_id, params}); for Nextflow workflows the
        # run parameters must be nested at workflowDefinition.parameters — a
        # top-level `parameters`/`params` field is ignored and the run falls back
        # to the workflow's defaults. Fetch the spec to build the definition.
        spec = self.get_workflow_spec(workflow_id)
        if isinstance(spec, dict) and spec.get("error"):
            return spec
        wf_def: Dict[str, Any] = dict((spec or {}).get("workflow_ga") or {})
        for k in ("name", "annotation", "description", "nextflow_path",
                  "format", "workflow_type", "tags"):
            if not wf_def.get(k) and (spec or {}).get(k) is not None:
                wf_def[k] = spec[k]
        if not wf_def.get("name"):
            wf_def["name"] = workflow_id
        merged_params = dict(wf_def.get("parameters") or {})
        merged_params.update(params or {})
        wf_def["parameters"] = merged_params

        payload: Dict[str, Any] = {"workflowDefinition": wf_def}
        if session_message:
            payload["message"] = session_message
        try:
            r = self.session.post(
                self._url("/api/workflows/execute"),
                json=payload,
                timeout=60,
            )
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            return self._classify_exc(exc)

    def get_run_status(self, run_id: str) -> Dict[str, Any]:
        # /api/pipeline/runs/{id}/status no longer exists; the run record (with
        # live status) lives at /api/workflows/runs/{id} under `.execution`.
        try:
            r = self.session.get(
                self._url(f"/api/workflows/runs/{run_id}"),
                timeout=10,
            )
            r.raise_for_status()
            data = r.json()
            execution = data.get("execution") or {}
            return {
                "run_id": run_id,
                "status": execution.get("status") or data.get("status"),
                "execution": execution,
                "progress": data.get("progress"),
            }
        except Exception as exc:
            return {**self._classify_exc(exc), "run_id": run_id}

    def get_run_results(self, run_id: str) -> Dict[str, Any]:
        # Outputs now come from the run record at /api/workflows/runs/{id}
        # (`.output_files` / `.inline_findings`), not /api/pipeline/.../outputs.
        try:
            r = self.session.get(
                self._url(f"/api/workflows/runs/{run_id}"),
                timeout=15,
            )
            r.raise_for_status()
            data = r.json()
            execution = data.get("execution") or {}
            return {
                "run_id": run_id,
                "status": execution.get("status") or data.get("status"),
                "output_files": data.get("output_files", []),
                "inline_findings": data.get("inline_findings", []),
                "output_dir": execution.get("nextflowOutputDir"),
            }
        except Exception as exc:
            return {**self._classify_exc(exc), "run_id": run_id}

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
            return {**self._classify_exc(exc), "database": database, "query": query}

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
            return self._classify_exc(exc)

    def cancel_run(self, run_id: str) -> Dict[str, Any]:
        try:
            r = self.session.post(self._url(f"/api/workflows/runs/{run_id}/cancel"), json={}, timeout=15)
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            return {**self._classify_exc(exc), "run_id": run_id}

    def list_runs(self, limit: int = 10, status: str = "all") -> Dict[str, Any]:
        params: Dict[str, str] = {"limit": str(limit)}
        if status != "all":
            params["status"] = status
        try:
            r = self.session.get(self._url("/api/workflows/runs"), params=params, timeout=15)
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            return self._classify_exc(exc)

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
            return {**self._classify_exc(exc), "run_id": run_id}

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
            return {**self._classify_exc(exc), "run_id": run_id}

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
            return {**self._classify_exc(exc), "workflow_id": workflow_id}

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
            return {**self._classify_exc(exc), "run_id": run_id}

    def preview_file(self, s3_key: str, run_id: Optional[str] = None, max_rows: int = 100) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"s3_key": s3_key, "max_rows": max_rows}
        if run_id:
            payload["run_id"] = run_id
        try:
            r = self.session.post(self._url("/api/files/preview"), json=payload, timeout=30)
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            return {**self._classify_exc(exc), "s3_key": s3_key}

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
            return {**self._classify_exc(exc), "run_id": run_id}

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
            return self._classify_exc(exc)

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
            return {**self._classify_exc(exc), "filename": filename}

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
        """Generator yielding SSE events from POST /api/open-claw/stream.

        Each yielded dict has shape: {event: <name>, data: <parsed json or str>}.

        The endpoint takes {messages: [...]} in Anthropic Messages API format and
        runs Claude with all 17 BioMate tools in an agentic loop.  It emits:
            delta           — {text: str}          streaming text chunk
            tool_call_start — {id, name}           tool invocation started
            tool_call_complete — {id, name, input} tool input parsed
            tool_result     — {tool_use_id, name, result}  tool result
            complete        — {iterations: int}    terminal: loop finished
            error           — {error: str}         terminal: server error

        The goal / inputs / experiment_id are serialised into the first user
        message so the inner Claude receives full context.
        """
        # Build user message content from goal + optional structured inputs.
        content = goal
        if inputs:
            content = f"{goal}\n\nInputs:\n{json.dumps(inputs, indent=2)}"
        if experiment_id:
            content = f"{content}\n\nExperiment ID: {experiment_id}"

        payload: Dict[str, Any] = {
            "messages": [{"role": "user", "content": content}]
        }

        headers = dict(self.session.headers)
        headers["Accept"] = "text/event-stream"

        with self.session.post(
            self._url("/api/open-claw/stream"),
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
            return self._classify_exc(exc)

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
    raw_data = evt.get("data")
    if isinstance(raw_data, dict):
        data: Dict[str, Any] = raw_data
    elif raw_data is not None:
        data = {"raw": raw_data, "text": str(raw_data)}
    else:
        data = {}

    # ── Simplified /api/open-claw/stream event format ─────────────────────────
    # These are the canonical event types emitted by the Open Claw streaming
    # endpoint. They form the public contract for all connector surfaces.

    if name == "text_delta":
        text = data.get("text") or data.get("raw") or ""
        return {"kind": "text_delta", "summary_md": str(text), "delta": data}

    if name == "workflow_phase":
        phase_name = data.get("name") or "?"
        status = data.get("status", "")
        if status in ("completed", "done"):
            return {
                "kind": "phase_completed",
                "summary_md": f"> **Phase: {phase_name}** — completed",
                "delta": data,
            }
        if status == "failed":
            return {
                "kind": "phase_failed",
                "summary_md": f"> ⚠ **Phase: {phase_name}** — failed",
                "delta": data,
            }
        # "started" or any other value → phase_started
        return {
            "kind": "phase_started",
            "summary_md": f"> **Phase: {phase_name}** — started",
            "delta": data,
        }

    if name == "workflow_step":
        step_name = data.get("name") or "?"
        status = data.get("status", "")
        if status == "failed":
            return {
                "kind": "step_failed",
                "summary_md": f"> ⚠ Step: {step_name} — failed",
                "delta": data,
            }
        # "completed" or any other terminal status
        return {
            "kind": "step_completed",
            "summary_md": f"> Step: {step_name} — completed",
            "delta": data,
        }

    if name == "qc_gate":
        metric = data.get("metric") or "?"
        value = data.get("value", "?")
        verdict = data.get("verdict") or "halt"
        return {
            "kind": "qc_gate",
            "summary_md": f"**QC gate — {metric}:** `{value}` → **{verdict}**",
            "delta": data,
        }

    if name == "auto_loop":
        param = data.get("param") or "?"
        was = data.get("was")
        now = data.get("now")
        return {
            "kind": "auto_loop_remediation",
            "summary_md": f"Auto-loop: `{param}` {was} → {now}",
            "delta": data,
        }

    if name == "done":
        summary = data.get("summary_md") or "Session complete."
        return {
            "kind": "done",
            "summary_md": summary,
            "view_url": data.get("view_url"),
            "delta": data,
        }

    # ── Internal BioMate SSE format (from /api/workflows/:id/events) ──────────

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

    if name == "complete":
        # Terminal event from POST /api/open-claw/stream (Node.js agentic loop).
        # data = {iterations: int}
        n_iter = data.get("iterations", 0) if isinstance(data, dict) else 0
        return {
            "kind": "done",
            "summary_md": f"Session complete ({n_iter} iteration{'s' if n_iter != 1 else ''}).",
            "delta": data,
        }

    # Ignore: connected, final, tool_event, ready, needs_input, error,
    # tool_call_start, tool_call_complete, tool_result
    # (not user-facing progress; handled directly in SessionRunner or not needed)
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

        # ── Stream all events from /api/open-claw/stream ──────────────────────
        # The server emits: text_delta, workflow_phase, workflow_step, qc_gate,
        # auto_loop, finding, done.  We normalize each one, emit a progress
        # notification, and on "done" break and send the final tools/call reply.

        for evt in self.client.open_claw_stream(goal=goal, inputs=inputs, experiment_id=experiment_id):
            ename = evt.get("event", "")
            edata = evt.get("data")
            if isinstance(edata, dict):
                # Harvest run_id and view_url from any event that carries them
                if edata.get("run_id"):
                    self._final_run_id = edata["run_id"]
                if edata.get("view_url"):
                    self._final_view_url = self._final_view_url or edata["view_url"]

            payload = _normalize_sse_event(evt)
            if payload is not None:
                n += 1
                # Capture view_url promoted by the normalizer (e.g. finding events)
                if payload.get("view_url"):
                    self._final_view_url = self._final_view_url or payload["view_url"]
                self._emit_progress(payload, n)
                if payload.get("kind") == "text_delta":
                    self._final_summary_md.append(payload.get("summary_md", ""))

            # "complete" = Node.js /api/open-claw/stream terminal event
            # "done"     = Galaxy backend / test-fixture terminal event
            if ename in ("done", "complete"):
                break

        # ── Final tools/call response ─────────────────────────────────────────
        summary = " ".join(self._final_summary_md).strip() or "Session complete."
        result_payload: Dict[str, Any] = {
            "summary_md": summary,
            "run_id": self._final_run_id,
            "view_url": self._final_view_url,
        }

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
            # Structured error: {"error": True, "code": ..., "human_message": ..., "debug": ...}
            # (error=True boolean, set by _classify_exc — distinct from {"error": <string>} from old code)
            is_err = isinstance(result, dict) and result.get("error") is True
            return make_response(req_id, {
                "content": [{"type": "text", "text": content_text}],
                "isError": is_err,
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
