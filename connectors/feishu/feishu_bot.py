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

import hashlib
import hmac
import json
import logging
import os
import re
import threading
import time
import uuid
from collections import deque
from typing import Any, Deque, Dict, List, Optional, Tuple
from urllib.parse import quote, urlencode, urlparse

import requests

log = logging.getLogger(__name__)

FEISHU_APP_ID = os.environ.get("FEISHU_APP_ID", "")
FEISHU_APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")
FEISHU_VERIFY_TOKEN = os.environ.get("FEISHU_VERIFY_TOKEN", "")
FEISHU_BASE = os.environ.get("FEISHU_BASE", "https://open.feishu.cn")  # Lark: https://open.larksuite.com
BIOMATE_API_URL = os.environ.get("BIOMATE_API_URL", "http://localhost:5000")
BIOMATE_API_KEY = os.environ.get("BIOMATE_API_KEY", "")
BIOMATE_DEEP_LINK_BASE = os.environ.get("BIOMATE_DEEP_LINK_BASE", "https://app.biomate.ai")

# Public HTTPS base of THIS bot (e.g. https://connect.example.com). When set,
# workflow-card buttons point at the bot's own /connect/feishu/go redirect so a
# fresh one-time login token is minted at click time (avoids the single-use
# token being expired or pre-consumed by IM link previews). Falls back to a
# baked magic link / bare deep link when unset.
CONNECTOR_PUBLIC_URL = os.environ.get("CONNECTOR_PUBLIC_URL", "")
# Secret for HMAC-signing /go links so nobody can forge an auto-login URL for an
# arbitrary open_id. Dedicated env, else the always-present app secret.
_GO_SIGNING_SECRET = os.environ.get("CONNECTOR_SIGNING_SECRET", "") or FEISHU_APP_SECRET
_GO_LINK_TTL = 3600  # seconds a /go link stays valid


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
    button_text: str = "打开 BioMate 面板 / Open in BioMate",
    title: Optional[str] = None,
) -> bool:
    """
    Send an interactive card with a single button linking to `url`. Used both for
    the "Open in BioMate" workflow card and the "Link account" prompt.
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
            "title": {"tag": "plain_text", "content": title or f"BioMate: {workflow_name}"},
            "template": "blue",
        },
        "elements": [
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": button_text},
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
    session_id: Optional[str] = None,
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
    if session_id:
        # Persist the conversation + generated workflow under this session so the
        # card's ?session=<id> deep link reopens it in the user's browser (the
        # backend keys persistence on sessionId + the bound user's token).
        context["sessionId"] = session_id
        context["memorySessionId"] = session_id

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


def _redirect_path_from_view_url(view_url: Optional[str]) -> str:
    """Path (+query) to redirect to after auto-login; app home if no view_url."""
    if not view_url:
        return "/"
    parsed = urlparse(view_url)
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query
    return path


def _mint_magic_for_path(
    api_key: Optional[str],
    redirect_path: str,
    _base_url_override: Optional[str] = None,
) -> Optional[str]:
    """
    Mint a one-time auto-login link to `redirect_path` (no long-lived secret in
    the URL):
      1. POST /api/auth/login-token (Bearer = bound api_key) → single-use,
         ~5-min token.
      2. Build …/api/auth/magic?token=<ott>&redirect=<path> — opening it sets the
         session cookie then 302s to the destination.

    NOTE: the token is single-use + short-lived, so mint it at CLICK time (via
    the /connect/feishu/go route), not when the card is sent — otherwise an IM
    link preview or a slow click burns/expires it. Returns None on any failure.
    """
    if not api_key:
        return None
    api_base = (_base_url_override or BIOMATE_API_URL).rstrip("/")
    try:
        resp = requests.post(
            f"{api_base}/api/auth/login-token",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"redirect": redirect_path},
            timeout=10,
        )
        if resp.status_code != 200:
            log.warning(f"login-token failed ({resp.status_code})")
            return None
        ott = (resp.json() or {}).get("token")
        if not ott:
            return None
        return (
            f"{api_base}/api/auth/magic"
            f"?token={quote(ott, safe='')}&redirect={quote(redirect_path, safe='')}"
        )
    except Exception as exc:
        log.warning(f"magic link mint failed: {exc}")
        return None


def _build_magic_link(
    api_key: Optional[str],
    view_url: Optional[str],
    _base_url_override: Optional[str] = None,
) -> Optional[str]:
    """Bake a magic link now (fragile for IM — prefer the /go redirect)."""
    if not api_key:
        return None
    return _mint_magic_for_path(
        api_key, _redirect_path_from_view_url(view_url), _base_url_override=_base_url_override
    )


# ──────────────────────────────────────────────────────────────────────────────
# Click-time auto-login redirect (/connect/feishu/go)
# ──────────────────────────────────────────────────────────────────────────────
# The card button points at the bot's own /go route rather than baking a token.
# At the moment of the real click the bot mints a FRESH one-time token — so a
# link-preview prefetch or a slow click can never hand the user a dead token.
# The /go URL is HMAC-signed (open_id + redirect + expiry) so nobody can forge an
# auto-login link for an arbitrary user, and it self-expires.

def _sign_go(open_id: str, redirect_path: str, exp: int) -> str:
    msg = f"{open_id}\n{redirect_path}\n{exp}".encode("utf-8")
    return hmac.new(_GO_SIGNING_SECRET.encode("utf-8"), msg, hashlib.sha256).hexdigest()


def _signed_go_url(open_id: str, redirect_path: str = "/", ttl: int = _GO_LINK_TTL) -> Optional[str]:
    """Build a signed, self-expiring link to the bot's /go route. None if no public URL."""
    if not CONNECTOR_PUBLIC_URL or not open_id:
        return None
    exp = int(time.time()) + ttl
    sig = _sign_go(open_id, redirect_path, exp)
    qs = urlencode({"u": open_id, "r": redirect_path, "exp": exp, "sig": sig})
    return f"{CONNECTOR_PUBLIC_URL.rstrip('/')}/connect/feishu/go?{qs}"


