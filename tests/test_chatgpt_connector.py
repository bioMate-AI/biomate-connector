"""
test_chatgpt_connector.py — Tests for the BioMate ChatGPT Actions adapter.

Two test classes:
  TestAdapterUnit  — mock BioMate server, no OpenAI key required (fast, CI-safe)
  TestGPT4Live     — real GPT-4 function-calling, requires OPENAI_API_KEY env var

The live tests verify that GPT-4 actually selects the right BioMate tool and
that the round-trip (GPT → adapter → mock BioMate → GPT) works end-to-end.
"""

import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from backend.lib.integrations.chatgpt_adapter import (
    dispatch_tool,
    _extract_bearer,
    _consume_chat_stream,
    handle_biomate_session,
    handle_search_workflow,
)

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")


# ─────────────────────────────────────────────────────────────────────────────
# Shared mock SSE server (same pattern as Slack / WeChat / Coze tests)
# ─────────────────────────────────────────────────────────────────────────────

def _make_sse_body(*events: tuple) -> bytes:
    lines = []
    for name, data in events:
        lines.append(f"event: {name}")
        lines.append(f"data: {json.dumps(data)}")
        lines.append("")
    return "\n".join(lines).encode("utf-8")


class _SSEHandler(BaseHTTPRequestHandler):
    response_body: bytes = b""
    response_status: int = 200

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length) if length else b""
        self.send_response(_SSEHandler.response_status)
        if _SSEHandler.response_status == 200:
            self.send_header("Content-Type", "text/event-stream")
        else:
            self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(_SSEHandler.response_body)

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(_SSEHandler.response_body)

    def log_message(self, *args):
        pass


class MockBioMateServer:
    def __init__(self, status: int = 200, events: list = None, json_body: dict = None):
        self.status = status
        if json_body is not None:
            self.body = json.dumps(json_body).encode()
            self.is_sse = False
        else:
            events = events or [("delta", {"text": "Answer."}), ("done", {})]
            self.body = _make_sse_body(*events)
            self.is_sse = True
        self.server = None
        self.port = 0

    def __enter__(self):
        _SSEHandler.response_status = self.status
        _SSEHandler.response_body = self.body
        self.server = HTTPServer(("127.0.0.1", 0), _SSEHandler)
        self.port = self.server.server_address[1]
        t = threading.Thread(target=self.server.serve_forever, daemon=True)
        t.start()
        return self

    def __exit__(self, *args):
        self.server.shutdown()

    @property
    def base_url(self):
        return f"http://127.0.0.1:{self.port}"


# ─────────────────────────────────────────────────────────────────────────────
# Unit tests (no OpenAI API key needed)
# ─────────────────────────────────────────────────────────────────────────────

