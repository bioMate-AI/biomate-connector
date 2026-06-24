"""
test_telegram_bot.py — Unit tests for the BioMate Telegram integration.

Tests _open_claw_query() SSE parsing, conversation history, parse_update(),
and handle_update() routing (bind / help / clear / scientific query) without
requiring real Telegram or BioMate credentials.

Uses a lightweight mock SSE server (http.server in a thread) to simulate
/api/chat/stream responses — same pattern as test_wechat_open_claw.py.

The connector lives at connectors/telegram/telegram_bot.py, so we add that
directory to sys.path and import the module directly.
"""

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "connectors", "telegram"))

import telegram_bot
from telegram_bot import (
    _open_claw_query,
    _get_history,
    _push_history,
    _conversation_history,
    parse_update,
    handle_update,
    send_message,
)


# ─────────────────────────────────────────────────────────────────────────────
# Mock SSE server helpers (same pattern as test_wechat_open_claw.py)
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
# Tests: _open_claw_query
# ─────────────────────────────────────────────────────────────────────────────

class TestOpenClawQuery(unittest.TestCase):

    def setUp(self):
        _conversation_history.clear()

    def test_normal_query_returns_text(self):
        events = [
            ("delta", {"text": "RNA-seq "}),
            ("delta", {"text": "differential expression workflow."}),
            ("done", {}),
        ]
        with MockSSEServer(events=events) as srv:
            reply, wf_id, _ = _open_claw_query(
                "chat1", "Find RNA-seq workflow",
                api_key=None, _base_url_override=srv.base_url,
            )
        self.assertIn("RNA-seq", reply)
        self.assertIsNone(wf_id)

    def test_done_event_stops_stream(self):
        events = [
            ("delta", {"text": "First part."}),
            ("done", {}),
            ("delta", {"text": "This should not appear."}),
        ]
        with MockSSEServer(events=events) as srv:
            reply, _, _ = _open_claw_query("chat2", "Test", _base_url_override=srv.base_url)
        self.assertIn("First part", reply)
        self.assertNotIn("should not appear", reply)

    def test_workflow_id_from_workflow_ready(self):
        events = [
            ("workflow_ready", {"workflow_name": "rnaseq_differential", "workflow_type": "pipeline"}),
            ("delta", {"text": "Found rnaseq_differential."}),
            ("done", {}),
        ]
        with MockSSEServer(events=events) as srv:
            _, wf_id, _ = _open_claw_query("chat3", "Find RNA-seq", _base_url_override=srv.base_url)
        self.assertEqual(wf_id, "rnaseq_differential")

    def test_history_grows_after_query(self):
        events = [("delta", {"text": "Answer 1."}), ("done", {})]
        with MockSSEServer(events=events) as srv:
            _open_claw_query("chat4", "Question 1", _base_url_override=srv.base_url)
        history = _get_history("chat4")
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["role"], "user")
        self.assertEqual(history[1]["role"], "assistant")

    def test_503_returns_friendly_error(self):
        with MockSSEServer(status=503, events=[]) as srv:
            reply, wf_id, _ = _open_claw_query("chat6", "Test", _base_url_override=srv.base_url)
        self.assertIn("❌", reply)
        self.assertIsNone(wf_id)

    def test_timeout_returns_timeout_message(self):
        import requests

        def _slow_post(*args, **kwargs):
            raise requests.exceptions.Timeout("timed out")

        with patch("telegram_bot.requests.post", side_effect=_slow_post):
            reply, wf_id, _ = _open_claw_query("chat7", "Slow query")
        self.assertIn("⏱", reply)
        self.assertIsNone(wf_id)


# ─────────────────────────────────────────────────────────────────────────────
# Tests: parse_update
# ─────────────────────────────────────────────────────────────────────────────

