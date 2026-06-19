"""
BioMate Feishu / Lark Integration
=================================
Exposes BioMate's scientific AI as a Feishu (飞书) / Lark bot. Users @-mention
the bot or DM it in natural language; each message is routed through BioMate's
/api/chat/stream endpoint and answered with the AI narration plus a
"Run in BioMate" deep link.

Setup (one-time):
    1. Create a custom app at https://open.feishu.cn/ (Lark: https://open.larksuite.com/)
    2. Enable the bot capability and add scope: im:message, im:message:send_as_bot
    3. Under "Event Subscriptions" set the request URL to:
         https://<your-domain>/connect/feishu/webhook
       and subscribe to im.message.receive_v1
    4. Set env vars: FEISHU_APP_ID, FEISHU_APP_SECRET, FEISHU_VERIFY_TOKEN
       (optionally FEISHU_BASE for Lark international)
    5. Disable "Encrypt Key" (encrypt-mode decryption is NOT implemented here),
       or add a WBizMsgCrypt-equivalent decrypt step.

Authentication flow:
    - Inbound events are checked against FEISHU_VERIFY_TOKEN.
    - BioMate account binding: user sends "bind <biomate_api_key>" →
      Feishu open_id stored alongside the BioMate API key.

API reference:
    https://open.feishu.cn/document/server-docs/im-v1/message/create
"""

import json
import logging
import os
import re
import threading
import time
from collections import deque
from typing import Any, Deque, Dict, List, Optional, Tuple
from urllib.parse import quote, urlparse

import requests

log = logging.getLogger(__name__)

FEISHU_APP_ID = os.environ.get("FEISHU_APP_ID", "")
FEISHU_APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")
FEISHU_VERIFY_TOKEN = os.environ.get("FEISHU_VERIFY_TOKEN", "")
FEISHU_BASE = os.environ.get("FEISHU_BASE", "https://open.feishu.cn")  # Lark: https://open.larksuite.com
BIOMATE_API_URL = os.environ.get("BIOMATE_API_URL", "http://localhost:5000")
BIOMATE_API_KEY = os.environ.get("BIOMATE_API_KEY", "")
BIOMATE_DEEP_LINK_BASE = os.environ.get("BIOMATE_DEEP_LINK_BASE", "https://app.biomate.ai")


# ──────────────────────────────────────────────────────────────────────────────
# Feishu API token management (tenant_access_token, cached)
# ──────────────────────────────────────────────────────────────────────────────

_tenant_token: Dict[str, Any] = {"token": None, "expires_at": 0}
_token_lock = threading.Lock()


def get_tenant_access_token(_base_url_override: Optional[str] = None) -> str:
    """
    Fetch or return a cached Feishu tenant_access_token.
    Tokens are valid for ~7200s; refreshed automatically.
    """
    with _token_lock:
        if _tenant_token["token"] and time.time() < _tenant_token["expires_at"] - 60:
            return _tenant_token["token"]

        base = _base_url_override or FEISHU_BASE
        r = requests.post(
            f"{base}/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET},
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        if data.get("code", 0) != 0:
            raise RuntimeError(f"Feishu token error: {data}")
        _tenant_token["token"] = data["tenant_access_token"]
        _tenant_token["expires_at"] = time.time() + data.get("expire", 7200)
        return _tenant_token["token"]


# ──────────────────────────────────────────────────────────────────────────────
# Sending messages via the Feishu IM API
# ──────────────────────────────────────────────────────────────────────────────

def send_text_message(
    receive_id: str,
    text: str,
    receive_id_type: str = "chat_id",
    _base_url_override: Optional[str] = None,
) -> bool:
    """Send a plain-text message to a Feishu chat / user."""
    try:
        token = get_tenant_access_token(_base_url_override=_base_url_override)
    except Exception as exc:
        log.error(f"Feishu token fetch failed: {exc}")
        return False

    base = _base_url_override or FEISHU_BASE
    payload = {
        "receive_id": receive_id,
        "msg_type": "text",
        "content": json.dumps({"text": text}),
    }
    try:
        r = requests.post(
            f"{base}/open-apis/im/v1/messages",
            params={"receive_id_type": receive_id_type},
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=payload,
            timeout=10,
        )
        r.raise_for_status()
        result = r.json()
        if result.get("code", 0) != 0:
            log.error(f"Feishu send error: {result}")
            return False
        return True
    except Exception as exc:
        log.error(f"Feishu message send failed: {exc}")
        return False


def send_workflow_card(
    receive_id: str,
    workflow_name: str,
    url: str,
    receive_id_type: str = "chat_id",
    _base_url_override: Optional[str] = None,
) -> bool:
    """
    Send an interactive card with an "Open in BioMate" button linking to `url`
    (a server-provided view_url, or the app panel).
    """
    try:
        token = get_tenant_access_token(_base_url_override=_base_url_override)
    except Exception as exc:
        log.error(f"Feishu token fetch failed: {exc}")
        return False

    base = _base_url_override or FEISHU_BASE
    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": f"BioMate: {workflow_name}"},
            "template": "blue",
        },
        "elements": [
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "打开 BioMate 面板 / Open in BioMate"},
                        "type": "primary",
                        "url": url,
                    }
                ],
            }
        ],
    }
    payload = {
        "receive_id": receive_id,
        "msg_type": "interactive",
        "content": json.dumps(card),
    }
    try:
        r = requests.post(
            f"{base}/open-apis/im/v1/messages",
            params={"receive_id_type": receive_id_type},
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=payload,
            timeout=10,
        )
        r.raise_for_status()
        return r.json().get("code", 0) == 0
    except Exception as exc:
        log.error(f"Feishu workflow card failed: {exc}")
        return False


