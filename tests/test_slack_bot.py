"""
test_slack_bot.py — Unit tests for the BioMate Slack integration.

Tests _chat_stream_query(), conversation history management,
handle_slash_command() routing, and Slack signature verification
without requiring real Slack or BioMate credentials.

Uses a lightweight mock SSE server (http.server in a thread) to simulate
/api/chat/stream responses.
"""

import hashlib
import hmac
import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import List, Tuple
from unittest.mock import patch

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from connectors.slack.slack_bot import (
    _chat_stream_query,
    _open_claw_query,
    _get_history,
    _push_history,
    _conversation_history,
    handle_slash_command,
    build_workflow_card,
    build_response_blocks,
    build_error_block,
    verify_slack_signature,
)


# ─────────────────────────────────────────────────────────────────────────────
# Mock SSE server helpers (same pattern as test_wechat_open_claw.py)
# ─────────────────────────────────────────────────────────────────────────────

def _make_sse_body(*events: Tuple[str, dict]) -> bytes:
    """Build a mock SSE response body from (event_name, data_dict) tuples."""
    lines = []
    for name, data in events:
        lines.append(f"event: {name}")
        lines.append(f"data: {json.dumps(data)}")
        lines.append("")  # blank line separates events
    return "\n".join(lines).encode("utf-8")


class _SSEHandler(BaseHTTPRequestHandler):
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
# Tests: _chat_stream_query
# ─────────────────────────────────────────────────────────────────────────────

class TestChatStreamQuery(unittest.TestCase):

    def setUp(self):
        _conversation_history.clear()

    def test_normal_query_returns_text(self):
        """delta events accumulate into reply_text."""
        events = [
            ("delta", {"text": "RNA-seq "}),
            ("delta", {"text": "differential expression workflow."}),
            ("done", {}),
        ]
        with MockSSEServer(events=events) as srv:
            reply, wf_name, view_url = _chat_stream_query(
                "u1", "Find RNA-seq workflow",
                _base_url_override=srv.base_url,
            )

        self.assertIn("RNA-seq", reply)
        self.assertIsNone(wf_name)
        self.assertIsNone(view_url)

    def test_workflow_ready_event_captured(self):
        """workflow_ready event supplies workflow_name."""
        events = [
            ("delta", {"text": "I recommend the ADMET workflow."}),
            ("workflow_ready", {"workflow_name": "predict_admet_properties", "workflow_type": "pipeline"}),
            ("done", {}),
        ]
        with MockSSEServer(events=events) as srv:
            reply, wf_name, view_url = _chat_stream_query(
                "u2", "Screen aspirin for ADMET",
                _base_url_override=srv.base_url,
            )

        self.assertIn("ADMET", reply)
        self.assertEqual(wf_name, "predict_admet_properties")

    def test_view_url_from_workflow_ready(self):
        """view_url is extracted from workflow_ready when present."""
        events = [
            ("workflow_ready", {"workflow_name": "rnaseq", "view_url": "https://app.biomate.ai/w/abc"}),
            ("done", {}),
        ]
        with MockSSEServer(events=events) as srv:
            _, _, view_url = _chat_stream_query(
                "u3", "RNA-seq", _base_url_override=srv.base_url,
            )
        self.assertEqual(view_url, "https://app.biomate.ai/w/abc")

    def test_final_event_fallback_for_workflow_name(self):
        """final event's workflow.workflow_name fills in when workflow_ready is absent."""
        events = [
            ("delta", {"text": "Running ADMET."}),
            ("final", {"workflow": {"success": True, "workflow_name": "admet_v2", "pipeline_path": "admet/main.nf"}}),
            ("done", {}),
        ]
        with MockSSEServer(events=events) as srv:
            _, wf_name, view_url = _chat_stream_query(
                "u4", "Screen drug", _base_url_override=srv.base_url,
            )

        self.assertEqual(wf_name, "admet_v2")
        # view_url should be constructed from BIOMATE_DEEP_LINK_BASE
        self.assertIsNotNone(view_url)
        self.assertIn("admet", view_url)

    def test_done_stops_stream(self):
        """Events after 'done' are ignored."""
        events = [
            ("delta", {"text": "First."}),
            ("done", {}),
            ("delta", {"text": "Should not appear."}),
        ]
        with MockSSEServer(events=events) as srv:
            reply, _, _ = _chat_stream_query("u5", "Test", _base_url_override=srv.base_url)

        self.assertIn("First", reply)
        self.assertNotIn("Should not appear", reply)

    def test_503_returns_friendly_error(self):
        with MockSSEServer(status=503) as srv:
            reply, wf_name, view_url = _chat_stream_query(
                "u6", "Test", _base_url_override=srv.base_url,
            )

        self.assertIn(":x:", reply)
        self.assertIsNone(wf_name)
        self.assertIsNone(view_url)

    def test_400_returns_friendly_error(self):
        with MockSSEServer(status=400) as srv:
            reply, _, _ = _chat_stream_query("u7", "Test", _base_url_override=srv.base_url)
        self.assertIn(":x:", reply)

    def test_timeout_returns_friendly_error(self):
        import requests as _req

        def _slow_post(*args, **kwargs):
            raise _req.exceptions.Timeout("timed out")

        with patch("connectors.slack.slack_bot.requests.post", side_effect=_slow_post):
            reply, _, _ = _chat_stream_query("u8", "Slow query")

        self.assertIn(":hourglass:", reply)

    def test_history_grows_after_query(self):
        """Both user and assistant turns are recorded."""
        events = [("delta", {"text": "Answer."}), ("done", {})]
        with MockSSEServer(events=events) as srv:
            _chat_stream_query("u9", "Question", _base_url_override=srv.base_url)

        history = _get_history("u9")
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["role"], "user")
        self.assertEqual(history[1]["role"], "assistant")

    def test_empty_response_returns_fallback(self):
        """When no delta events arrive, returns a fallback message."""
        events = [("done", {})]
        with MockSSEServer(events=events) as srv:
            reply, _, _ = _chat_stream_query("u10", "Test", _base_url_override=srv.base_url)
        self.assertTrue(len(reply) > 0)
        self.assertNotIn(":x:", reply)


