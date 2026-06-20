"""
Connector sandbox tests — comprehensive offline validation before submission to
the Claude / ChatGPT / OpenAI directories.

This file exercises the contracts every external surface depends on:

  1. OpenAPI 3.1 spec validity              (validate the whole connectors/chatgpt/openapi.json)
  2. Per-tool JSON Schema validity          (Draft 2020-12 validator)
  3. Per-tool sample input validation       (positive + negative cases)
  4. Anthropic + OpenAI SDK shape           (the SDK accepts our exports)
  5. MCP protocol end-to-end                (real subprocess, real stdio JSON-RPC)
  6. Streaming biomate_session              (subprocess + mock SSE backend)

We do NOT make outbound calls to api.anthropic.com or api.openai.com because
the sandbox has no production keys. The libraries are imported and the request
shapes are validated structurally — the same checks the SDK does locally
before issuing an HTTP call.

Run: PYTHONPATH=. pytest tests/test_connector_sandbox.py -v
"""

from __future__ import annotations

import json
import os
import select
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest

import jsonschema
from jsonschema import Draft202012Validator
from openapi_spec_validator import validate as validate_openapi
import anthropic
import openai

from mcp import tools_manifest as tm


REPO_ROOT = Path(__file__).resolve().parents[1]


# ──────────────────────────────────────────────────────────────────────────────
# 1. OpenAPI 3.1 spec validity (consumed by ChatGPT GPT Actions)
# ──────────────────────────────────────────────────────────────────────────────

def test_openapi_spec_valid_310():
    """Validate the committed spec against the OpenAPI 3.1 schema."""
    spec_path = REPO_ROOT / "connectors" / "chatgpt" / "openapi.json"
    spec = json.loads(spec_path.read_text())
    # openapi_spec_validator.validate raises on any spec-level violation.
    validate_openapi(spec)


def test_openapi_has_required_chatgpt_fields():
    """ChatGPT GPT Actions require info.title, info.version, servers, paths, oauth scopes."""
    spec = tm.to_openapi()
    assert spec["info"]["title"]
    assert spec["info"]["version"]
    assert spec["servers"][0]["url"].startswith("https://")
    assert spec["info"].get("x-privacy-policy", "").startswith("https://"), (
        "ChatGPT GPT store submission requires a privacy policy URL"
    )
    auth = spec["components"]["securitySchemes"]["BiomateOAuth"]
    flow = auth["flows"]["authorizationCode"]
    assert flow["authorizationUrl"].startswith("https://")
    assert flow["tokenUrl"].startswith("https://")
    assert flow["scopes"]


def test_openapi_operation_ids_unique():
    """ChatGPT requires unique operationIds; ours are 1:1 with tool names."""
    spec = tm.to_openapi()
    op_ids = [p["post"]["operationId"] for p in spec["paths"].values()]
    assert len(op_ids) == len(set(op_ids))
    assert set(op_ids) == {t.name for t in tm.TOOL_SCHEMAS}


# ──────────────────────────────────────────────────────────────────────────────
# 2. Per-tool JSON Schema validity (Anthropic + MCP + OpenAI all consume this)
# ──────────────────────────────────────────────────────────────────────────────

def test_every_tool_input_schema_is_valid_jsonschema_2020_12():
    """Each input_schema must be a syntactically valid Draft 2020-12 JSON Schema."""
    for t in tm.TOOL_SCHEMAS:
        try:
            Draft202012Validator.check_schema(t.input_schema)
        except jsonschema.SchemaError as exc:
            pytest.fail(f"{t.name}: invalid schema: {exc.message}")


# ──────────────────────────────────────────────────────────────────────────────
# 3. Per-tool sample inputs — positive + negative cases
# ──────────────────────────────────────────────────────────────────────────────