class TestParseUpdate(unittest.TestCase):

    def test_parses_text_and_chat_id(self):
        update = {"message": {"chat": {"id": 4242}, "text": "  hello  "}}
        chat_id, text = parse_update(update)
        self.assertEqual(chat_id, "4242")
        self.assertEqual(text, "hello")

    def test_edited_message_supported(self):
        update = {"edited_message": {"chat": {"id": 7}, "text": "edited"}}
        chat_id, text = parse_update(update)
        self.assertEqual(chat_id, "7")
        self.assertEqual(text, "edited")

    def test_no_message_returns_none(self):
        chat_id, text = parse_update({"callback_query": {}})
        self.assertIsNone(chat_id)
        self.assertEqual(text, "")


# ─────────────────────────────────────────────────────────────────────────────
# Tests: handle_update routing
# ─────────────────────────────────────────────────────────────────────────────

class TestHandleUpdate(unittest.TestCase):

    def setUp(self):
        _conversation_history.clear()
        telegram_bot._user_bindings.clear()

    def _update(self, text: str, chat_id: int = 100) -> dict:
        return {"message": {"chat": {"id": chat_id}, "text": text}}

    def test_bind_stores_key(self):
        sent = []
        with patch("telegram_bot.send_message", side_effect=lambda cid, txt, **k: sent.append((cid, txt)) or True):
            handle_update(self._update("/bind test-key-abc"))
        self.assertEqual(telegram_bot._user_bindings.get("100"), "test-key-abc")
        self.assertIn("✅", sent[0][1])

    def test_bind_with_bot_suffix(self):
        sent = []
        with patch("telegram_bot.send_message", side_effect=lambda cid, txt, **k: sent.append((cid, txt)) or True):
            handle_update(self._update("/bind@BioMateBot k2"))
        self.assertEqual(telegram_bot._user_bindings.get("100"), "k2")

    def test_help_returns_help_text(self):
        sent = []
        with patch("telegram_bot.send_message", side_effect=lambda cid, txt, **k: sent.append((cid, txt)) or True):
            handle_update(self._update("/help"))
        self.assertIn("BioMate", sent[0][1])
        self.assertIn("/bind", sent[0][1])

    def test_clear_wipes_history(self):
        _push_history("100", "user", "old")
        _push_history("100", "assistant", "old a")
        self.assertEqual(len(_get_history("100")), 2)
        with patch("telegram_bot.send_message", return_value=True):
            handle_update(self._update("/clear"))
        self.assertEqual(len(_get_history("100")), 0)

    def test_scientific_query_sends_reply_and_card(self):
        # No server view_url → button falls back to the app root.
        sent = []
        cards = []
        events = [
            ("delta", {"text": "Found ADMET workflow."}),
            ("workflow_ready", {"workflow_name": "predict_admet_properties"}),
            ("done", {}),
        ]
        with MockSSEServer(events=events) as srv:
            with patch("telegram_bot.BIOMATE_API_URL", srv.base_url), \
                 patch("telegram_bot.send_message", side_effect=lambda cid, txt, **k: sent.append(txt) or True), \
                 patch("telegram_bot.send_workflow_card", side_effect=lambda cid, **k: cards.append(k) or True):
                handle_update(self._update("screen aspirin for ADMET"))

        self.assertTrue(any("ADMET" in t for t in sent))
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["workflow_name"], "predict_admet_properties")
        self.assertEqual(cards[0]["url"], telegram_bot.BIOMATE_DEEP_LINK_BASE)

    def test_card_prefers_server_view_url(self):
        # When the stream supplies a view_url, the button uses it verbatim.
        cards = []
        events = [
            ("workflow_ready", {"workflow_name": "wf", "view_url": "https://app.biomate.ai/w/abc123"}),
            ("done", {}),
        ]
        with MockSSEServer(events=events) as srv:
            with patch("telegram_bot.BIOMATE_API_URL", srv.base_url), \
                 patch("telegram_bot.send_message", return_value=True), \
                 patch("telegram_bot.send_workflow_card", side_effect=lambda cid, **k: cards.append(k) or True):
                handle_update(self._update("run wf"))

        self.assertEqual(cards[0]["url"], "https://app.biomate.ai/w/abc123")