class TestOpenClawQueryAlias(unittest.TestCase):
    """_open_claw_query is a compat alias for _chat_stream_query."""

    def setUp(self):
        _conversation_history.clear()

    def test_alias_returns_two_tuple(self):
        events = [("delta", {"text": "Alias test."}), ("done", {})]
        with MockSSEServer(events=events) as srv:
            result = _open_claw_query("alias_user", "Test", _base_url_override=srv.base_url)
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 2)
        reply, wf_name = result
        self.assertIn("Alias test", reply)


# ─────────────────────────────────────────────────────────────────────────────
# Tests: handle_slash_command routing
# ─────────────────────────────────────────────────────────────────────────────

class TestHandleSlashCommand(unittest.TestCase):

    def setUp(self):
        _conversation_history.clear()

    def _form(self, text: str, user_id: str = "U123", user_name: str = "testuser",
               response_url: str = "") -> dict:
        return {
            "text": text,
            "user_id": user_id,
            "user_name": user_name,
            "response_url": response_url,
        }

    def test_empty_query_returns_help(self):
        resp = handle_slash_command(self._form(""))
        self.assertEqual(resp["response_type"], "ephemeral")
        self.assertIn("Usage", resp["text"])

    def test_help_flag_returns_help(self):
        resp = handle_slash_command(self._form("help"))
        self.assertEqual(resp["response_type"], "ephemeral")

    def test_clear_wipes_history(self):
        _push_history("U123", "user", "old question")
        resp = handle_slash_command(self._form("clear"))
        self.assertEqual(resp["response_type"], "ephemeral")
        self.assertIn("cleared", resp["text"])
        self.assertEqual(len(_get_history("U123")), 0)

    def test_reset_also_wipes_history(self):
        _push_history("U999", "user", "old")
        resp = handle_slash_command(self._form("reset", user_id="U999"))
        self.assertIn("cleared", resp["text"])

    def test_scientific_query_returns_immediate_ack(self):
        """Scientific queries return immediate ack; async thread handles the real response."""
        resp = handle_slash_command(self._form("run ADMET on aspirin", response_url=""))
        # With no response_url, we still get the ack shape
        self.assertEqual(resp.get("response_type"), "in_channel")
        self.assertIn("BioMate", resp["text"])

    def test_scientific_query_with_response_url_spawns_async(self):
        """With a response_url, the command spawns a background thread and returns ack."""
        posted = []

        def _fake_post(channel, text, blocks=None, response_url=None):
            posted.append({"text": text, "blocks": blocks, "response_url": response_url})

        events = [("delta", {"text": "Found ADMET workflow."}), ("done", {})]
        with MockSSEServer(events=events) as srv:
            with patch("connectors.slack.slack_bot.BIOMATE_API_URL", srv.base_url), \
                 patch("connectors.slack.slack_bot.post_to_slack", side_effect=_fake_post):
                resp = handle_slash_command(self._form(
                    "screen aspirin for ADMET",
                    response_url="https://hooks.slack.com/commands/fake"
                ))
                # Wait for async thread
                time.sleep(0.8)

        self.assertEqual(resp["response_type"], "in_channel")
        self.assertTrue(len(posted) > 0, "Expected async post_to_slack call")
        self.assertIn("ADMET", posted[0]["text"])

    def test_workflow_button_appears_when_workflow_ready(self):
        """When a workflow is generated, the response includes an action button."""
        posted = []

        def _fake_post(channel, text, blocks=None, response_url=None):
            posted.append({"blocks": blocks or []})

        events = [
            ("delta", {"text": "Here is the workflow."}),
            ("workflow_ready", {"workflow_name": "predict_admet_properties"}),
            ("done", {}),
        ]
        with MockSSEServer(events=events) as srv:
            with patch("connectors.slack.slack_bot.BIOMATE_API_URL", srv.base_url), \
                 patch("connectors.slack.slack_bot.post_to_slack", side_effect=_fake_post):
                handle_slash_command(self._form(
                    "ADMET for aspirin",
                    response_url="https://hooks.slack.com/commands/fake"
                ))
                time.sleep(0.8)

        all_blocks = [b for p in posted for b in p["blocks"]]
        action_blocks = [b for b in all_blocks if b.get("type") == "actions"]
        self.assertTrue(len(action_blocks) > 0, "Expected actions block with Run button")
        btn_text = action_blocks[0]["elements"][0]["text"]["text"]
        self.assertIn("predict_admet_properties", btn_text)