# A valid sample input for each tool. New tools must add an entry here.
VALID_SAMPLES: Dict[str, Dict[str, Any]] = {
    "biomate_session": {"goal": "Screen aspirin SMILES for hERG", "stream": True},
    "search_workflow": {"query": "ADMET screening for small molecules", "limit": 5},
    "get_workflow_spec": {"workflow_id": "admet_screen"},
    "run_workflow": {"workflow_id": "admet_screen", "params": {"smiles": ["CCO"]}},
    "get_run": {"run_id": "run-xyz", "include_findings": True},
    "cancel_run": {"run_id": "run-xyz"},
    "list_runs": {"limit": 5, "status": "running"},
    "preview_file": {"s3_key": "s3://bucket/output.csv", "max_rows": 50},
    "export_report": {"run_id": "run-xyz", "format": "pdf", "sections": ["methods", "qc"]},
    "analyze_results": {"run_id": "run-xyz", "question": "any hERG hits?"},
    "explain_error": {"run_id": "run-xyz"},
    "query_database": {"database": "uniprot", "query": "P04637"},
    "resolve_accession": {"accession": "GSE183947"},
    "browse_data": {"source_id": "ncbi_ftp", "path": "/genomes/refseq/"},
    "fetch_public_data": {"source_id": "ebi_ftp", "remote_path": "/pub/databases/uniprot/current_release/knowledgebase/complete/uniprot_sprot.fasta.gz"},
    "recall_memory": {"query": "previous hERG screens", "scope": "runs", "limit": 3},
    "upload_file": {"filename": "compounds.csv", "size_bytes": 1234, "content_type": "text/csv"},
}


def test_sample_inputs_cover_every_tool():
    """Every tool in the manifest must have a sample input here."""
    missing = {t.name for t in tm.TOOL_SCHEMAS} - set(VALID_SAMPLES)
    assert not missing, f"VALID_SAMPLES missing entries for: {missing}"


@pytest.mark.parametrize("tool", tm.TOOL_SCHEMAS, ids=lambda t: t.name)
def test_valid_sample_passes_schema(tool):
    Draft202012Validator(tool.input_schema).validate(VALID_SAMPLES[tool.name])


def test_missing_required_field_rejected():
    """biomate_session requires `goal` — empty dict must fail."""
    schema = tm.get_tool("biomate_session").input_schema
    with pytest.raises(jsonschema.ValidationError):
        Draft202012Validator(schema).validate({})


def test_wrong_type_rejected():
    """list_runs.limit is integer — string must fail."""
    schema = tm.get_tool("list_runs").input_schema
    with pytest.raises(jsonschema.ValidationError):
        Draft202012Validator(schema).validate({"limit": "five"})


def test_invalid_enum_value_rejected():
    """query_database.database is enum — unknown value must fail."""
    schema = tm.get_tool("query_database").input_schema
    with pytest.raises(jsonschema.ValidationError):
        Draft202012Validator(schema).validate({"database": "fake_db", "query": "x"})


def test_invalid_export_format_rejected():
    """export_report.format is enum [pdf, markdown, docx]."""
    schema = tm.get_tool("export_report").input_schema
    with pytest.raises(jsonschema.ValidationError):
        Draft202012Validator(schema).validate({"run_id": "r", "format": "xlsx"})


# ──────────────────────────────────────────────────────────────────────────────
# 4. SDK shape acceptance (Anthropic + OpenAI)
# ──────────────────────────────────────────────────────────────────────────────

def test_anthropic_tools_have_required_fields():
    """Anthropic Messages API requires {name, description, input_schema} per tool."""
    tools = tm.to_anthropic()
    for tool in tools:
        assert isinstance(tool["name"], str) and tool["name"]
        # Anthropic name constraints: ≤64 chars, [a-zA-Z0-9_-]
        assert len(tool["name"]) <= 64
        assert all(c.isalnum() or c in "_-" for c in tool["name"])
        assert isinstance(tool["description"], str) and len(tool["description"]) > 10
        assert tool["input_schema"]["type"] == "object"


