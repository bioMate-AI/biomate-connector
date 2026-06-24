"""
test_wechat_open_claw.py — Unit tests for the WeChat ↔ Open Claw integration.

Tests _open_claw_query(), conversation history management, and handle_wechat_message()
routing without requiring a real Open Claw server.  A lightweight mock SSE server
(http.server in a thread) is used to simulate /api/open-claw/stream responses.

Covers benchmark items from docs/20260412_SESSION5_BENCHMARK_PLAN.md §5.
"""

import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from io import BytesIO
from unittest.mock import patch

# Add project root to path so we can import the wechat_bot module
from connectors.wechat.wechat_bot import (
    _open_claw_query,
    _get_history,
    _push_history,
    _conversation_history,
    handle_wechat_message,
    build_text_reply,
)


# ─────────────────────────────────────────────────────────────────────────────
# Mock SSE server helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_sse_body(*events: tuple) -> bytes:
    """
    Build a mock SSE response body from (event_name, data_dict) tuples.
    e.g. ("text_delta", {"text": "Hello"})
    """
    lines = []
    for name, data in events:
        lines.append(f"event: {name}")
        lines.append(f"data: {json.dumps(data)}")
        lines.append("")  # blank line separates events
    return "\n".join(lines).encode("utf-8")


class _SSEHandler(BaseHTTPRequestHandler):
    """Minimal HTTP handler that returns a pre-set SSE response."""

    response_body: bytes = b""
    response_status: int = 200

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        _body = self.rfile.read(length) if length else b""

        self.send_response(_SSEHandler.response_status)
        if _SSEHandler.response_status == 200:
            self.send_header("Content-Type", "text/event-stream")
        else:
            self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(_SSEHandler.response_body)

    def log_message(self, *args):  # suppress default server logs in test output
        pass


class MockSSEServer:
    """Context manager that runs a mock SSE HTTP server on localhost."""

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
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.server.shutdown()

    def _serve(self):
        self.server.serve_forever()

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestOpenClawQuery(unittest.TestCase):

    def setUp(self):
        # Clear per-user history before each test
        _conversation_history.clear()

    # ── Test 1: Normal query → delta events accumulate → reply returned ─────────
    # /api/chat/stream uses event: delta (not text_delta)

    def test_normal_query_returns_text(self):
        events = [
            ("delta", {"text": "RNA-seq "}),
            ("delta", {"text": "differential expression workflow."}),
            ("done", {}),
        ]
        with MockSSEServer(events=events) as srv:
            reply, wf_id = _open_claw_query(
                "user1", "Find RNA-seq workflow",
                api_key=None,
                _base_url_override=srv.base_url,
            )

        self.assertIn("RNA-seq", reply)
        self.assertIsNone(wf_id)

    # ── Test 2: done event stops the stream early ─────────────────────────────

    def test_done_event_stops_stream(self):
        # Events after "done" should not be collected
        events = [
            ("delta", {"text": "First part."}),
            ("done", {}),
            ("delta", {"text": "This should not appear."}),
        ]
        with MockSSEServer(events=events) as srv:
            reply, _ = _open_claw_query(
                "user2", "Test early stop",
                _base_url_override=srv.base_url,
            )

        self.assertIn("First part", reply)
        self.assertNotIn("should not appear", reply)

    # ── Test 3: workflow_name extracted from workflow_ready event ─────────────
    # /api/chat/stream emits workflow_ready when a runnable workflow is found.
    # The workflow name is used for the "Run in BioMate" deep-link.

    def test_workflow_id_extracted_from_tool_result(self):
        events = [
            ("workflow_ready", {"workflow_name": "rnaseq_differential", "workflow_type": "pipeline"}),
            ("delta", {"text": "Found rnaseq_differential workflow."}),
            ("done", {}),
        ]
        with MockSSEServer(events=events) as srv:
            reply, wf_id = _open_claw_query(
                "user3", "Find RNA-seq",
                _base_url_override=srv.base_url,
            )

        self.assertEqual(wf_id, "rnaseq_differential")

    # ── Test 4: Multi-turn — history grows after each query ───────────────────

    def test_history_grows_after_query(self):
        events = [("delta", {"text": "Answer 1."}), ("done", {})]
        with MockSSEServer(events=events) as srv:
            _open_claw_query("user4", "Question 1", _base_url_override=srv.base_url)

        history = _get_history("user4")
        self.assertEqual(len(history), 2)  # user + assistant
        self.assertEqual(history[0]["role"], "user")
        self.assertEqual(history[1]["role"], "assistant")

    # ── Test 5: clear command wipes history ──────────────────────────────────

    def test_clear_command_wipes_history(self):
        _push_history("user5", "user", "old question")
        _push_history("user5", "assistant", "old answer")
        self.assertEqual(len(_get_history("user5")), 2)

        # Simulate clear via handle_wechat_message
        xml = f"""<xml>
          <MsgType><![CDATA[text]]></MsgType>
          <FromUserName><![CDATA[user5]]></FromUserName>
          <ToUserName><![CDATA[biomate]]></ToUserName>
          <Content><![CDATA[clear]]></Content>
        </xml>"""
        reply = handle_wechat_message(xml, "corp123")

        self.assertEqual(len(_get_history("user5")), 0)
        self.assertIn("清除", reply)

    # ── Test 6: 503 from Open Claw → user-friendly Chinese error ─────────────

    def test_503_returns_friendly_error(self):
        with MockSSEServer(status=503, events=[]) as srv:
            reply, wf_id = _open_claw_query(
                "user6", "Test query",
                _base_url_override=srv.base_url,
            )

        self.assertIn("❌", reply)
        self.assertIsNone(wf_id)

    # ── Test 7: Timeout → returns timeout message ─────────────────────────────

    def test_timeout_returns_timeout_message(self):
        import requests

        def _slow_post(*args, **kwargs):
            raise requests.exceptions.Timeout("timed out")

        with patch("connectors.wechat.wechat_bot.requests.post", side_effect=_slow_post):
            reply, wf_id = _open_claw_query("user7", "Slow query")

        self.assertIn("⏱", reply)
        self.assertIsNone(wf_id)