# ─────────────────────────────────────────────────────────────────────────────
# Tests: long-output rendering + multi-user isolation in a single channel
#
# Block Kit caps a section text at 3000 chars; a long BioMate reply must be
# truncated with a "view full results" fallback link, not sent raw (Slack would
# reject the whole message). And two users running concurrently in the same
# channel must each get their own response via their own response_url with their
# own @-mention — no thread cross-talk. Covers test plan L1 gaps (Slack section).
# ─────────────────────────────────────────────────────────────────────────────

class TestLongOutputAndIsolation(unittest.TestCase):

    def setUp(self):
        _conversation_history.clear()

    def _form(self, text, user_id="U123", user_name="testuser", response_url=""):
        return {"text": text, "user_id": user_id, "user_name": user_name,
                "response_url": response_url}

    @staticmethod
    def _section_text(blocks):
        for b in blocks or []:
            if b.get("type") == "section":
                return b["text"]["text"]
        return ""

    def test_long_reply_truncated_under_block_limit(self):
        """A multi-thousand-char reply is truncated to <3000 chars with a fallback link."""
        posted = []
        long_reply = "DESeq2 results: " + ("geneX up; " * 600)  # ~6k chars
        deltas = [("delta", {"text": long_reply}), ("done", {})]

        def _fake_post(channel, text, blocks=None, response_url=None):
            posted.append({"text": text, "blocks": blocks})

        with MockSSEServer(events=deltas) as srv:
            with patch("connectors.slack.slack_bot.BIOMATE_API_URL", srv.base_url), \
                 patch("connectors.slack.slack_bot.post_to_slack", side_effect=_fake_post):
                handle_slash_command(self._form(
                    "RNA-seq DE on my samples",
                    response_url="https://hooks.slack.com/commands/fake"))
                time.sleep(0.8)

        self.assertTrue(posted, "expected an async post")
        section = self._section_text(posted[0]["blocks"])
        self.assertLess(len(section), 3000, "section text must stay under Block Kit's 3000 limit")
        self.assertIn("View full results", section)

    def test_two_users_same_channel_no_crosstalk(self):
        """Concurrent runs from two users each post to their own response_url + mention."""
        posted = []

        def _fake_post(channel, text, blocks=None, response_url=None):
            posted.append({"blocks": blocks, "response_url": response_url})

        with MockSSEServer(events=[("delta", {"text": "Result for you."}), ("done", {})]) as srv:
            with patch("connectors.slack.slack_bot.BIOMATE_API_URL", srv.base_url), \
                 patch("connectors.slack.slack_bot.post_to_slack", side_effect=_fake_post):
                handle_slash_command(self._form("screen aspirin", user_id="U_alice",
                                                user_name="alice",
                                                response_url="https://hooks.slack.com/alice"))
                handle_slash_command(self._form("screen ibuprofen", user_id="U_bob",
                                                user_name="bob",
                                                response_url="https://hooks.slack.com/bob"))
                time.sleep(1.0)

        by_url = {p["response_url"]: self._section_text(p["blocks"]) for p in posted}
        self.assertIn("https://hooks.slack.com/alice", by_url)
        self.assertIn("https://hooks.slack.com/bob", by_url)
        # Each reply is addressed to the right user — no cross-mention.
        self.assertIn("@alice", by_url["https://hooks.slack.com/alice"])
        self.assertNotIn("@bob", by_url["https://hooks.slack.com/alice"])
        self.assertIn("@bob", by_url["https://hooks.slack.com/bob"])
        self.assertNotIn("@alice", by_url["https://hooks.slack.com/bob"])


# ─────────────────────────────────────────────────────────────────────────────
# Tests: Slack signature verification
# ─────────────────────────────────────────────────────────────────────────────