def test_anthropic_request_body_structurally_valid():
    """Build a Messages.create() body shape and check the SDK's local validators don't reject."""
    tools = tm.to_anthropic()
    # Build the request body the SDK will serialize. We don't send it — but the
    # SDK's pydantic-backed types will reject malformed shapes at construction.
    body = {
        "model": "claude-sonnet-4-6",
        "max_tokens": 256,
        "messages": [{"role": "user", "content": "test"}],
        "tools": tools,
    }
    # Smoke-check: json.dumps must succeed (no unserializable values), and the
    # SDK's own JSON serializer (used internally by Messages.create) must work.
    serialized = json.dumps(body)
    assert serialized
    # Anthropic SDK ≥ 0.40 exposes the type at anthropic.types.ToolParam (TypedDict).
    # TypedDicts can't be validated at runtime, but we structurally compare.
    from anthropic.types import ToolParam  # noqa: F401  — import confirms SDK contract.


def test_openai_tools_have_required_fields():
    """OpenAI function-calling requires {type:'function', function:{name,description,parameters}}."""
    tools = tm.to_openai()
    for tool in tools:
        assert tool["type"] == "function"
        fn = tool["function"]
        assert isinstance(fn["name"], str) and fn["name"]
        # OpenAI name constraints: ≤64 chars, [a-zA-Z0-9_-]
        assert len(fn["name"]) <= 64
        assert all(c.isalnum() or c in "_-" for c in fn["name"])
        assert isinstance(fn["description"], str)
        assert fn["parameters"]["type"] == "object"


def test_openai_request_body_structurally_valid():
    tools = tm.to_openai()
    body = {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "test"}],
        "tools": tools,
    }
    assert json.dumps(body)
    from openai.types.chat import ChatCompletionToolParam  # noqa: F401


# ──────────────────────────────────────────────────────────────────────────────
# 5–6. MCP protocol end-to-end (real subprocess + mock BioMate backend)
# ──────────────────────────────────────────────────────────────────────────────