# ──────────────────────────────────────────────────────────────────────────────
# BioMate chat engine — routes Feishu queries through /api/chat/stream
# ──────────────────────────────────────────────────────────────────────────────

# Per-user conversation history: {open_id: deque of {role, content} dicts}
_MAX_HISTORY = 10
_conversation_history: Dict[str, Deque[Dict[str, str]]] = {}
_history_lock = threading.Lock()


def _get_history(user_id: str) -> List[Dict[str, str]]:
    with _history_lock:
        return list(_conversation_history.get(user_id, deque()))


def _push_history(user_id: str, role: str, content: str) -> None:
    with _history_lock:
        if user_id not in _conversation_history:
            _conversation_history[user_id] = deque(maxlen=_MAX_HISTORY)
        _conversation_history[user_id].append({"role": role, "content": content})


def _iter_sse_events(resp):
    """
    Yield (event_name, data_str) pairs from an SSE response.

    Robust against two quirks observed on BioMate's live /api/chat/stream:
      - the body is UTF-8 but text/event-stream defaults to latin-1, so we force
        resp.encoding = "utf-8" (otherwise CJK text is mojibake and stray 0x85
        bytes get mis-split by str.splitlines) and split only on "\\n".
      - a single data: payload may be split across unprefixed continuation
        lines, so we buffer all data until the blank line that ends the event.
    """
    resp.encoding = "utf-8"
    event = "message"
    data_buf: List[str] = []
    for raw_line in resp.iter_lines(decode_unicode=True, delimiter="\n"):
        if raw_line is None:
            continue
        line = raw_line.rstrip("\r")
        if line == "":
            if data_buf:
                yield event, "".join(data_buf)
                data_buf = []
            event = "message"
            continue
        if line.startswith(":"):
            continue  # SSE comment / heartbeat
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            chunk = line[5:]
            if chunk.startswith(" "):
                chunk = chunk[1:]
            data_buf.append(chunk)
        else:
            data_buf.append(line)  # continuation of a split data: line
    if data_buf:
        yield event, "".join(data_buf)


