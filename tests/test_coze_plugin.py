"""
test_coze_plugin.py — Unit tests for the BioMate Coze plugin adapter.

Tests chat_stream_query(), handle_query(), and auth using a lightweight
mock SSE server (http.server in a thread), mirroring the pattern used
for test_slack_bot.py and test_wechat_open_claw.py.
"""

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from backend.lib.integrations.coze_plugin import (
    chat_stream_query,
    handle_query,
    verify_plugin_key,
    _get_history,
    _push_history,
    _session_history,
)


# ─────────────────────────────────────────────────────────────────────────────
# Mock SSE server (shared pattern with Slack / WeChat tests)
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

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length) if length else b""
        self.send_response(_SSEHandler.response_status)
        if _SSEHandler.response_status == 200:
            self.send_header("Content-Type", "text/event-stream")
        else:
            self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(_SSEHandler.response_body)

    def log_message(self, *args):
        pass


class MockSSEServer:
    def __init__(self, status: int = 200, events: list = None):
        self.status = status
        self.events = events or [
            ("delta", {"text": "Hello "}),
            ("delta", {"text": "world."}),
            ("done", {}),
        ]
        self.server: HTTPServer = None
        self.port: int = 0

    def __enter__(self):
        _SSEHandler.response_status = self.status
        _SSEHandler.response_body = _make_sse_body(*self.events)
        self.server = HTTPServer(("127.0.0.1", 0), _SSEHandler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.server.shutdown()

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


# ─────────────────────────────────────────────────────────────────────────────
# Auth tests
# ─────────────────────────────────────────────────────────────────────────────

class TestAuth(unittest.TestCase):

    def test_no_secret_configured_allows_any_key(self):
        with patch("backend.lib.integrations.coze_plugin.COZE_PLUGIN_SECRET", ""):
            self.assertTrue(verify_plugin_key("anything"))
            self.assertTrue(verify_plugin_key(""))

    def test_correct_key_allowed(self):
        with patch("backend.lib.integrations.coze_plugin.COZE_PLUGIN_SECRET", "secret123"):
            self.assertTrue(verify_plugin_key("secret123"))

    def test_wrong_key_rejected(self):
        with patch("backend.lib.integrations.coze_plugin.COZE_PLUGIN_SECRET", "secret123"):
            self.assertFalse(verify_plugin_key("wrong"))
            self.assertFalse(verify_plugin_key(""))

    def test_handle_query_returns_401_on_bad_key(self):
        with patch("backend.lib.integrations.coze_plugin.COZE_PLUGIN_SECRET", "secret123"):
            result = handle_query("Find RNA-seq workflow", plugin_key="bad")
        self.assertEqual(result.get("status"), 401)
        self.assertIn("Unauthorized", result.get("error", ""))

    def test_handle_query_returns_400_on_empty_query(self):
        result = handle_query("", plugin_key="")
        self.assertEqual(result.get("status"), 400)


# ─────────────────────────────────────────────────────────────────────────────
# chat_stream_query tests
# ─────────────────────────────────────────────────────────────────────────────

class TestChatStreamQuery(unittest.TestCase):

    def setUp(self):
        _session_history.clear()

    # ── Test 1: delta events accumulate into answer ───────────────────────────

    def test_normal_query_returns_text(self):
        events = [
            ("delta", {"text": "ADMET "}),
            ("delta", {"text": "screening complete."}),
            ("done", {}),
        ]
        with MockSSEServer(events=events) as srv:
            answer, wf_name, view_url = chat_stream_query(
                "sess1", "Screen aspirin", _base_url_override=srv.base_url
            )
        self.assertIn("ADMET", answer)
        self.assertIsNone(wf_name)
        self.assertIsNone(view_url)

    # ── Test 2: done stops stream early ──────────────────────────────────────

    def test_done_stops_stream(self):
        events = [
            ("delta", {"text": "Part one."}),
            ("done", {}),
            ("delta", {"text": "Should not appear."}),
        ]
        with MockSSEServer(events=events) as srv:
            answer, _, _ = chat_stream_query(
                "sess2", "Test", _base_url_override=srv.base_url
            )
        self.assertIn("Part one", answer)
        self.assertNotIn("Should not appear", answer)

    # ── Test 3: workflow_ready event extracted ────────────────────────────────

    def test_workflow_name_extracted_from_workflow_ready(self):
        events = [
            ("workflow_ready", {"workflow_name": "admet_screening", "workflow_type": "pipeline"}),
            ("delta", {"text": "Found ADMET workflow."}),
            ("done", {}),
        ]
        with MockSSEServer(events=events) as srv:
            answer, wf_name, view_url = chat_stream_query(
                "sess3", "ADMET screen", _base_url_override=srv.base_url
            )
        self.assertEqual(wf_name, "admet_screening")
        self.assertIn("admet_screening", view_url)

    # ── Test 4: workflow fallback from final event ────────────────────────────

    def test_workflow_name_fallback_from_final(self):
        events = [
            ("delta", {"text": "Here is the plan."}),
            ("final", {"workflow": {"workflow_ga": {"name": "nfcore_rnaseq"}}}),
            ("done", {}),
        ]
        with MockSSEServer(events=events) as srv:
            _, wf_name, _ = chat_stream_query(
                "sess4", "RNA-seq analysis", _base_url_override=srv.base_url
            )
        self.assertEqual(wf_name, "nfcore_rnaseq")

    # ── Test 5: history grows after query ─────────────────────────────────────

    def test_history_grows_after_query(self):
        events = [("delta", {"text": "Answer."}), ("done", {})]
        with MockSSEServer(events=events) as srv:
            chat_stream_query("sess5", "Question?", _base_url_override=srv.base_url)
        history = _get_history("sess5")
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["role"], "user")
        self.assertEqual(history[1]["role"], "assistant")

    # ── Test 6: prior history sent in next request ────────────────────────────

    def test_prior_history_sent_on_followup(self):
        _push_history("sess6", "user", "What is ADMET?")
        _push_history("sess6", "assistant", "ADMET stands for...")

        captured_payload = {}

        class _CapturingHandler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length) if length else b""
                captured_payload.update(json.loads(body))
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                body_bytes = _make_sse_body(("delta", {"text": "Sure."}), ("done", {}))
                self.wfile.write(body_bytes)

            def log_message(self, *args):
                pass

        server = HTTPServer(("127.0.0.1", 0), _CapturingHandler)
        port = server.server_address[1]
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        try:
            chat_stream_query("sess6", "Tell me more", _base_url_override=f"http://127.0.0.1:{port}")
        finally:
            server.shutdown()

        self.assertIn("context", captured_payload)
        prior = captured_payload["context"]["priorMessages"]
        self.assertGreater(len(prior), 0)
        self.assertEqual(prior[0]["role"], "user")

    # ── Test 7: 503 returns error string ─────────────────────────────────────

    def test_503_returns_error_string(self):
        with MockSSEServer(status=503, events=[]) as srv:
            answer, wf_name, view_url = chat_stream_query(
                "sess7", "Test", _base_url_override=srv.base_url
            )
        self.assertIn("unavailable", answer.lower())
        self.assertIsNone(wf_name)

    # ── Test 8: timeout returns message ──────────────────────────────────────

    def test_timeout_returns_message(self):
        import requests as req

        def _slow(*args, **kwargs):
            raise req.exceptions.Timeout("timed out")

        with patch("backend.lib.integrations.coze_plugin.requests.post", side_effect=_slow):
            answer, wf_name, _ = chat_stream_query("sess8", "Slow query")
        self.assertIn("timed out", answer.lower())
        self.assertIsNone(wf_name)

    # ── Test 9: empty stream returns default message ──────────────────────────

    def test_empty_stream_returns_default_message(self):
        events = [("done", {})]
        with MockSSEServer(events=events) as srv:
            answer, _, _ = chat_stream_query(
                "sess9", "Empty test", _base_url_override=srv.base_url
            )
        self.assertGreater(len(answer), 0)  # fallback message, not empty string