class TestAdapterUnit(unittest.TestCase):

    def test_extract_bearer_parses_token(self):
        self.assertEqual(_extract_bearer("Bearer abc123"), "abc123")
        self.assertEqual(_extract_bearer(""), "")
        self.assertEqual(_extract_bearer("Basic xyz"), "")

    def test_consume_chat_stream_normal(self):
        events = [
            ("delta", {"text": "ADMET "}),
            ("delta", {"text": "screening plan."}),
            ("done", {}),
        ]
        with MockBioMateServer(events=events) as srv:
            answer, wf_name, view_url = _consume_chat_stream(
                "Screen aspirin", "", base_url=srv.base_url
            )
        self.assertIn("ADMET", answer)
        self.assertIsNone(wf_name)

    def test_consume_chat_stream_workflow_ready(self):
        events = [
            ("workflow_ready", {"workflow_name": "admet_screening"}),
            ("delta", {"text": "Found workflow."}),
            ("done", {}),
        ]
        with MockBioMateServer(events=events) as srv:
            _, wf_name, view_url = _consume_chat_stream(
                "ADMET screen aspirin", "", base_url=srv.base_url
            )
        self.assertEqual(wf_name, "admet_screening")
        self.assertIn("admet_screening", view_url)

    def test_consume_chat_stream_timeout(self):
        import requests as req
        with patch("backend.lib.integrations.chatgpt_adapter.requests.post",
                   side_effect=req.exceptions.Timeout()):
            answer, wf_name, _ = _consume_chat_stream("Query", "")
        self.assertIn("timed out", answer.lower())
        self.assertIsNone(wf_name)

    def test_handle_biomate_session_returns_answer(self):
        events = [
            ("workflow_ready", {"workflow_name": "nfcore_rnaseq"}),
            ("delta", {"text": "RNA-seq workflow selected."}),
            ("done", {}),
        ]
        with MockBioMateServer(events=events) as srv:
            with patch("backend.lib.integrations.chatgpt_adapter.BIOMATE_API_URL", srv.base_url):
                result = handle_biomate_session({"goal": "Run RNA-seq"}, "")

        self.assertFalse(result["isError"])
        content = json.loads(result["content"][0]["text"])
        self.assertIn("RNA-seq", content["answer"])
        self.assertEqual(content["workflow_name"], "nfcore_rnaseq")
        self.assertIn("nfcore_rnaseq", content["view_url"])

    def test_handle_biomate_session_empty_goal_returns_error(self):
        result = handle_biomate_session({"goal": ""}, "")
        self.assertTrue(result["isError"])

    def test_handle_search_workflow_fallback_to_chat(self):
        # When /api/workflows/search returns 404, falls back to chat stream
        events = [
            ("delta", {"text": "Found 3 workflows for ADMET."}),
            ("done", {}),
        ]
        with MockBioMateServer(events=events) as srv:
            result = handle_search_workflow(
                {"query": "ADMET screening", "limit": 3}, "", base_url=srv.base_url
            )
        self.assertFalse(result["isError"])

    def test_dispatch_unknown_tool_returns_error(self):
        result = dispatch_tool("nonexistent_tool", {}, "")
        self.assertTrue(result["isError"])
        self.assertIn("Unknown tool", result["content"][0]["text"])

    def test_dispatch_biomate_session_routes_correctly(self):
        events = [("delta", {"text": "Answer."}), ("done", {})]
        with MockBioMateServer(events=events) as srv:
            with patch("backend.lib.integrations.chatgpt_adapter.BIOMATE_API_URL", srv.base_url):
                result = dispatch_tool("biomate_session", {"goal": "Screen aspirin"}, "")
        self.assertFalse(result["isError"])

    def test_dispatch_get_run_proxies_correctly(self):
        runs_data = {"run_id": "run-123", "status": "completed"}
        with MockBioMateServer(json_body=runs_data) as srv:
            result = dispatch_tool("get_run", {"run_id": "run-123"}, "", base_url=srv.base_url)
        self.assertFalse(result["isError"])

    def test_dispatch_list_runs_proxies_correctly(self):
        with MockBioMateServer(json_body={"runs": []}) as srv:
            result = dispatch_tool("list_runs", {"limit": 5}, "", base_url=srv.base_url)
        self.assertFalse(result["isError"])


# ─────────────────────────────────────────────────────────────────────────────
# Live GPT-4 function-calling tests — skipped if OPENAI_API_KEY not set
# ─────────────────────────────────────────────────────────────────────────────