def _open_claw_query(
    user_id: str,
    query: str,
    api_key: Optional[str] = None,
    timeout: int = 55,
    _base_url_override: Optional[str] = None,
) -> Tuple[str, Optional[str], Optional[str]]:
    """
    Send a query through /api/chat/stream (BioMate's main AI endpoint).
    Returns (reply_text, workflow_name_or_None, view_url_or_None).

    /api/chat/stream emits SSE events:
        event: delta          / data: {"text": "..."}
        event: workflow_ready / data: {"workflow_name": ..., "view_url": ...}
        event: final          / data: {"workflow": {...}, "response": "..."}
        event: done           / data: {}

    We accumulate delta events for the reply, capture the workflow_name from
    workflow_ready (or final) for the card title, and capture a server-provided
    view_url for the button (caller falls back to the app panel if absent).
    """
    headers: Dict[str, str] = {"Content-Type": "application/json", "Accept": "text/event-stream"}
    effective_key = api_key or BIOMATE_API_KEY
    if effective_key:
        headers["Authorization"] = f"Bearer {effective_key}"

    text_parts: List[str] = []
    workflow_id: Optional[str] = None  # workflow display name (card title)
    view_url: Optional[str] = None     # server-provided deep link, if any

    base_url = _base_url_override or BIOMATE_API_URL

    history = _get_history(user_id)
    context: Dict[str, Any] = {}
    if history:
        context["priorMessages"] = history[-6:]

    payload: Dict[str, Any] = {"message": query}
    if context:
        payload["context"] = context

    try:
        with requests.post(
            f"{base_url}/api/chat/stream",
            json=payload,
            headers=headers,
            stream=True,
            timeout=timeout,
        ) as resp:
            if resp.status_code == 503:
                return "❌ BioMate AI engine不可用（API密钥未配置）。请联系管理员。", None, None
            if resp.status_code == 400:
                return "❌ 请求格式错误，请重试。", None, None
            if resp.status_code != 200:
                return f"❌ BioMate返回错误 {resp.status_code}，请稍后重试。", None, None

            for current_event, data_str in _iter_sse_events(resp):
                try:
                    data = json.loads(data_str)
                except json.JSONDecodeError:
                    continue

                if current_event == "delta" and isinstance(data, dict):
                    text_parts.append(data.get("text", ""))

                elif current_event == "workflow_ready" and isinstance(data, dict):
                    workflow_id = (
                        data.get("workflow_name")
                        or data.get("name")
                        or data.get("chain_display_name")
                    )
                    view_url = view_url or data.get("view_url")

                elif current_event == "final" and isinstance(data, dict):
                    wf = data.get("workflow") or {}
                    if not workflow_id:
                        workflow_id = (
                            wf.get("workflow_ga", {}).get("name")
                            or wf.get("workflow_name")
                            or wf.get("chain_display_name")
                        )
                    view_url = view_url or data.get("view_url") or wf.get("view_url")

                elif current_event in ("done", "complete"):
                    break

    except requests.exceptions.Timeout:
        return "⏱ BioMate响应超时，请稍后重试。", None, None
    except Exception as exc:
        log.exception(f"BioMate chat stream query failed for user {user_id}: {exc}")
        return f"❌ BioMate查询失败：{exc}", None, None

    reply_text = "".join(text_parts).strip()
    if not reply_text:
        reply_text = "BioMate正在处理您的请求，请稍后在应用中查看结果。"

    _push_history(user_id, "user", query)
    _push_history(user_id, "assistant", reply_text)

    return reply_text, workflow_id, view_url


def _build_magic_link(
    api_key: Optional[str],
    view_url: Optional[str],
    _base_url_override: Optional[str] = None,
) -> Optional[str]:
    """
    Wrap a server-provided view_url in a one-time auto-login link so the bound
    user lands logged-in on the workflow instead of the login page.

    Flow (no long-lived secret ever in the URL):
      1. POST /api/auth/login-token  (Bearer = the user's bound BioMate api_key)
         → a single-use, ~5-min login token.
      2. Build  …/api/auth/magic?token=<ott>&redirect=<view_url path>  — opening
         it sets the session cookie then 302s to the workflow.

    Returns the magic URL, or None on any failure (caller falls back to the bare
    view_url). Requires both a bound api_key and a server-provided view_url.
    """
    if not api_key or not view_url:
        return None
    api_base = (_base_url_override or BIOMATE_API_URL).rstrip("/")
    try:
        parsed = urlparse(view_url)
        redirect_path = parsed.path or "/"
        if parsed.query:
            redirect_path += "?" + parsed.query
        resp = requests.post(
            f"{api_base}/api/auth/login-token",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"redirect": redirect_path},
            timeout=10,
        )
        if resp.status_code != 200:
            log.warning(f"login-token failed ({resp.status_code}); using bare view_url")
            return None
        ott = (resp.json() or {}).get("token")
        if not ott:
            return None
        return (
            f"{api_base}/api/auth/magic"
            f"?token={quote(ott, safe='')}&redirect={quote(redirect_path, safe='')}"
        )
    except Exception as exc:
        log.warning(f"magic link build failed: {exc}")
        return None