class TestHandleWechatMessage(unittest.TestCase):
    """Tests for message routing in handle_wechat_message()."""

    def setUp(self):
        _conversation_history.clear()

    def _xml(self, content: str, user: str = "wxuser1") -> str:
        return f"""<xml>
          <MsgType><![CDATA[text]]></MsgType>
          <FromUserName><![CDATA[{user}]]></FromUserName>
          <ToUserName><![CDATA[biomate]]></ToUserName>
          <Content><![CDATA[{content}]]></Content>
        </xml>"""

    def test_bind_command_stores_key(self):
        import connectors.wechat.wechat_bot as wechat_bot
        reply = handle_wechat_message(self._xml("bind test-key-abc"), "corp")
        self.assertIn("✅", reply)
        self.assertEqual(wechat_bot._user_bindings.get("wxuser1"), "test-key-abc")

    def test_help_command_returns_help_text(self):
        reply = handle_wechat_message(self._xml("帮助"), "corp")
        self.assertIn("Open Claw", reply)

    def test_non_text_message_returns_guidance(self):
        xml = """<xml>
          <MsgType><![CDATA[image]]></MsgType>
          <FromUserName><![CDATA[wximg]]></FromUserName>
          <ToUserName><![CDATA[biomate]]></ToUserName>
        </xml>"""
        reply = handle_wechat_message(xml, "corp")
        self.assertIn("仅支持文字", reply)

    def test_scientific_query_returns_immediate_ack(self):
        # handle_wechat_message() returns an immediate ACK and spawns async thread
        # We just check the synchronous reply shape.
        reply = handle_wechat_message(self._xml("RNA-seq分析"), "corp")
        # Should be an XML text reply with "正在分析" and a dashboard link
        self.assertIn("正在分析", reply)
        self.assertIn("dashboard", reply)
        self.assertTrue(reply.startswith("<xml>"))


# ─────────────────────────────────────────────────────────────────────────────
# Tests: unbind flow + long-output rendering
#
# WeChat caps message bodies; a long Open Claw reply must be truncated with a
# "view full results" link, not sent raw. And `unbind` must drop the stored
# binding. Covers test plan L1 gaps (WeChat section).
# ─────────────────────────────────────────────────────────────────────────────

class TestWeChatUnbindAndLongOutput(unittest.TestCase):

    def setUp(self):
        _conversation_history.clear()
        import connectors.wechat.wechat_bot as wechat_bot
        wechat_bot._user_bindings.clear()

    def _xml(self, content: str, user: str = "wxuser_lo") -> str:
        return f"""<xml>
          <MsgType><![CDATA[text]]></MsgType>
          <FromUserName><![CDATA[{user}]]></FromUserName>
          <ToUserName><![CDATA[biomate]]></ToUserName>
          <Content><![CDATA[{content}]]></Content>
        </xml>"""

    def test_unbind_removes_binding(self):
        import connectors.wechat.wechat_bot as wechat_bot
        handle_wechat_message(self._xml("bind key-123"), "corp")
        self.assertEqual(wechat_bot._user_bindings.get("wxuser_lo"), "key-123")

        reply = handle_wechat_message(self._xml("unbind"), "corp")
        self.assertIn("解除", reply)
        self.assertIsNone(wechat_bot._user_bindings.get("wxuser_lo"))

    def test_long_reply_truncated_with_fallback_link(self):
        import connectors.wechat.wechat_bot as wechat_bot
        long_reply = "ADMET screen: " + ("compound flagged; " * 300)  # ~5k chars
        sent = []

        with patch.object(wechat_bot, "_open_claw_query", return_value=(long_reply, None)), \
             patch.object(wechat_bot, "send_text_message",
                          side_effect=lambda user, text: sent.append(text) or True), \
             patch.object(wechat_bot, "send_workflow_card", return_value=True):
            ack = handle_wechat_message(self._xml("screen my library"), "corp")
            self.assertIn("正在分析", ack)  # immediate ack still returned
            time.sleep(0.6)               # let the async thread finish

        self.assertTrue(sent, "expected an async send_text_message")
        # Truncated to 1800 chars + a short fallback suffix, not the raw 5k.
        self.assertLess(len(sent[0]), 1900)
        self.assertIn("查看完整结果", sent[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