@unittest.skipUnless(OPENAI_API_KEY, "OPENAI_API_KEY not set — skipping live GPT-4 tests")
class TestGPT4Live(unittest.TestCase):
    """
    These tests use the real OpenAI API to verify that GPT-4 correctly selects
    and invokes BioMate tools for scientific questions.

    The BioMate backend is mocked — only the OpenAI calls are real.
    """

    # BioMate tools in OpenAI function-calling format (subset of openapi.json)
    TOOLS = [
        {
            "type": "function",
            "function": {
                "name": "biomate_session",
                "description": (
                    "Run a complete BioMate scientific session from a natural-language goal. "
                    "BioMate selects the best workflow (ADMET, RNA-seq, WGS, CryoEM, PBPK, etc.), "
                    "fills parameters, runs on AWS Batch, and returns findings. "
                    "Use this for 90% of scientific analysis requests."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "goal": {
                            "type": "string",
                            "description": "Natural language description of the scientific goal.",
                        },
                        "stream": {"type": "boolean", "default": True},
                    },
                    "required": ["goal"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "search_workflow",
                "description": (
                    "Search BioMate's catalog of 2,455 indexed workflows. "
                    "Use when the user wants to browse available workflows before running one."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "limit": {"type": "integer", "default": 5},
                        "domain": {"type": "string"},
                    },
                    "required": ["query"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_run",
                "description": "Get the status, progress, and findings of a BioMate run by run_id.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "run_id": {"type": "string"},
                        "include_findings": {"type": "boolean", "default": True},
                    },
                    "required": ["run_id"],
                },
            },
        },
    ]

    SYSTEM = (
        "You are the BioMate GPT. You help researchers run bioinformatics and drug discovery "
        "workflows on BioMate's cloud infrastructure. Use the biomate_session tool for analysis "
        "requests, search_workflow to browse the catalog, and get_run to check run status."
    )

    def _client(self):
        from openai import OpenAI
        return OpenAI(api_key=OPENAI_API_KEY)

    def _first_tool_call(self, user_message: str):
        """Send a message to GPT-4, return the first tool_calls entry (or None)."""
        client = self._client()
        resp = client.chat.completions.create(
            model="gpt-4o-mini",   # cheapest capable model; swap to gpt-4o for production
            messages=[
                {"role": "system", "content": self.SYSTEM},
                {"role": "user", "content": user_message},
            ],
            tools=self.TOOLS,
            tool_choice="auto",
            max_tokens=200,
        )
        msg = resp.choices[0].message
        if msg.tool_calls:
            return msg.tool_calls[0]
        return None

    # ── Test A: ADMET query → biomate_session called ──────────────────────────

    def test_admet_query_calls_biomate_session(self):
        tc = self._first_tool_call(
            "Screen aspirin (CC(=O)Oc1ccccc1C(=O)O) for hERG inhibition and CYP3A4 metabolism"
        )
        self.assertIsNotNone(tc, "GPT-4 should have called a tool")
        self.assertEqual(tc.function.name, "biomate_session")
        args = json.loads(tc.function.arguments)
        self.assertIn("goal", args)
        self.assertIn("aspirin", args["goal"].lower())

    # ── Test B: browse query → search_workflow called ─────────────────────────

    def test_browse_query_calls_search_workflow(self):
        tc = self._first_tool_call(
            "What workflows do you have for CryoEM single-particle analysis?"
        )
        self.assertIsNotNone(tc, "GPT-4 should have called a tool")
        self.assertEqual(tc.function.name, "search_workflow")
        args = json.loads(tc.function.arguments)
        self.assertIn("query", args)

    # ── Test C: RNA-seq query → biomate_session with goal containing RNA-seq ──

    def test_rnaseq_query_goal_contains_rnaseq(self):
        tc = self._first_tool_call(
            "Run nf-core/rnaseq differential expression on FASTQ files in s3://my-bucket/exp1/"
        )
        self.assertIsNotNone(tc)
        self.assertEqual(tc.function.name, "biomate_session")
        args = json.loads(tc.function.arguments)
        goal = args.get("goal", "").lower()
        self.assertTrue(
            "rnaseq" in goal or "rna" in goal or "expression" in goal,
            f"goal should mention RNA-seq but got: {args['goal']}"
        )

    # ── Test D: status query → get_run called ────────────────────────────────

    def test_run_status_query_calls_get_run(self):
        tc = self._first_tool_call("What's the status of run abc-123-xyz?")
        self.assertIsNotNone(tc)
        self.assertEqual(tc.function.name, "get_run")
        args = json.loads(tc.function.arguments)
        self.assertIn("run_id", args)

    # ── Test E: full round-trip — mock BioMate response fed back to GPT-4 ─────

    def test_full_roundtrip_with_mock_biomate(self):
        """
        Simulates the full GPT → tool call → mock BioMate → GPT → final answer loop.
        Verifies that GPT-4 can process BioMate's JSON response and produce
        a coherent final answer.
        """
        client = self._client()
        user_message = "Screen aspirin for ADMET properties"

        # Turn 1: user → GPT → tool call
        resp1 = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": self.SYSTEM},
                {"role": "user", "content": user_message},
            ],
            tools=self.TOOLS,
            tool_choice="auto",
            max_tokens=200,
        )
        msg1 = resp1.choices[0].message
        self.assertTrue(msg1.tool_calls, "GPT-4 should call a tool")

        tc = msg1.tool_calls[0]
        tool_result = json.dumps({
            "answer": (
                "BioMate has selected the admet_screening workflow. "
                "Aspirin shows low hERG inhibition risk (IC50 > 30 µM) "
                "and moderate CYP3A4 inhibition."
            ),
            "workflow_name": "admet_screening",
            "view_url": "https://app.biomate.ai?workflow=admet_screening",
        })

        # Turn 2: tool result → GPT → natural language reply
        resp2 = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": self.SYSTEM},
                {"role": "user", "content": user_message},
                msg1,  # assistant message with tool_calls
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": tool_result,
                },
            ],
            tools=self.TOOLS,
            max_tokens=300,
        )
        final_text = resp2.choices[0].message.content or ""
        self.assertGreater(len(final_text), 20, "GPT-4 should produce a non-trivial final answer")
        # GPT should mention ADMET or aspirin in the final answer
        self.assertTrue(
            "admet" in final_text.lower() or "aspirin" in final_text.lower() or "herg" in final_text.lower(),
            f"Final answer doesn't mention key terms: {final_text[:200]}"
        )
        print(f"\n[GPT-4 final answer]: {final_text[:300]}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