# ─────────────────────────────────────────────────────────────────────────────
# Tests: send_message truncation
# ─────────────────────────────────────────────────────────────────────────────

class TestSendMessage(unittest.TestCase):

    def test_truncates_to_telegram_limit(self):
        captured = {}

        class _Resp:
            def raise_for_status(self):
                pass

            def json(self):
                return {"ok": True}

        def _fake_post(url, json=None, timeout=None):
            captured["text"] = json["text"]
            return _Resp()

        long_text = "x" * 5000
        with patch("telegram_bot.TELEGRAM_BOT_TOKEN", "tkn"), \
             patch("telegram_bot.requests.post", side_effect=_fake_post):
            ok = send_message(1, long_text)

        self.assertTrue(ok)
        self.assertLessEqual(len(captured["text"]), telegram_bot.TELEGRAM_MAX_CHARS)
        self.assertIn("full results", captured["text"])

    def test_no_token_returns_false(self):
        with patch("telegram_bot.TELEGRAM_BOT_TOKEN", ""):
            self.assertFalse(send_message(1, "hi"))


# ─────────────────────────────────────────────────────────────────────────────
# Tests: webhook secret-token verification (X-Telegram-Bot-Api-Secret-Token)
#
# Telegram echoes the setWebhook `secret_token` back on every update. Without
# this check, anyone who learns the public webhook URL can POST forged updates
# (spoofed messages from arbitrary chat_ids). Covers test plan S.10 / 9.4.
# ─────────────────────────────────────────────────────────────────────────────

class TestWebhookSecret(unittest.TestCase):

    SECRET = "s3cr3t-token-xyz"

    def _client(self):
        return telegram_bot.create_flask_app().test_client()

    def test_no_secret_configured_allows_update(self):
        """Back-compat: when no secret is set, updates pass (with a logged warning)."""
        with patch("telegram_bot.TELEGRAM_WEBHOOK_SECRET", ""):
            self.assertTrue(telegram_bot.verify_webhook_secret(None))
            self.assertTrue(telegram_bot.verify_webhook_secret("anything"))

    def test_correct_secret_passes(self):
        with patch("telegram_bot.TELEGRAM_WEBHOOK_SECRET", self.SECRET):
            self.assertTrue(telegram_bot.verify_webhook_secret(self.SECRET))

    def test_wrong_secret_rejected(self):
        with patch("telegram_bot.TELEGRAM_WEBHOOK_SECRET", self.SECRET):
            self.assertFalse(telegram_bot.verify_webhook_secret("wrong"))
            self.assertFalse(telegram_bot.verify_webhook_secret(None))  # missing header

    def test_webhook_route_403_on_wrong_secret(self):
        """A forged POST with the wrong header is rejected before any dispatch."""
        spawned = []
        with patch("telegram_bot.TELEGRAM_BOT_TOKEN", "tkn"), \
             patch("telegram_bot.TELEGRAM_WEBHOOK_SECRET", self.SECRET), \
             patch("telegram_bot.handle_update", side_effect=lambda u: spawned.append(u)):
            resp = self._client().post(
                "/connect/telegram/webhook",
                json={"message": {"chat": {"id": 1}, "text": "spoofed"}},
                headers={"X-Telegram-Bot-Api-Secret-Token": "forged"},
            )
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(spawned, [])  # update never dispatched

    def test_webhook_route_200_on_correct_secret(self):
        with patch("telegram_bot.TELEGRAM_BOT_TOKEN", "tkn"), \
             patch("telegram_bot.TELEGRAM_WEBHOOK_SECRET", self.SECRET), \
             patch("telegram_bot.handle_update", return_value=None):
            resp = self._client().post(
                "/connect/telegram/webhook",
                json={"message": {"chat": {"id": 1}, "text": "real"}},
                headers={"X-Telegram-Bot-Api-Secret-Token": self.SECRET},
            )
        self.assertEqual(resp.status_code, 200)


if __name__ == "__main__":
    unittest.main(verbosity=2)