# ──────────────────────────────────────────────────────────────────────────────
# Event parsing + message handler
# ──────────────────────────────────────────────────────────────────────────────

# Simple in-memory binding store: {open_id: biomate_api_key}
# For production: replace with database persistence.
_user_bindings: Dict[str, str] = {}

# Dedup store — Feishu retries events on non-2xx, so track processed message_ids.
_seen_message_ids: Deque[str] = deque(maxlen=2048)
_seen_lock = threading.Lock()


def _already_seen(message_id: str) -> bool:
    """Return True if this message_id was already processed (dedup retries)."""
    if not message_id:
        return False
    with _seen_lock:
        if message_id in _seen_message_ids:
            return True
        _seen_message_ids.append(message_id)
        return False


def extract_text(message: Dict[str, Any]) -> str:
    """
    Pull the user text out of a Feishu message event, stripping @mentions.
    Feishu text content is a JSON string like {"text": "@_user_1 hello"}.
    """
    content_raw = message.get("content", "")
    try:
        content = json.loads(content_raw) if isinstance(content_raw, str) else content_raw
    except json.JSONDecodeError:
        return ""
    text = (content or {}).get("text", "") if isinstance(content, dict) else ""
    # Feishu renders mentions as @_user_1, @_user_2, … placeholder tokens.
    for mention in (message.get("mentions") or []):
        key = mention.get("key", "")
        if key:
            text = text.replace(key, "")
    # Strip any leftover @_user_* placeholder tokens.
    text = re.sub(r"@_user_\d+", "", text)
    return text.strip()


def handle_message_event(event: Dict[str, Any], _base_url_override: Optional[str] = None) -> None:
    """
    Process an im.message.receive_v1 event.

    Handles:
      - "bind <api_key>"   → bind Feishu user to BioMate account
      - "unbind"           → remove binding
      - "clear" / "清除"   → clear conversation history
      - "help" / "帮助"    → help text
      - Any other text     → routed through BioMate /api/chat/stream
    """
    message = (event or {}).get("message", {}) or {}
    sender = (event or {}).get("sender", {}) or {}
    sender_id = (sender.get("sender_id") or {}) if isinstance(sender, dict) else {}

    message_id = message.get("message_id", "")
    if _already_seen(message_id):
        return  # duplicate retry — skip

    chat_id = message.get("chat_id", "")
    open_id = sender_id.get("open_id") or sender_id.get("user_id") or chat_id

    if message.get("message_type") != "text":
        send_text_message(
            chat_id, "BioMate仅支持文字查询。请输入您的分析需求。",
            _base_url_override=_base_url_override,
        )
        return

    text = extract_text(message)
    if not text:
        return

    lower = text.lower()

    if lower.startswith("bind "):
        api_key = text[5:].strip()
        if api_key:
            _user_bindings[open_id] = api_key
            send_text_message(chat_id, "✅ BioMate账号绑定成功！现在您可以直接发送分析请求了。",
                              _base_url_override=_base_url_override)
        else:
            send_text_message(chat_id, "❌ 请提供有效的API密钥：bind <your-api-key>",
                              _base_url_override=_base_url_override)
        return

    if lower == "unbind":
        _user_bindings.pop(open_id, None)
        send_text_message(chat_id, "✅ 已解除BioMate账号绑定。", _base_url_override=_base_url_override)
        return

    if lower in ("clear", "清除", "新对话", "reset"):
        with _history_lock:
            _conversation_history.pop(open_id, None)
        send_text_message(chat_id, "✅ 对话历史已清除，开始新对话。", _base_url_override=_base_url_override)
        return

    if lower in ("help", "帮助", "?", "？"):
        send_text_message(
            chat_id,
            "BioMate 生命科学AI助手\n\n"
            "发送分析请求，例如：\n"
            "• 对化合物列表进行ADMET筛选\n"
            "• RNA-seq差异表达分析\n"
            "• 蛋白质结构预测\n\n"
            "支持多轮对话——可直接追问上下文。\n\n"
            "命令：\n"
            "• bind <api-key>  绑定BioMate账号\n"
            "• unbind          解除绑定\n"
            "• clear           清除对话历史\n\n"
            f"打开应用：{BIOMATE_DEEP_LINK_BASE}",
            _base_url_override=_base_url_override,
        )
        return

    # Scientific query → BioMate chat engine.
    user_api_key = _user_bindings.get(open_id)
    reply_text, workflow_id, view_url = _open_claw_query(
        open_id, text, api_key=user_api_key, _base_url_override=_base_url_override,
    )

    send_text_message(chat_id, reply_text, _base_url_override=_base_url_override)

    # Build the "Open in BioMate" button target, in order of preference:
    #   1. magic auto-login link wrapping view_url (bound user → lands logged-in
    #      on the generated workflow)
    #   2. bare view_url (deep-links to the workflow, but may hit the login page)
    #   3. the app root (no addressable workflow)
    if workflow_id:
        url = (
            _build_magic_link(user_api_key, view_url, _base_url_override=_base_url_override)
            or view_url
            or BIOMATE_DEEP_LINK_BASE
        )
        send_workflow_card(
            chat_id,
            workflow_name=workflow_id,
            url=url,
            _base_url_override=_base_url_override,
        )


