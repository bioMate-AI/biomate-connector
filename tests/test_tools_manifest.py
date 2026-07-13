"""
Tests for the canonical connector tools manifest and streaming bridge.

Run: pytest backend/tests/test_tools_manifest.py -v
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import pytest

from mcp import tools_manifest as tm
from mcp import biomate_mcp_server as srv


# ──────────────────────────────────────────────────────────────────────────────
# Manifest schema
# ──────────────────────────────────────────────────────────────────────────────

def test_manifest_has_expected_tiers():
    tiers = {t.tier for t in tm.TOOL_SCHEMAS}
    assert tiers == {"agentic", "workflow", "analysis", "instrument"}
    # instrument-tier tools are local-only (dispatched in-process, not proxied
    # to the cloud backend which cannot reach a robot on the lab LAN).
    assert all(t.local_only for t in tm.TOOL_SCHEMAS if t.tier == "instrument")


def test_local_only_tools_in_mcp_but_not_cloud():
    """local_only instrument tools are exposed over local MCP only — never on
    the cloud surfaces (ChatGPT / Open Claw), which cannot reach the hardware."""
    mcp_names = {t["name"] for t in tm.to_mcp()}
    oai_names = {t["function"]["name"] for t in tm.to_openai()}
    ant_names = {t["name"] for t in tm.to_anthropic()}
    local = [t for t in tm.TOOL_SCHEMAS if t.local_only]
    assert local, "expected at least one local-only instrument tool"
    for t in local:
        assert t.name in mcp_names, f"{t.name} should be in the local MCP list"
        assert t.name not in oai_names, f"{t.name} must not be on the ChatGPT surface"
        assert t.name not in ant_names, f"{t.name} must not be on the Open Claw surface"


def test_manifest_has_streaming_session_tool():
    sess = tm.get_tool("biomate_session")
    assert sess is not None
    assert sess.streaming is True
    assert sess.tier == "agentic"


def test_every_tool_has_name_description_inputschema():
    for t in tm.TOOL_SCHEMAS:
        assert t.name and t.description and t.input_schema
        assert t.input_schema.get("type") == "object"
        assert "properties" in t.input_schema


def test_tool_names_unique():
    names = [t.name for t in tm.TOOL_SCHEMAS]
    assert len(names) == len(set(names))


def test_required_fields_exist_in_properties():
    for t in tm.TOOL_SCHEMAS:
        props = t.input_schema.get("properties", {})
        for req in t.input_schema.get("required", []):
            assert req in props, f"{t.name}: required field '{req}' not in properties"


# ──────────────────────────────────────────────────────────────────────────────
# Exporters
# ──────────────────────────────────────────────────────────────────────────────

def test_mcp_export_uses_camelcase_inputschema():
    for tool in tm.to_mcp():
        assert "inputSchema" in tool
        assert "input_schema" not in tool


def test_anthropic_export_uses_snake_case_input_schema():
    for tool in tm.to_anthropic():
        assert "input_schema" in tool
        assert "inputSchema" not in tool


def test_openai_export_wraps_in_function_type():
    for tool in tm.to_openai():
        assert tool["type"] == "function"
        assert "name" in tool["function"]
        assert "parameters" in tool["function"]


def test_openapi_export_has_path_per_tool():
    spec = tm.to_openapi()
    assert spec["openapi"].startswith("3.1")
    # The cloud HTTP surface exposes every non-local tool (local_only instrument
    # tools are excluded — the cloud cannot reach lab hardware).
    cloud_tools = [t for t in tm.TOOL_SCHEMAS if not t.local_only]
    assert len(spec["paths"]) == len(cloud_tools)
    for t in cloud_tools:
        path = f"/tools/{t.name}"
        assert path in spec["paths"]
        op = spec["paths"][path]["post"]
        assert op["operationId"] == t.name
        assert op["x-streaming"] == t.streaming
        body = op["requestBody"]["content"]["application/json"]["schema"]
        assert body == t.input_schema
    for t in tm.TOOL_SCHEMAS:
        if t.local_only:
            assert f"/tools/{t.name}" not in spec["paths"]


def test_openapi_uses_oauth_with_required_scopes():
    spec = tm.to_openapi()
    auth = spec["components"]["securitySchemes"]["BiomateOAuth"]
    assert auth["type"] == "oauth2"
    scopes = auth["flows"]["authorizationCode"]["scopes"]
    for required in ("runs:read", "runs:write", "workflows:search"):
        assert required in scopes


def test_committed_openapi_in_sync():
    """Drift test for the ChatGPT GPT Actions OpenAPI spec."""
    committed = Path("connectors/chatgpt/openapi.json")
    if not committed.exists():
        pytest.skip("Committed OpenAPI not present yet")
    current = json.loads(committed.read_text())
    assert current == tm.to_openapi(), (
        "connectors/chatgpt/openapi.json is out of sync with tools_manifest.py. "
        "Regenerate: `python -m mcp.tools_manifest`"
    )


def test_manifest_json_round_trips(tmp_path):
    out = tm.build_manifest_json(tmp_path / "m.json")
    payload = json.loads(out.read_text())
    assert payload["version"] == "2.0.0"
    # MCP (local) carries every tool; the cloud shapes exclude local_only tools.
    assert len(payload["mcp"]) == len(tm.TOOL_SCHEMAS)
    cloud_tools = [t for t in tm.TOOL_SCHEMAS if not t.local_only]
    assert len(payload["anthropic"]) == len(cloud_tools)
    assert len(payload["openai"]) == len(cloud_tools)


def test_committed_manifest_json_in_sync():
    """Drift test: regenerate the committed JSON and diff. Fails if out of sync."""
    committed = Path("mcp/tools_manifest.json")
    if not committed.exists():
        pytest.skip("Committed manifest not present yet")
    current = json.loads(committed.read_text())
    fresh = {
        "version": "2.0.0",
        "generated_from": "mcp/tools_manifest.py",
        "server": tm.to_server_info(),
        "mcp": tm.to_mcp(),
        "anthropic": tm.to_anthropic(),
        "openai": tm.to_openai(),
        "lite": {
            "mcp": tm.to_lite_mcp(),
            "anthropic": tm.to_lite_anthropic(),
            "openai": tm.to_lite_openai(),
            "tool_names": sorted(tm.LITE_TOOL_NAMES),
        },
        "backend_routes": [
            {
                "name": t.name,
                "method": t.backend_method,
                "path": t.backend_path,
                "streaming": t.streaming,
            }
            for t in tm.TOOL_SCHEMAS
        ],
    }
    assert current == fresh, (
        "tools_manifest.json is out of sync with tools_manifest.py. "
        "Regenerate: `python -m mcp.tools_manifest`"
    )


# ──────────────────────────────────────────────────────────────────────────────
# Skill catalog
# ──────────────────────────────────────────────────────────────────────────────

def test_skill_catalog_lists_every_cloud_tool():
    md = tm.to_skill_catalog()
    for t in tm.TOOL_SCHEMAS:
        if t.local_only:
            # local-only instrument tools are not part of the cloud Skill catalog
            assert f"`{t.name}`" not in md
        else:
            assert f"`{t.name}`" in md


def test_skill_catalog_marks_streaming_tools():
    md = tm.to_skill_catalog()
    sess_section = md.split("### `biomate_session`")[1].split("###")[0]
    assert "**streams**" in sess_section


# ──────────────────────────────────────────────────────────────────────────────
# MCP server uses the manifest
# ──────────────────────────────────────────────────────────────────────────────

def test_server_tools_match_manifest():
    server_names = [t["name"] for t in srv.TOOLS]
    manifest_names = [t.name for t in tm.TOOL_SCHEMAS]
    assert server_names == manifest_names


def test_streaming_tool_classification():
    assert srv.is_streaming_call("biomate_session", {}) is True
    assert srv.is_streaming_call("biomate_session", {"stream": False}) is False
    assert srv.is_streaming_call("run_workflow", {}) is False
    assert srv.is_streaming_call("run_workflow", {"stream": True}) is True
    assert srv.is_streaming_call("search_workflow", {}) is False


# ──────────────────────────────────────────────────────────────────────────────
# SSE → progress payload normalization
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "evt,expected_kind",
    [
        ({"event": "text_delta", "data": {"text": "hi"}}, "text_delta"),
        ({"event": "workflow_phase", "data": {"name": "QC", "status": "started"}}, "phase_started"),
        ({"event": "workflow_phase", "data": {"name": "QC", "status": "completed"}}, "phase_completed"),
        ({"event": "workflow_step", "data": {"name": "BWA-MEM", "status": "completed"}}, "step_completed"),
        ({"event": "workflow_step", "data": {"name": "BWA-MEM", "status": "failed"}}, "step_failed"),
        ({"event": "qc_gate", "data": {"metric": "hERG", "value": 6.2, "verdict": "halt"}}, "qc_gate"),
        ({"event": "auto_loop", "data": {"param": "n_steps", "was": 1000, "now": 5000}}, "auto_loop_remediation"),
        ({"event": "finding", "data": {"title": "Top hit", "summary_md": "..."}}, "finding"),
        ({"event": "done", "data": {"summary_md": "ok"}}, "done"),
    ],
)
def test_normalize_sse_event_kinds(evt: Dict[str, Any], expected_kind: str):
    out = srv._normalize_sse_event(evt)
    assert out is not None
    assert out["kind"] == expected_kind
    assert "summary_md" in out


def test_normalize_sse_event_ignores_internal_events():
    # Tool calls inside Open Claw aren't user-facing — they should be filtered.
    assert srv._normalize_sse_event({"event": "tool_use", "data": {}}) is None
    assert srv._normalize_sse_event({"event": "unknown_event", "data": {}}) is None


def test_normalize_sse_event_handles_non_dict_data():
    out = srv._normalize_sse_event({"event": "text_delta", "data": "plain string"})
    assert out is not None
    assert out["kind"] == "text_delta"


# ──────────────────────────────────────────────────────────────────────────────
# SessionRunner emits notifications/progress for each event
# ──────────────────────────────────────────────────────────────────────────────

class _FakeClient:
    """Stub BioMateClient that yields a canned SSE sequence."""

    def __init__(self, events: List[Dict[str, Any]]):
        self.events = events

    def open_claw_stream(self, goal, inputs=None, experiment_id=None):
        for e in self.events:
            yield e


def test_session_runner_emits_progress_and_final(monkeypatch):
    sent: List[Dict[str, Any]] = []
    monkeypatch.setattr(srv, "send", lambda obj: sent.append(obj))

    fake = _FakeClient([
        {"event": "workflow_phase", "data": {"name": "Align", "status": "started"}},
        {"event": "workflow_step", "data": {"name": "BWA-MEM", "status": "completed", "run_id": "run-xyz"}},
        {"event": "finding", "data": {"title": "12 variants pass", "view_url": "https://biomate.ai/runs/run-xyz"}},
        {"event": "done", "data": {"summary_md": "All phases complete."}},
    ])

    runner = srv.SessionRunner(
        client=fake,
        req_id=42,
        tool_name="biomate_session",
        tool_args={"goal": "test"},
        progress_token="tok-1",
    )
    runner.run()  # Synchronous in-test — bypasses the thread start.

    progress = [m for m in sent if m.get("method") == "notifications/progress"]
    assert len(progress) == 4, f"expected 4 progress events, got {len(progress)}: {progress}"
    for p in progress:
        assert p["params"]["progressToken"] == "tok-1"
        assert "message" in p["params"]
        assert "_meta" in p["params"]

    # Final tools/call response
    finals = [m for m in sent if m.get("id") == 42 and "result" in m]
    assert len(finals) == 1
    content = finals[0]["result"]["content"][0]["text"]
    payload = json.loads(content)
    assert payload["run_id"] == "run-xyz"
    assert payload["view_url"] == "https://biomate.ai/runs/run-xyz"


def test_session_runner_omits_progress_without_token(monkeypatch):
    """Hosts without progress support pass no progressToken → no notifications sent."""
    sent: List[Dict[str, Any]] = []
    monkeypatch.setattr(srv, "send", lambda obj: sent.append(obj))

    fake = _FakeClient([
        {"event": "workflow_step", "data": {"name": "X", "status": "completed"}},
        {"event": "done", "data": {}},
    ])

    runner = srv.SessionRunner(fake, req_id=1, tool_name="biomate_session", tool_args={"goal": "x"}, progress_token=None)
    runner.run()

    assert not any(m.get("method") == "notifications/progress" for m in sent)
    # Still gets a final response.
    assert any(m.get("id") == 1 for m in sent)
