"""
test_feishu_bot.py — Unit tests for the BioMate Feishu / Lark integration.

Tests _open_claw_query() SSE parsing, conversation history, url_verification
challenge echo, event-token checks, message-id dedup, mention stripping, and
handle_message_event() routing (bind / clear / help / scientific query) without
requiring real Feishu or BioMate credentials.

Uses a lightweight mock SSE server (http.server in a thread) to simulate
/api/chat/stream responses — same pattern as test_wechat_open_claw.py.

The connector lives at connectors/feishu/feishu_bot.py, so we add that
directory to sys.path and import the module directly.
"""

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "connectors", "feishu"))

import feishu_bot
from feishu_bot import (
    _open_claw_query,
    _get_history,
    _push_history,
    _conversation_history,
    extract_text,
    handle_event,
    handle_message_event,
    _already_seen,
    _seen_message_ids,
    _sign_go,
    _signed_go_url,
    _verify_go,
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
            reply, wf_id, _ = _open_claw_query("u1", "Find RNA-seq", _base_url_override=srv.base_url)
        self.assertIn("RNA-seq", reply)
        self.assertIsNone(wf_id)

    def test_done_event_stops_stream(self):
        events = [
            ("delta", {"text": "First part."}),
            ("done", {}),
            ("delta", {"text": "Should not appear."}),
        ]
        with MockSSEServer(events=events) as srv:
            reply, _, _ = _open_claw_query("u2", "Test", _base_url_override=srv.base_url)
        self.assertIn("First part", reply)
        self.assertNotIn("Should not appear", reply)

    def test_workflow_id_from_workflow_ready(self):
        events = [
            ("workflow_ready", {"workflow_name": "rnaseq_differential"}),
            ("delta", {"text": "Found it."}),
            ("done", {}),
        ]
        with MockSSEServer(events=events) as srv:
            _, wf_id, _ = _open_claw_query("u3", "Find RNA-seq", _base_url_override=srv.base_url)
        self.assertEqual(wf_id, "rnaseq_differential")

    def test_history_grows_after_query(self):
        events = [("delta", {"text": "Answer."}), ("done", {})]
        with MockSSEServer(events=events) as srv:
            _open_claw_query("u4", "Q", _base_url_override=srv.base_url)
        h = _get_history("u4")
        self.assertEqual(len(h), 2)

    def test_503_returns_friendly_error(self):
        with MockSSEServer(status=503, events=[]) as srv:
            reply, wf_id, _ = _open_claw_query("u6", "Test", _base_url_override=srv.base_url)
        self.assertIn("❌", reply)
        self.assertIsNone(wf_id)

    def test_timeout_returns_timeout_message(self):
        import requests

        def _slow_post(*args, **kwargs):
            raise requests.exceptions.Timeout("timed out")

        with patch("feishu_bot.requests.post", side_effect=_slow_post):
            reply, _, _ = _open_claw_query("u7", "Slow")
        self.assertIn("⏱", reply)


# ─────────────────────────────────────────────────────────────────────────────
# Tests: url_verification + event-token handling
# ─────────────────────────────────────────────────────────────────────────────

class TestHandleEvent(unittest.TestCase):

    def test_url_verification_echoes_challenge(self):
        with patch("feishu_bot.FEISHU_VERIFY_TOKEN", ""):
            resp = handle_event({"type": "url_verification", "challenge": "abc123"})
        self.assertEqual(resp, {"challenge": "abc123"})

    def test_url_verification_token_mismatch_rejected(self):
        with patch("feishu_bot.FEISHU_VERIFY_TOKEN", "right-token"):
            resp = handle_event({
                "type": "url_verification",
                "challenge": "abc123",
                "token": "wrong-token",
            })
        self.assertEqual(resp, {})

    def test_url_verification_token_match_echoes(self):
        with patch("feishu_bot.FEISHU_VERIFY_TOKEN", "right-token"):
            resp = handle_event({
                "type": "url_verification",
                "challenge": "abc123",
                "token": "right-token",
            })
        self.assertEqual(resp, {"challenge": "abc123"})

    def test_message_event_token_mismatch_ignored(self):
        spawned = []
        with patch("feishu_bot.FEISHU_VERIFY_TOKEN", "right-token"), \
             patch("feishu_bot.threading.Thread",
                   side_effect=lambda *a, **k: spawned.append(k) or _NoopThread()):
            resp = handle_event({
                "header": {"event_type": "im.message.receive_v1", "token": "wrong"},
                "event": {},
            })
        self.assertEqual(resp, {})
        self.assertEqual(spawned, [])


class _NoopThread:
    def start(self):
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Tests: extract_text (mention stripping)
# ─────────────────────────────────────────────────────────────────────────────

class TestExtractText(unittest.TestCase):

    def test_plain_text(self):
        msg = {"content": json.dumps({"text": "RNA-seq analysis"})}
        self.assertEqual(extract_text(msg), "RNA-seq analysis")

    def test_strips_mention_placeholder(self):
        msg = {
            "content": json.dumps({"text": "@_user_1 screen aspirin"}),
            "mentions": [{"key": "@_user_1"}],
        }
        self.assertEqual(extract_text(msg), "screen aspirin")

    def test_strips_leftover_placeholder_without_mentions_list(self):
        msg = {"content": json.dumps({"text": "@_user_2 hello"})}
        self.assertEqual(extract_text(msg), "hello")

    def test_bad_content_returns_empty(self):
        self.assertEqual(extract_text({"content": "not-json"}), "")


# ─────────────────────────────────────────────────────────────────────────────
# Tests: message_id dedup
# ─────────────────────────────────────────────────────────────────────────────

class TestDedup(unittest.TestCase):

    def setUp(self):
        _seen_message_ids.clear()

    def test_first_seen_false_then_true(self):
        self.assertFalse(_already_seen("om_123"))
        self.assertTrue(_already_seen("om_123"))

    def test_empty_id_never_seen(self):
        self.assertFalse(_already_seen(""))
        self.assertFalse(_already_seen(""))


# ─────────────────────────────────────────────────────────────────────────────
# Tests: handle_message_event routing
# ─────────────────────────────────────────────────────────────────────────────

class TestHandleMessageEvent(unittest.TestCase):

    def setUp(self):
        _conversation_history.clear()
        feishu_bot._user_bindings.clear()
        _seen_message_ids.clear()

    def _event(self, text: str, mid: str = "om_1", open_id: str = "ou_1") -> dict:
        return {
            "message": {
                "message_id": mid,
                "chat_id": "oc_chat",
                "message_type": "text",
                "content": json.dumps({"text": text}),
            },
            "sender": {"sender_id": {"open_id": open_id}},
        }

    def test_bind_stores_key(self):
        sent = []
        with patch("feishu_bot.send_text_message",
                   side_effect=lambda cid, txt, **k: sent.append(txt) or True):
            handle_message_event(self._event("bind test-key-abc"))
        self.assertEqual(feishu_bot._user_bindings.get("ou_1"), "test-key-abc")
        self.assertIn("✅", sent[0])

    def test_help_returns_help_text(self):
        sent = []
        with patch("feishu_bot.send_text_message",
                   side_effect=lambda cid, txt, **k: sent.append(txt) or True):
            handle_message_event(self._event("帮助", mid="om_help"))
        self.assertIn("BioMate", sent[0])

    def test_clear_wipes_history(self):
        _push_history("ou_1", "user", "old")
        _push_history("ou_1", "assistant", "old a")
        with patch("feishu_bot.send_text_message", return_value=True):
            handle_message_event(self._event("clear", mid="om_clear"))
        self.assertEqual(len(_get_history("ou_1")), 0)

    def test_duplicate_message_id_skipped(self):
        sent = []
        with patch("feishu_bot.send_text_message",
                   side_effect=lambda cid, txt, **k: sent.append(txt) or True):
            handle_message_event(self._event("帮助", mid="dup"))
            handle_message_event(self._event("帮助", mid="dup"))
        self.assertEqual(len(sent), 1)  # second call deduped

    def test_scientific_query_sends_reply_and_card(self):
        sent = []
        cards = []
        events = [
            ("delta", {"text": "Found ADMET workflow."}),
            ("workflow_ready", {"workflow_name": "predict_admet_properties"}),
            ("done", {}),
        ]
        with MockSSEServer(events=events) as srv:
            with patch("feishu_bot.send_text_message",
                       side_effect=lambda cid, txt, **k: sent.append(txt) or True), \
                 patch("feishu_bot.send_workflow_card",
                       side_effect=lambda cid, **k: cards.append(k) or True):
                handle_message_event(
                    self._event("screen aspirin for ADMET", mid="om_sci"),
                    _base_url_override=srv.base_url,
                )

        self.assertTrue(any("ADMET" in t for t in sent))
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["workflow_name"], "predict_admet_properties")
        self.assertEqual(cards[0]["url"], feishu_bot.BIOMATE_DEEP_LINK_BASE)