def _verify_go(open_id: str, redirect_path: str, exp: str, sig: str) -> bool:
    try:
        exp_i = int(exp)
    except (TypeError, ValueError):
        return False
    if exp_i < int(time.time()):
        return False
    expected = _sign_go(open_id, redirect_path, exp_i)
    return hmac.compare_digest(expected, sig or "")


# ──────────────────────────────────────────────────────────────────────────────
# Account linking (OAuth-style, no token pasting)
# ──────────────────────────────────────────────────────────────────────────────
# The bot is served on the SAME domain as BioMate (…/connect/feishu/* via the
# reverse proxy), so after the user logs into BioMate the browser automatically
# sends the biomate_token cookie to /connect/feishu/link — the bot reads it and
# binds the Feishu user, no token pasting. The link carries a signed open_id so
# nobody can bind an account to someone else's Feishu id.

def _sign_link(open_id: str, exp: int) -> str:
    msg = f"link\n{open_id}\n{exp}".encode("utf-8")
    return hmac.new(_GO_SIGNING_SECRET.encode("utf-8"), msg, hashlib.sha256).hexdigest()


def _verify_link(open_id: str, exp: str, sig: str) -> bool:
    try:
        exp_i = int(exp)
    except (TypeError, ValueError):
        return False
    if exp_i < int(time.time()):
        return False
    return hmac.compare_digest(_sign_link(open_id, exp_i), sig or "")


def _account_link_url(open_id: str, ttl: int = 1800) -> str:
    """
    Direct link to the bot's /connect/feishu/link (a FULL-page load → reverse
    proxy → bot, NOT the SPA router). Same domain as BioMate, so the browser
    sends the biomate_token cookie and the bot can bind. Carries a signed
    open_id. (We deliberately do NOT route through /login?next=… because the
    SPA client-routes `next` and 404s on this server-only path.)
    """
    exp = int(time.time()) + ttl
    sig = _sign_link(open_id, exp)
    return f"{BIOMATE_DEEP_LINK_BASE.rstrip('/')}/connect/feishu/link?" + urlencode(
        {"u": open_id, "exp": exp, "sig": sig}
    )