class TestVerifySlackSignature(unittest.TestCase):

    SECRET = "test-signing-secret-abc123"

    def _make_sig(self, body: bytes, ts: int) -> str:
        base = f"v0:{ts}:{body.decode()}"
        return "v0=" + hmac.new(
            self.SECRET.encode(), base.encode(), hashlib.sha256
        ).hexdigest()

    def test_valid_signature_passes(self):
        body = b"text=hello+world&user_id=U123"
        ts = int(time.time())
        sig = self._make_sig(body, ts)
        self.assertTrue(verify_slack_signature(self.SECRET, body, str(ts), sig))

    def test_wrong_secret_fails(self):
        body = b"text=hello"
        ts = int(time.time())
        sig = self._make_sig(body, ts)
        self.assertFalse(verify_slack_signature("wrong-secret", body, str(ts), sig))

    def test_tampered_body_fails(self):
        body = b"text=hello"
        ts = int(time.time())
        sig = self._make_sig(body, ts)
        self.assertFalse(verify_slack_signature(self.SECRET, b"text=tampered", str(ts), sig))

    def test_old_timestamp_fails(self):
        body = b"text=hello"
        old_ts = int(time.time()) - 400  # >300s ago
        sig = self._make_sig(body, old_ts)
        self.assertFalse(verify_slack_signature(self.SECRET, body, str(old_ts), sig))

    def test_missing_signature_fails(self):
        body = b"text=hello"
        ts = int(time.time())
        self.assertFalse(verify_slack_signature(self.SECRET, body, str(ts), ""))

    def test_invalid_timestamp_fails(self):
        self.assertFalse(verify_slack_signature(self.SECRET, b"body", "not-a-number", "v0=abc"))


# ─────────────────────────────────────────────────────────────────────────────
# Tests: Block Kit builders
# ─────────────────────────────────────────────────────────────────────────────

class TestBlockBuilders(unittest.TestCase):

    def test_build_workflow_card_structure(self):
        card = build_workflow_card(
            workflow_name="predict_admet_properties",
            description="ADMET prediction for small molecules",
            workflow_id="admet_v2",
            required_inputs=["smiles", "endpoint"],
            estimated_time="5 min",
            confidence=0.92,
        )
        self.assertEqual(card["type"], "section")
        self.assertIn("predict_admet_properties", card["text"]["text"])
        self.assertIn("92%", card["text"]["text"])
        self.assertIn("Run in BioMate", card["accessory"]["text"]["text"])
        self.assertIn("admet_v2", card["accessory"]["url"])

    def test_build_workflow_card_no_optional_fields(self):
        card = build_workflow_card("WF", "A workflow", "wf-id")
        self.assertEqual(card["type"], "section")

    def test_build_response_blocks_with_workflows(self):
        workflows = [
            {"name": "RNA-seq", "description": "DE analysis", "workflow_id": "rnaseq", "score": 0.9},
            {"name": "ADMET", "description": "Drug screening", "workflow_id": "admet", "score": 0.8},
        ]
        blocks = build_response_blocks("run RNA-seq", workflows, user_name="alice")
        types = [b["type"] for b in blocks]
        self.assertIn("section", types)
        # Should include divider(s) for multiple workflows
        self.assertIn("divider", types)

    def test_build_response_blocks_no_workflows(self):
        blocks = build_response_blocks("obscure query", [])
        texts = [b.get("text", {}).get("text", "") for b in blocks if "text" in b]
        self.assertTrue(any("No matching workflows" in t for t in texts))

    def test_build_error_block(self):
        blocks = build_error_block("Something broke")
        self.assertTrue(len(blocks) > 0)
        text = blocks[0]["text"]["text"]
        self.assertIn("error", text.lower())
        self.assertIn("Something broke", text)


# ─────────────────────────────────────────────────────────────────────────────
# Tests: History management
# ─────────────────────────────────────────────────────────────────────────────

class TestHistoryManagement(unittest.TestCase):

    def setUp(self):
        _conversation_history.clear()

    def test_push_and_get_history(self):
        _push_history("hist_user", "user", "first question")
        _push_history("hist_user", "assistant", "first answer")
        h = _get_history("hist_user")
        self.assertEqual(len(h), 2)
        self.assertEqual(h[0]["content"], "first question")

    def test_history_independent_per_user(self):
        _push_history("u_a", "user", "a")
        _push_history("u_b", "user", "b")
        self.assertEqual(len(_get_history("u_a")), 1)
        self.assertEqual(len(_get_history("u_b")), 1)

    def test_history_max_length(self):
        for i in range(15):
            _push_history("u_max", "user", f"q{i}")
        h = _get_history("u_max")
        self.assertLessEqual(len(h), 10)  # _MAX_HISTORY = 10

    def test_empty_history_returns_empty_list(self):
        h = _get_history("u_nonexistent")
        self.assertEqual(h, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