def handle_event(body: Dict[str, Any]) -> Dict[str, Any]:
    """
    Top-level Feishu webhook dispatcher. Returns a JSON-serializable dict that
    the Flask route echoes back.

    - type=url_verification → echo the challenge (event-subscription handshake)
    - im.message.receive_v1 → dispatch to handle_message_event() asynchronously
      so the webhook returns 200 fast (Feishu retries on slow/non-2xx).
    """
    # URL verification handshake (sent when you save the event request URL).
    if body.get("type") == "url_verification":
        if FEISHU_VERIFY_TOKEN and body.get("token") != FEISHU_VERIFY_TOKEN:
            log.warning("Feishu url_verification token mismatch")
            return {}
        return {"challenge": body.get("challenge", "")}

    # Event callbacks (schema 2.0) carry header + event.
    header = body.get("header", {}) or {}
    token = header.get("token") or body.get("token")
    if FEISHU_VERIFY_TOKEN and token and token != FEISHU_VERIFY_TOKEN:
        log.warning("Feishu event token mismatch — ignoring")
        return {}

    event_type = header.get("event_type") or body.get("type")
    if event_type == "im.message.receive_v1":
        event = body.get("event", {})
        threading.Thread(target=handle_message_event, args=(event,), daemon=True).start()

    return {}


# ──────────────────────────────────────────────────────────────────────────────
# Flask app (standalone deployment)
# ──────────────────────────────────────────────────────────────────────────────

def create_flask_app():
    """
    Minimal Flask app for the Feishu event webhook.
    Set the event request URL to: https://<domain>/connect/feishu/webhook

    POST /connect/feishu/webhook — url_verification + im.message.receive_v1
    GET  /connect/feishu/health  — health probe

    NOTE: encrypt-mode decryption is NOT implemented. Disable the Encrypt Key in
    the Feishu console, or add a WBizMsgCrypt-equivalent decrypt step here.
    """
    from flask import Flask, request, jsonify

    app = Flask("biomate-feishu")

    @app.route("/connect/feishu/webhook", methods=["POST"])
    def feishu_webhook():
        body = request.get_json(silent=True) or {}
        if "encrypt" in body:
            log.error("Feishu encrypt-mode payload received but decryption is not "
                      "implemented — disable Encrypt Key in the app console.")
            return jsonify({}), 200
        return jsonify(handle_event(body))

    @app.route("/connect/feishu/health", methods=["GET"])
    def health():
        return jsonify({"status": "ok", "service": "biomate-feishu"})

    return app


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="BioMate Feishu / Lark Bot")
    parser.add_argument("--port", type=int, default=8093)
    args = parser.parse_args()
    app = create_flask_app()
    log.warning(f"BioMate Feishu bot listening on port {args.port}")
    app.run(host="0.0.0.0", port=args.port)