def send_link_prompt(receive_id: str, open_id: str,
                     receive_id_type: str = "chat_id",
                     _base_url_override: Optional[str] = None) -> bool:
    """Send the 'Link your BioMate account' card to an unbound user."""
    return send_workflow_card(
        receive_id,
        workflow_name="",
        url=_account_link_url(open_id),
        receive_id_type=receive_id_type,
        _base_url_override=_base_url_override,
        title="绑定 BioMate 账号 / Link your BioMate account",
        button_text="绑定账号 / Link account",
    )


# ──────────────────────────────────────────────────────────────────────────────
# Event parsing + message handler
# ──────────────────────────────────────────────────────────────────────────────

# Simple in-memory binding store: {open_id: biomate_api_key}
# For production: replace with database persistence.
_user_bindings: Dict[str, str] = {}

# Per-user BioMate chat session id: {open_id: sessionId}. Passed to
# /api/chat/stream so the conversation + generated workflow persist server-side
# under the user's account; the card's ?session=<id> deep link reopens it.
# In-memory like the rest — a restart starts a fresh session (acceptable).
_user_sessions: Dict[str, str] = {}
_sessions_lock = threading.Lock()


def _get_session_id(open_id: str) -> str:
    """Stable BioMate chat-session id for a Feishu user (created on first use)."""
    with _sessions_lock:
        sid = _user_sessions.get(open_id)
        if not sid:
            sid = str(uuid.uuid4())
            _user_sessions[open_id] = sid
        return sid

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

    # Scientific query → BioMate chat engine. Require a linked account first so
    # the run + results tie to the user (and the magic deep link can log them in).
    user_api_key = _user_bindings.get(open_id)
    if not user_api_key:
        send_text_message(
            chat_id, "先绑定你的 BioMate 账号即可开始（点下方按钮，浏览器登录一下就好）。",
            _base_url_override=_base_url_override,
        )
        send_link_prompt(chat_id, open_id, _base_url_override=_base_url_override)
        return

    # Pass a per-user sessionId so the conversation + generated workflow persist
    # server-side and the card can deep-link the browser back to it (?session=<id>).
    session_id = _get_session_id(open_id)
    reply_text, workflow_id, view_url = _open_claw_query(
        open_id, text, api_key=user_api_key, _base_url_override=_base_url_override,
        session_id=session_id,
    )

    send_text_message(chat_id, reply_text, _base_url_override=_base_url_override)

    # Build the "Open in BioMate" button target, in order of preference:
    #   1. signed /go redirect → mints a FRESH one-time login token at click time
    #      (previews/slow clicks can't kill it) and lands on the chat session so
    #      the user reviews the generated workflow's params, then Confirm & Run.
    #   2. baked magic link to the session (bound user, no public bot URL)
    #   3. server view_url, else the session deep link, else the app root.
    if workflow_id:
        session_path = f"/?session={session_id}"
        url = (
            (_signed_go_url(open_id, session_path) if user_api_key else None)
            or (_build_magic_link(user_api_key, BIOMATE_DEEP_LINK_BASE + session_path,
                                  _base_url_override=_base_url_override) if user_api_key else None)
            or view_url
            or (BIOMATE_DEEP_LINK_BASE + session_path)
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
    from flask import Flask, request, jsonify, redirect

    app = Flask("biomate-feishu")

    @app.route("/connect/feishu/webhook", methods=["POST"])
    def feishu_webhook():
        body = request.get_json(silent=True) or {}
        if "encrypt" in body:
            log.error("Feishu encrypt-mode payload received but decryption is not "
                      "implemented — disable Encrypt Key in the app console.")
            return jsonify({}), 200
        return jsonify(handle_event(body))

    @app.route("/connect/feishu/go", methods=["GET"])
    def feishu_go():
        """
        Click-time auto-login redirect. Verifies the signed link, mints a FRESH
        one-time login token for the bound user, then 302s into BioMate already
        logged-in. Falls back to the app root (login page) if anything is off.
        """
        open_id = request.args.get("u", "")
        redirect_path = request.args.get("r", "/")
        if not _verify_go(open_id, redirect_path, request.args.get("exp", ""),
                          request.args.get("sig", "")):
            log.warning("feishu /go: bad or expired signature")
            return redirect(BIOMATE_DEEP_LINK_BASE, code=302)
        api_key = _user_bindings.get(open_id)
        if not api_key:
            return redirect(BIOMATE_DEEP_LINK_BASE, code=302)
        magic = _mint_magic_for_path(api_key, redirect_path)
        return redirect(magic or BIOMATE_DEEP_LINK_BASE, code=302)

    @app.route("/connect/feishu/link", methods=["GET"])
    def feishu_link():
        """
        Account-linking landing. Reached after the user logs into BioMate (via
        /login?next=…). Same domain → the browser sends the biomate_token cookie;
        we verify the signed open_id, confirm the token works, then bind. No
        token pasting.
        """
        open_id = request.args.get("u", "")
        if not _verify_link(open_id, request.args.get("exp", ""), request.args.get("sig", "")):
            return _link_page("链接无效或已过期，请回飞书重新点「绑定账号」。",
                              "Link invalid or expired — tap Link account again in Feishu."), 403
        token = request.cookies.get("biomate_token", "")
        if not token:
            # Not logged in → show a login button. (We can't auto-return via
            # /login?next= because the SPA client-routes `next` and 404s on this
            # server path, so the user logs in then taps the Feishu button again.)
            login_url = f"{BIOMATE_DEEP_LINK_BASE.rstrip('/')}/login"
            return _link_page(
                "请先登录 BioMate，然后回飞书再点一次「绑定账号」。",
                "Log in to BioMate, then tap Link account again in Feishu.",
                button=(login_url, "登录 BioMate / Log in"),
            )
        # Confirm the token actually authenticates before binding.
        if not _mint_magic_for_path(token, "/"):
            return _link_page("登录态无效，请重新登录后再点绑定。",
                              "Session invalid — log in again then retry."), 401
        _user_bindings[open_id] = token
        log.warning(f"Feishu account linked for open_id={open_id[:8]}…")
        return _link_page("✅ 绑定成功！回飞书继续，直接发你的分析请求即可。",
                          "Linked! Return to Feishu and send your analysis request.")

    @app.route("/connect/feishu/health", methods=["GET"])
    def health():
        return jsonify({"status": "ok", "service": "biomate-feishu"})

    return app


def _link_page(zh: str, en: str, button: Optional[Tuple[str, str]] = None) -> str:
    btn = ""
    if button:
        href, label = button
        btn = (f"<a href='{href}' style='display:inline-block;margin-top:1.2rem;"
               "padding:.7rem 1.6rem;background:#3370ff;color:#fff;border-radius:8px;"
               f"text-decoration:none;font-weight:600'>{label}</a>")
    return (
        "<!doctype html><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<div style='font-family:-apple-system,sans-serif;max-width:30rem;margin:18vh auto;"
        "text-align:center;padding:0 1.5rem;color:#1f2329'>"
        f"<h2 style='font-weight:600'>{zh}</h2>"
        f"<p style='color:#646a73'>{en}</p>{btn}</div>"
    )


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="BioMate Feishu / Lark Bot")
    parser.add_argument("--port", type=int, default=8093)
    args = parser.parse_args()
    app = create_flask_app()
    log.warning(f"BioMate Feishu bot listening on port {args.port}")
    app.run(host="0.0.0.0", port=args.port)