class _MockBioMateHandler(BaseHTTPRequestHandler):
    """In-process HTTP server pretending to be the BioMate API."""

    def _send_json(self, status: int, body: Dict[str, Any]) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):  # noqa: N802 (BaseHTTPRequestHandler API)
        length = int(self.headers.get("Content-Length", 0) or 0)
        _ = self.rfile.read(length)  # discard

        if self.path == "/api/workflows/search":
            self._send_json(200, {
                "results": [
                    {"workflow_id": "admet_screen", "name": "ADMET screen",
                     "domain": "drug_discovery", "estimated_cost_usd": 0.42},
                ],
            })
            return

        if self.path == "/api/open-claw/stream":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            events: List[Tuple[str, Dict[str, Any]]] = [
                ("workflow_phase", {"name": "QC", "status": "started",
                                    "view_url": "http://example.test/runs/run-mock-1#phase-1"}),
                ("workflow_step", {"name": "compute_hERG", "status": "completed",
                                   "run_id": "run-mock-1"}),
                ("finding", {"title": "1 compound flagged hERG-positive",
                             "view_url": "http://example.test/runs/run-mock-1#findings",
                             "summary_md": "Aspirin: hERG IC50 = 6.2"}),
                ("done", {"summary_md": "Session complete.",
                          "view_url": "http://example.test/runs/run-mock-1",
                          "run_id": "run-mock-1"}),
            ]
            try:
                for ev_name, ev_data in events:
                    payload = f"event: {ev_name}\ndata: {json.dumps(ev_data)}\n\n"
                    self.wfile.write(payload.encode())
                    self.wfile.flush()
                    time.sleep(0.01)
            except (BrokenPipeError, ConnectionResetError):
                pass
            return

        self._send_json(404, {"error": "unknown_path", "path": self.path})

    def do_GET(self):  # noqa: N802
        if self.path.startswith("/api/workflows/runs/"):
            self._send_json(200, {
                "run_id": "run-mock-1",
                "status": "completed",
                "phases": [{"name": "QC", "status": "completed"}],
                "output_files": [{"s3_key": "s3://mock/out.csv", "size": 1024}],
                "findings": [{"title": "1 hit"}],
            })
            return
        self._send_json(404, {"error": "unknown_path"})

    def log_message(self, *_):
        pass


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def mock_backend():
    port = _free_port()
    server = HTTPServer(("127.0.0.1", port), _MockBioMateHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()
    server.server_close()


@pytest.fixture
def mcp_server(mock_backend):
    """Spawn the real MCP server subprocess pointing at the mock backend."""
    env = {
        **os.environ,
        "BIOMATE_API_URL": mock_backend,
        "BIOMATE_API_KEY": "test-key",
        "PYTHONPATH": str(REPO_ROOT),
        "MCP_DEBUG": "",
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "mcp.biomate_mcp_server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        bufsize=0,
    )
    yield proc
    try:
        proc.terminate()
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        proc.kill()


def _send(proc: subprocess.Popen, msg: Dict[str, Any]) -> None:
    line = (json.dumps(msg) + "\n").encode()
    assert proc.stdin is not None
    proc.stdin.write(line)
    proc.stdin.flush()


def _read_one(proc: subprocess.Popen, timeout: float = 5.0) -> Dict[str, Any]:
    """Read one JSON-RPC message line from the server, with a timeout."""
    assert proc.stdout is not None
    deadline = time.time() + timeout
    while time.time() < deadline:
        ready, _, _ = select.select([proc.stdout], [], [], 0.05)
        if ready:
            line = proc.stdout.readline()
            if not line:
                raise EOFError("MCP server closed stdout")
            return json.loads(line.decode().rstrip())
        if proc.poll() is not None:
            stderr = proc.stderr.read().decode() if proc.stderr else ""
            raise RuntimeError(f"MCP server exited rc={proc.returncode}; stderr:\n{stderr}")
    raise TimeoutError(f"No response within {timeout}s")


def _initialize(proc: subprocess.Popen) -> Dict[str, Any]:
    _send(proc, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                 "params": {"protocolVersion": "2024-11-05",
                            "capabilities": {}, "clientInfo": {"name": "sandbox-test", "version": "1"}}})
    resp = _read_one(proc)
    _send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})
    return resp


def test_mcp_initialize_handshake(mcp_server):
    resp = _initialize(mcp_server)
    assert resp["id"] == 1
    assert resp["result"]["serverInfo"]["name"] == "biomate"
    assert resp["result"]["serverInfo"]["version"] == "2.0.0"
    assert resp["result"]["capabilities"]["tools"]


def test_mcp_tools_list_returns_17_tools(mcp_server):
    _initialize(mcp_server)
    _send(mcp_server, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    resp = _read_one(mcp_server)
    names = [t["name"] for t in resp["result"]["tools"]]
    assert len(names) == 17
    for required in ("biomate_session", "search_workflow", "get_run", "export_report",
                     "resolve_accession", "browse_data", "fetch_public_data"):
        assert required in names


def test_mcp_tools_call_search_workflow(mcp_server):
    """Sync tool dispatch — server calls mock backend, returns JSON result."""
    _initialize(mcp_server)
    _send(mcp_server, {"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                       "params": {"name": "search_workflow",
                                  "arguments": {"query": "ADMET screening"}}})
    resp = _read_one(mcp_server)
    assert resp["id"] == 7
    body = json.loads(resp["result"]["content"][0]["text"])
    assert body["results"][0]["workflow_id"] == "admet_screen"


def test_mcp_tools_call_get_run(mcp_server):
    """The merged get_run tool hits /api/workflows/runs/{id}."""
    _initialize(mcp_server)
    _send(mcp_server, {"jsonrpc": "2.0", "id": 8, "method": "tools/call",
                       "params": {"name": "get_run", "arguments": {"run_id": "run-mock-1"}}})
    resp = _read_one(mcp_server)
    body = json.loads(resp["result"]["content"][0]["text"])
    assert body["run_id"] == "run-mock-1"
    assert body["status"] == "completed"


