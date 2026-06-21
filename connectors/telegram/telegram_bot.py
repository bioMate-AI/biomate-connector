"""
BioMate Telegram Integration
============================
Exposes BioMate's scientific AI as a Telegram bot. Users chat in natural
language; the bot routes each message through BioMate's /api/chat/stream
endpoint and replies with the AI narration plus a "Run in BioMate" deep link.

Setup (one-time):
    1. Talk to @BotFather on Telegram → /newbot → copy the bot token.
    2. Set env var TELEGRAM_BOT_TOKEN to that token.
    3. Deploy this Flask app at a public HTTPS URL.
    4. Register the webhook with Telegram:
         curl "https://api.telegram.org/bot<TOKEN>/setWebhook" \
              -d "url=https://<your-domain>/connect/telegram/webhook"

Authentication flow:
    - BioMate account binding: user sends "/bind <biomate_api_key>" →
      Telegram chat_id stored alongside the BioMate API key.

API reference:
    https://core.telegram.org/bots/api
"""

import json
import logging
import os
import threading
from collections import deque
from typing import Any, Deque, Dict, List, Optional, Tuple

import requests

log = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
BIOMATE_API_URL = os.environ.get("BIOMATE_API_URL", "http://localhost:5000")
BIOMATE_API_KEY = os.environ.get("BIOMATE_API_KEY", "")
BIOMATE_DEEP_LINK_BASE = os.environ.get("BIOMATE_DEEP_LINK_BASE", "https://app.biomate.ai")

# Telegram hard limit on a single message body.
TELEGRAM_MAX_CHARS = 4096


def _telegram_api_base() -> str:
    return f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"


# ──────────────────────────────────────────────────────────────────────────────
# Sending messages via the Telegram Bot API
# ──────────────────────────────────────────────────────────────────────────────

def send_message(chat_id: Any, text: str, disable_web_page_preview: bool = True) -> bool:
    """
    Send a text message to a Telegram chat. Truncates to Telegram's 4096-char
    limit (workflow cards are sent as separate follow-up messages).
    """
    if not TELEGRAM_BOT_TOKEN:
        log.warning("TELEGRAM_BOT_TOKEN not set — cannot send message")
        return False

    if len(text) > TELEGRAM_MAX_CHARS:
        suffix = f"\n\n[查看完整结果 / full results: {BIOMATE_DEEP_LINK_BASE}]"
        text = text[: TELEGRAM_MAX_CHARS - len(suffix)] + suffix

    try:
        r = requests.post(
            f"{_telegram_api_base()}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text,
                "disable_web_page_preview": disable_web_page_preview,
            },
            timeout=10,
        )
        r.raise_for_status()
        result = r.json()
        if not result.get("ok"):
            log.error(f"Telegram send error: {result}")
            return False
        return True
    except Exception as exc:
        log.error(f"Telegram sendMessage failed: {exc}")
        return False


def send_workflow_card(chat_id: Any, workflow_name: str, url: str) -> bool:
    """
    Send a follow-up message with an "Open in BioMate" button linking to `url`
    (a server-provided view_url, or the app panel). Telegram has no native card
    type, so we use an inline keyboard.
    """
    if not TELEGRAM_BOT_TOKEN:
        log.warning("TELEGRAM_BOT_TOKEN not set — cannot send workflow card")
        return False

    payload = {
        # Plain text (no parse_mode) so workflow names with _, *, (), / render as-is.
        "chat_id": chat_id,
        "text": f"🧬 BioMate workflow: {workflow_name}",
        "reply_markup": {
            "inline_keyboard": [[{"text": "打开 BioMate 面板 / Open in BioMate", "url": url}]]
        },
    }
    try:
        r = requests.post(f"{_telegram_api_base()}/sendMessage", json=payload, timeout=10)
        r.raise_for_status()
        return bool(r.json().get("ok"))
    except Exception as exc:
        log.error(f"Telegram workflow card failed: {exc}")
        return False


# ──────────────────────────────────────────────────────────────────────────────
# BioMate chat engine — routes Telegram queries through /api/chat/stream
# ──────────────────────────────────────────────────────────────────────────────