# ─────────────────────────────────────────────────────────────────────────────
# Tests: signed /go click-time auto-login redirect
# ─────────────────────────────────────────────────────────────────────────────

class TestGoRedirect(unittest.TestCase):

    def setUp(self):
        feishu_bot._user_bindings.clear()

    # ── signing helpers ───────────────────────────────────────────────────────

    def test_signed_go_url_none_without_public_url(self):
        with patch("feishu_bot.CONNECTOR_PUBLIC_URL", ""):
            self.assertIsNone(_signed_go_url("ou_1", "/"))

    def test_signed_go_url_is_signed_and_verifies(self):
        with patch("feishu_bot.CONNECTOR_PUBLIC_URL", "https://bot.example.com"):
            url = _signed_go_url("ou_1", "/workflow/abc")
        self.assertIn("https://bot.example.com/connect/feishu/go?", url)
        from urllib.parse import urlparse, parse_qs
        q = parse_qs(urlparse(url).query)
        self.assertTrue(_verify_go(q["u"][0], q["r"][0], q["exp"][0], q["sig"][0]))

    def test_verify_rejects_tampered_open_id(self):
        exp = int(feishu_bot.time.time()) + 600
        sig = _sign_go("ou_real", "/", exp)
        self.assertFalse(_verify_go("ou_attacker", "/", str(exp), sig))  # forged target

    def test_verify_rejects_expired(self):
        exp = int(feishu_bot.time.time()) - 10
        sig = _sign_go("ou_1", "/", exp)
        self.assertFalse(_verify_go("ou_1", "/", str(exp), sig))

    def test_verify_rejects_bad_sig(self):
        exp = int(feishu_bot.time.time()) + 600
        self.assertFalse(_verify_go("ou_1", "/", str(exp), "deadbeef"))

    # ── the Flask /go route ───────────────────────────────────────────────────

    def _client(self):
        return feishu_bot.create_flask_app().test_client()

    def test_go_valid_bound_mints_fresh_and_redirects(self):
        feishu_bot._user_bindings["ou_1"] = "key-abc"
        with patch("feishu_bot.CONNECTOR_PUBLIC_URL", "https://bot.example.com"), \
             patch("feishu_bot._mint_magic_for_path",
                   return_value="https://api.biomate/auth/magic?token=FRESH&redirect=%2F") as mint:
            url = _signed_go_url("ou_1", "/")
            from urllib.parse import urlparse
            path_q = urlparse(url).path + "?" + urlparse(url).query
            resp = self._client().get(path_q)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.headers["Location"], "https://api.biomate/auth/magic?token=FRESH&redirect=%2F")
        mint.assert_called_once_with("key-abc", "/")  # minted at click time

    def test_go_bad_signature_redirects_to_app_root(self):
        with patch("feishu_bot.BIOMATE_DEEP_LINK_BASE", "https://app.biomate.ai"):
            resp = self._client().get("/connect/feishu/go?u=ou_1&r=/&exp=99999999999&sig=forged")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.headers["Location"], "https://app.biomate.ai")

    def test_go_valid_sig_but_unbound_redirects_to_app_root(self):
        with patch("feishu_bot.CONNECTOR_PUBLIC_URL", "https://bot.example.com"), \
             patch("feishu_bot.BIOMATE_DEEP_LINK_BASE", "https://app.biomate.ai"):
            url = _signed_go_url("ou_unbound", "/")
            from urllib.parse import urlparse
            path_q = urlparse(url).path + "?" + urlparse(url).query
            resp = self._client().get(path_q)  # no binding for ou_unbound
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.headers["Location"], "https://app.biomate.ai")

    # ── card uses /go link when bound + public URL set ────────────────────────

    def test_card_uses_go_link_when_bound_and_public_url(self):
        feishu_bot._conversation_history.clear()
        _seen_message_ids.clear()
        feishu_bot._user_bindings["ou_card"] = "key-xyz"
        cards = []
        events = [
            ("delta", {"text": "Found workflow."}),
            ("workflow_ready", {"workflow_name": "wf"}),
            ("done", {}),
        ]
        event = {
            "message": {"message_id": "om_go_card", "chat_id": "oc_x", "message_type": "text",
                        "content": json.dumps({"text": "run wf"})},
            "sender": {"sender_id": {"open_id": "ou_card"}},
        }
        with MockSSEServer(events=events) as srv:
            with patch("feishu_bot.CONNECTOR_PUBLIC_URL", "https://bot.example.com"), \
                 patch("feishu_bot.send_text_message", return_value=True), \
                 patch("feishu_bot.send_workflow_card",
                       side_effect=lambda cid, **k: cards.append(k) or True):
                handle_message_event(event, _base_url_override=srv.base_url)

        self.assertEqual(len(cards), 1)
        self.assertIn("https://bot.example.com/connect/feishu/go?", cards[0]["url"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