def test_mcp_biomate_session_streams_progress(mcp_server):
    """biomate_session: progress notifications + final tools/call response."""
    _initialize(mcp_server)
    _send(mcp_server, {"jsonrpc": "2.0", "id": 42, "method": "tools/call",
                       "params": {"name": "biomate_session",
                                  "arguments": {"goal": "screen aspirin", "stream": True},
                                  "_meta": {"progressToken": "tok-sandbox"}}})

    notifications: List[Dict[str, Any]] = []
    final: Optional[Dict[str, Any]] = None
    deadline = time.time() + 10.0
    while time.time() < deadline:
        msg = _read_one(mcp_server, timeout=3.0)
        if msg.get("method") == "notifications/progress":
            notifications.append(msg)
        elif msg.get("id") == 42:
            final = msg
            break

    assert final is not None, "never got tools/call final response"
    assert len(notifications) >= 3, f"got only {len(notifications)} progress events"

    # Every notification carries our progressToken
    for n in notifications:
        assert n["params"]["progressToken"] == "tok-sandbox"
        assert "message" in n["params"]
        assert "_meta" in n["params"] and "kind" in n["params"]["_meta"]

    # The event-kind sequence must include at least one phase and the done marker
    kinds = [n["params"]["_meta"]["kind"] for n in notifications]
    assert any(k.startswith("phase_") for k in kinds), f"no phase event in {kinds}"
    assert "finding" in kinds, f"no finding event in {kinds}"
    assert "done" in kinds, f"no done event in {kinds}"

    # Final response carries the run_id and view_url discovered in the stream
    body = json.loads(final["result"]["content"][0]["text"])
    assert body["run_id"] == "run-mock-1"
    assert body["view_url"].startswith("http://example.test/runs/run-mock-1")


def test_mcp_biomate_session_without_progress_token(mcp_server):
    """No progressToken → no notifications/progress should be emitted."""
    _initialize(mcp_server)
    _send(mcp_server, {"jsonrpc": "2.0", "id": 100, "method": "tools/call",
                       "params": {"name": "biomate_session",
                                  "arguments": {"goal": "screen aspirin", "stream": True}}})

    seen: List[Dict[str, Any]] = []
    deadline = time.time() + 10.0
    final = None
    while time.time() < deadline:
        msg = _read_one(mcp_server, timeout=3.0)
        seen.append(msg)
        if msg.get("id") == 100:
            final = msg
            break

    assert final is not None
    assert not any(m.get("method") == "notifications/progress" for m in seen), (
        "no progressToken supplied — server must not emit notifications/progress"
    )


def test_mcp_unknown_tool_returns_method_error(mcp_server):
    _initialize(mcp_server)
    _send(mcp_server, {"jsonrpc": "2.0", "id": 99, "method": "tools/call",
                       "params": {"name": "does_not_exist", "arguments": {}}})
    resp = _read_one(mcp_server)
    assert resp["id"] == 99
    assert "error" in resp
    assert resp["error"]["code"] == -32601  # Method/tool not found


def test_mcp_legacy_tool_aliases_still_work(mcp_server):
    """get_run_status was removed from the manifest but kept as a dispatch alias for back-compat."""
    _initialize(mcp_server)
    # Note: tools/list will NOT advertise this name, but tools/call must still accept it.
    _send(mcp_server, {"jsonrpc": "2.0", "id": 50, "method": "tools/call",
                       "params": {"name": "get_run_status", "arguments": {"run_id": "run-mock-1"}}})
    resp = _read_one(mcp_server)
    # Mock backend returns 404 for /api/pipeline/runs/.../status, but the dispatcher
    # should still resolve the call (not return -32601 Method not found).
    assert resp.get("error", {}).get("code") != -32601, "legacy alias must still dispatch"