# ─────────────────────────────────────────────────────────────────────────────
# handle_query integration tests
# ─────────────────────────────────────────────────────────────────────────────

class TestHandleQuery(unittest.TestCase):

    def setUp(self):
        _session_history.clear()

    def test_auto_generates_session_id(self):
        events = [("delta", {"text": "Result."}), ("done", {})]
        with MockSSEServer(events=events) as srv:
            with patch("backend.lib.integrations.coze_plugin.BIOMATE_API_URL", srv.base_url):
                result = handle_query("Find workflow", session_id=None)
        self.assertIn("session_id", result)
        self.assertGreater(len(result["session_id"]), 0)

    def test_preserves_caller_supplied_session_id(self):
        events = [("delta", {"text": "Result."}), ("done", {})]
        with MockSSEServer(events=events) as srv:
            with patch("backend.lib.integrations.coze_plugin.BIOMATE_API_URL", srv.base_url):
                result = handle_query("Find workflow", session_id="my-session-42")
        self.assertEqual(result["session_id"], "my-session-42")

    def test_workflow_fields_present_when_found(self):
        events = [
            ("workflow_ready", {"workflow_name": "cryosparc_standard_spa"}),
            ("delta", {"text": "CryoEM workflow found."}),
            ("done", {}),
        ]
        with MockSSEServer(events=events) as srv:
            with patch("backend.lib.integrations.coze_plugin.BIOMATE_API_URL", srv.base_url):
                result = handle_query("CryoEM reconstruction")
        self.assertEqual(result["workflow_name"], "cryosparc_standard_spa")
        self.assertIn("cryosparc_standard_spa", result["view_url"])

    def test_workflow_fields_absent_when_not_found(self):
        events = [("delta", {"text": "General answer."}), ("done", {})]
        with MockSSEServer(events=events) as srv:
            with patch("backend.lib.integrations.coze_plugin.BIOMATE_API_URL", srv.base_url):
                result = handle_query("What is PCR?")
        self.assertNotIn("workflow_name", result)
        self.assertNotIn("view_url", result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