# Per-user conversation history: {chat_id: deque of {role, content} dicts}
# Keeps last 10 turns so follow-up questions work naturally.
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
    view_url for the button (the caller falls back to the app panel if absent —
    the chat-generated workflow has no name-addressable URL, so we never build a
    deep link from the workflow name).
    """
    headers: Dict[str, str] = {"Content-Type": "application/json", "Accept": "text/event-stream"}
    effective_key = api_key or BIOMATE_API_KEY
    if effective_key:
        headers["Authorization"] = f"Bearer {effective_key}"

    text_parts: List[str] = []
    workflow_id: Optional[str] = None  # workflow display name (card title)
    view_url: Optional[str] = None     # server-provided deep link, if any

    base_url = _base_url_override or BIOMATE_API_URL

    # Include recent conversation context so the AI has multi-turn continuity.
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
                    # Workflow identified — capture its display name + any view_url.
                    workflow_id = (
                        data.get("workflow_name")
                        or data.get("name")
                        or data.get("chain_display_name")
                    )
                    view_url = view_url or data.get("view_url")

                elif current_event == "final" and isinstance(data, dict):
                    # Fallback: pick workflow name / view_url from final if needed.
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

    # Persist turn into history
    _push_history(user_id, "user", query)
    _push_history(user_id, "assistant", reply_text)

    return reply_text, workflow_id, view_url


# ──────────────────────────────────────────────────────────────────────────────
# Update parsing + message handler
# ──────────────────────────────────────────────────────────────────────────────

# Simple in-memory binding store: {chat_id: biomate_api_key}
# For production: replace with database persistence.
_user_bindings: Dict[str, str] = {}

_HELP_TEXT = (
    "BioMate 生命科学AI助手\n\n"
    "发送分析请求，例如 / send an analysis request, e.g.:\n"
    "• 对阿司匹林和布洛芬进行ADMET筛选\n"
    "• RNA-seq differential expression, treated vs control\n"
    "• 蛋白质结构预测\n\n"
    "支持多轮对话——可直接追问上下文。\n\n"
    "命令 / commands:\n"
    "• /start            介绍\n"
    "• /help             帮助\n"
    "• /bind <api-key>   绑定BioMate账号\n"
    "• /unbind           解除绑定\n"
    "• /clear            清除对话历史\n\n"
    f"打开应用 / open the app: {BIOMATE_DEEP_LINK_BASE}"
)


def parse_update(update: Dict[str, Any]) -> Tuple[Optional[str], str]:
    """
    Extract (chat_id, text) from a Telegram update. Returns (None, "") when the
    update carries no usable text message (e.g. edited messages, callbacks).
    chat_id is returned as a string so it can key the binding/history dicts.
    """
    message = update.get("message") or update.get("edited_message") or {}
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    text = (message.get("text") or "").strip()
    if chat_id is None:
        return None, ""
    return str(chat_id), text


def handle_update(update: Dict[str, Any]) -> None:
    """
    Process an incoming Telegram update.

    Handles:
      - /start, /help     → help text
      - /bind <api_key>   → bind Telegram chat to BioMate account
      - /unbind           → remove binding
      - /clear            → clear conversation history
      - Any other text    → routed through BioMate /api/chat/stream
    """
    chat_id, text = parse_update(update)
    if chat_id is None or not text:
        return

    # Telegram commands may arrive as "/bind@MyBot ..." in groups — strip @bot.
    parts = text.split(maxsplit=1)
    first_token = parts[0].split("@")[0].lower() if parts else ""
    arg = parts[1].strip() if len(parts) > 1 else ""

    if first_token in ("/start", "/help"):
        send_message(chat_id, _HELP_TEXT)
        return

    if first_token == "/bind":
        api_key = arg
        if api_key:
            _user_bindings[chat_id] = api_key
            send_message(chat_id, "✅ BioMate账号绑定成功！现在您可以直接发送分析请求了。")
        else:
            send_message(chat_id, "❌ 请提供有效的API密钥：/bind <your-api-key>")
        return

    if first_token == "/unbind":
        _user_bindings.pop(chat_id, None)
        send_message(chat_id, "✅ 已解除BioMate账号绑定。")
        return

    if first_token in ("/clear", "/reset"):
        with _history_lock:
            _conversation_history.pop(chat_id, None)
        send_message(chat_id, "✅ 对话历史已清除，开始新对话。")
        return

    # Scientific query → BioMate chat engine.
    user_api_key = _user_bindings.get(chat_id)
    reply_text, workflow_id, view_url = _open_claw_query(chat_id, text, api_key=user_api_key)

    send_message(chat_id, reply_text)

    # Follow-up message with an Open-in-BioMate button once a workflow is identified.
    # Prefer a server-provided view_url; otherwise open the app panel (the
    # chat-generated workflow lives in the user's session panel, not at a
    # name-addressable URL).
    if workflow_id:
        # BioMate has no per-workflow route today and no URL auto-login, so the
        # only non-404 target is the app root (the chat home). Prefer a
        # server-provided view_url once /api/chat/stream emits one.
        url = view_url or BIOMATE_DEEP_LINK_BASE
        send_workflow_card(chat_id, workflow_name=workflow_id, url=url)


# ──────────────────────────────────────────────────────────────────────────────
# Flask app (standalone deployment)
# ──────────────────────────────────────────────────────────────────────────────

def create_flask_app():
    """
    Minimal Flask app for the Telegram webhook.
    Register with: setWebhook url=https://<domain>/connect/telegram/webhook

    POST /connect/telegram/webhook — incoming update handler
    GET  /connect/telegram/health  — health probe
    """
    from flask import Flask, request, jsonify

    app = Flask("biomate-telegram")

    @app.route("/connect/telegram/webhook", methods=["POST"])
    def telegram_webhook():
        if not TELEGRAM_BOT_TOKEN:
            log.error("TELEGRAM_BOT_TOKEN not set — refusing Telegram update")
            return jsonify({"ok": False, "error": "not configured"}), 503

        update = request.get_json(silent=True) or {}

        # Telegram retries on non-2xx, so dispatch async and return 200 fast.
        threading.Thread(target=handle_update, args=(update,), daemon=True).start()
        return jsonify({"ok": True})

    @app.route("/connect/telegram/health", methods=["GET"])
    def health():
        return jsonify({"status": "ok", "service": "biomate-telegram"})

    return app


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="BioMate Telegram Bot")
    parser.add_argument("--port", type=int, default=8092)
    args = parser.parse_args()
    app = create_flask_app()
    log.warning(f"BioMate Telegram bot listening on port {args.port}")
    app.run(host="0.0.0.0", port=args.port)
