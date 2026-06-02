"""
BioMate WeChat Work (企业微信) Integration
==========================================
Registers BioMate as a WeChat Work service application, handles incoming
messages via the WeChat Work webhook, and responds with workflow cards
plus "Open in BioMate" deep links.

Setup (one-time manual steps):
    1. Register a WeChat Work custom app at https://work.weixin.qq.com/
    2. Under "Receive messages" → set API URL:
       https://<your-domain>/integrations/wechat/message
    3. Set Token and EncodingAESKey in WeChat admin console
    4. Set env vars: WECHAT_CORP_ID, WECHAT_CORP_SECRET,
                     WECHAT_TOKEN, WECHAT_ENCODING_AES_KEY
       Optional: WECHAT_AGENT_ID (app agent ID for sending messages)

Authentication flow:
    - Incoming message: verified by HMAC-SHA1 + XML decrypt (WXBizMsgCrypt)
    - BioMate account binding: user sends "bind <biomate_api_key>" in WeChat →
      WeChat user_id stored alongside BioMate account

API reference:
    https://developer.work.weixin.qq.com/document/path/90236
"""

import hashlib
import json
import logging
import os
import threading
import time
import xml.etree.ElementTree as ET
from collections import deque
from typing import Any, Deque, Dict, List, Optional, Tuple

import requests

log = logging.getLogger(__name__)

WECHAT_CORP_ID = os.environ.get("WECHAT_CORP_ID", "")
WECHAT_CORP_SECRET = os.environ.get("WECHAT_CORP_SECRET", "")
WECHAT_TOKEN = os.environ.get("WECHAT_TOKEN", "")
WECHAT_ENCODING_AES_KEY = os.environ.get("WECHAT_ENCODING_AES_KEY", "")
WECHAT_AGENT_ID = os.environ.get("WECHAT_AGENT_ID", "")
BIOMATE_API_URL = os.environ.get("BIOMATE_API_URL", "http://localhost:5000")
BIOMATE_API_KEY = os.environ.get("BIOMATE_API_KEY", "")
BIOMATE_DEEP_LINK_BASE = os.environ.get("BIOMATE_DEEP_LINK_BASE", "https://app.biomate.ai")

WECHAT_API_BASE = "https://qyapi.weixin.qq.com/cgi-bin"

# ──────────────────────────────────────────────────────────────────────────────
# WeChat API token management
# ──────────────────────────────────────────────────────────────────────────────

_access_token: Dict[str, Any] = {"token": None, "expires_at": 0}
_token_lock = threading.Lock()


def get_access_token() -> str:
    """
    Fetch or return cached WeChat Work access token.
    Tokens are valid for 7200s; refreshed automatically.
    """
    with _token_lock:
        if _access_token["token"] and time.time() < _access_token["expires_at"] - 60:
            return _access_token["token"]

        r = requests.get(
            f"{WECHAT_API_BASE}/gettoken",
            params={"corpid": WECHAT_CORP_ID, "corpsecret": WECHAT_CORP_SECRET},
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        if data.get("errcode", 0) != 0:
            raise RuntimeError(f"WeChat token error: {data}")
        _access_token["token"] = data["access_token"]
        _access_token["expires_at"] = time.time() + data.get("expires_in", 7200)
        return _access_token["token"]


# ──────────────────────────────────────────────────────────────────────────────
# Signature verification (GET handshake + message verification)
# ──────────────────────────────────────────────────────────────────────────────

def verify_signature(token: str, timestamp: str, nonce: str, msg_encrypt: str = "") -> str:
    """
    Compute WeChat Work signature: SHA1 of sorted concatenation.
    Used for:
      - URL validation (GET): check sign = SHA1(sort(token, timestamp, nonce))
      - Message decryption (POST): SHA1(sort(token, timestamp, nonce, msg_encrypt))
    """
    parts = sorted([token, timestamp, nonce] + ([msg_encrypt] if msg_encrypt else []))
    return hashlib.sha1("".join(parts).encode("utf-8")).hexdigest()


def verify_url_signature(token: str, timestamp: str, nonce: str, expected_sign: str) -> bool:
    """Verify WeChat's GET handshake request."""
    return verify_signature(token, timestamp, nonce) == expected_sign


# ──────────────────────────────────────────────────────────────────────────────
# Message parsing (plaintext mode — no AES encryption)
# For production with AES encryption, integrate WXBizMsgCrypt library
# ──────────────────────────────────────────────────────────────────────────────

def parse_message_xml(xml_body: str) -> Dict[str, str]:
    """
    Parse a WeChat Work message XML payload.
    Returns dict with: ToUserName, FromUserName, CreateTime, MsgType, Content, MsgId
    """
    try:
        root = ET.fromstring(xml_body)
        return {child.tag: (child.text or "") for child in root}
    except ET.ParseError as exc:
        log.error(f"WeChat XML parse error: {exc}")
        return {}


def build_text_reply(to_user: str, from_user: str, content: str) -> str:
    """Build an XML text reply message to WeChat Work."""
    ts = str(int(time.time()))
    return f"""<xml>
<ToUserName><![CDATA[{to_user}]]></ToUserName>
<FromUserName><![CDATA[{from_user}]]></FromUserName>
<CreateTime>{ts}</CreateTime>
<MsgType><![CDATA[text]]></MsgType>
<Content><![CDATA[{content}]]></Content>
</xml>"""


# ──────────────────────────────────────────────────────────────────────────────
# Sending messages via WeChat Work API
# ──────────────────────────────────────────────────────────────────────────────

def send_text_message(to_user: str, content: str, agent_id: Optional[str] = None) -> bool:
    """Send a text message to a WeChat Work user."""
    token = get_access_token()
    payload = {
        "touser": to_user,
        "msgtype": "text",
        "agentid": int(agent_id or WECHAT_AGENT_ID or 0),
        "text": {"content": content},
    }
    r = requests.post(
        f"{WECHAT_API_BASE}/message/send",
        params={"access_token": token},
        json=payload,
        timeout=10,
    )
    r.raise_for_status()
    result = r.json()
    if result.get("errcode", 0) != 0:
        log.error(f"WeChat send error: {result}")
        return False
    return True


def send_workflow_card(
    to_user: str,
    workflow_name: str,
    description: str,
    workflow_id: str,
    agent_id: Optional[str] = None,
) -> bool:
    """
    Send a WeChat Work "text card" for a BioMate workflow recommendation.
    Text cards show a title, description, and "Read more" button (deep link).
    """
    token = get_access_token()
    deep_link = f"{BIOMATE_DEEP_LINK_BASE}?workflow={workflow_id}"

    payload = {
        "touser": to_user,
        "msgtype": "textcard",
        "agentid": int(agent_id or WECHAT_AGENT_ID or 0),
        "textcard": {
            "title": f"BioMate: {workflow_name[:28]}",
            "description": description[:512],
            "url": deep_link,
            "btntxt": "在BioMate中运行",  # "Run in BioMate"
        },
    }
    r = requests.post(
        f"{WECHAT_API_BASE}/message/send",
        params={"access_token": token},
        json=payload,
        timeout=10,
    )
    r.raise_for_status()
    result = r.json()
    return result.get("errcode", 0) == 0


# ──────────────────────────────────────────────────────────────────────────────
# BioMate chat engine — routes WeChat queries through /api/chat/stream
# ──────────────────────────────────────────────────────────────────────────────

# Per-user conversation history: {wechat_user_id: deque of {role, content} dicts}
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


def _open_claw_query(
    user_id: str,
    query: str,
    api_key: Optional[str] = None,
    timeout: int = 55,
    _base_url_override: Optional[str] = None,
) -> Tuple[str, Optional[str]]:
    """
    Send a query through /api/chat/stream (BioMate's main AI endpoint).
    Returns (reply_text, workflow_id_or_None).

    /api/chat/stream emits SSE events:
        event: delta          / data: {"text": "..."}
        event: workflow_ready / data: {"workflow_name": ..., "workflow_type": ...}
        event: final          / data: {"workflow": {...}, "response": "..."}
        event: done           / data: {}

    We accumulate delta events for the reply and capture the workflow_name
    from workflow_ready (or final) for the "Run in BioMate" deep-link button.
    """
    headers: Dict[str, str] = {"Content-Type": "application/json", "Accept": "text/event-stream"}
    effective_key = api_key or BIOMATE_API_KEY
    if effective_key:
        headers["Authorization"] = f"Bearer {effective_key}"

    text_parts: List[str] = []
    workflow_id: Optional[str] = None  # workflow name used as identifier for deep-link

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
                return "❌ BioMate AI engine不可用（API密钥未配置）。请联系管理员。", None
            if resp.status_code == 400:
                return "❌ 请求格式错误，请重试。", None
            if resp.status_code != 200:
                return f"❌ BioMate返回错误 {resp.status_code}，请稍后重试。", None

            current_event = "message"
            for raw_line in resp.iter_lines(decode_unicode=True):
                if not raw_line:
                    continue
                if raw_line.startswith(":"):
                    continue  # SSE comment / heartbeat
                if raw_line.startswith("event:"):
                    current_event = raw_line[6:].strip()
                    continue
                if raw_line.startswith("data:"):
                    data_str = raw_line[5:].strip()
                    try:
                        data = json.loads(data_str)
                    except json.JSONDecodeError:
                        current_event = "message"
                        continue

                    if current_event == "delta" and isinstance(data, dict):
                        text_parts.append(data.get("text", ""))

                    elif current_event == "workflow_ready" and isinstance(data, dict):
                        # Workflow identified — capture its name for the deep-link button.
                        workflow_id = (
                            data.get("workflow_name")
                            or data.get("name")
                            or data.get("chain_display_name")
                        )

                    elif current_event == "final" and isinstance(data, dict):
                        # Fallback: pick workflow name from final if workflow_ready didn't fire.
                        if not workflow_id:
                            wf = data.get("workflow") or {}
                            workflow_id = (
                                wf.get("workflow_ga", {}).get("name")
                                or wf.get("workflow_name")
                                or wf.get("chain_display_name")
                            )

                    elif current_event in ("done", "complete"):
                        break

                    current_event = "message"

    except requests.exceptions.Timeout:
        return "⏱ BioMate响应超时，请稍后重试。", None
    except Exception as exc:
        log.exception(f"BioMate chat stream query failed for user {user_id}: {exc}")
        return f"❌ BioMate查询失败：{exc}", None

    reply_text = "".join(text_parts).strip()
    if not reply_text:
        reply_text = "BioMate正在处理您的请求，请稍后在应用中查看结果。"

    # Persist turn into history
    _push_history(user_id, "user", query)
    _push_history(user_id, "assistant", reply_text)

    return reply_text, workflow_id


# ──────────────────────────────────────────────────────────────────────────────
# Message handler
# ──────────────────────────────────────────────────────────────────────────────

# Simple in-memory binding store: {wechat_user_id: biomate_api_key}
# For production: replace with database persistence
_user_bindings: Dict[str, str] = {}


def handle_wechat_message(xml_body: str, corp_app_id: str) -> str:
    """
    Process an incoming WeChat Work message.
    Returns an XML reply string (or empty string for async handling).

    Handles:
      - "bind <api_key>"   → bind WeChat user to BioMate account
      - "unbind"           → remove binding
      - "clear" / "清除"   → clear conversation history
      - Any other text     → routed through Open Claw engine (full agentic loop)
    """
    msg = parse_message_xml(xml_body)
    if not msg:
        return ""

    msg_type = msg.get("MsgType", "")
    from_user = msg.get("FromUserName", "")
    to_user = msg.get("ToUserName", "")  # The WeChat Work app account
    content = msg.get("Content", "").strip()

    if msg_type != "text" or not content:
        # Only handle text messages; ignore image/voice/event etc.
        return build_text_reply(
            from_user, to_user,
            "BioMate仅支持文字查询。请输入您的分析需求，例如：\n"
            "'对阿司匹林和布洛芬进行ADMET筛选'\n"
            "'全基因组测序变体鉴定'"
        )

    # Bind command
    if content.lower().startswith("bind "):
        api_key = content[5:].strip()
        if api_key:
            _user_bindings[from_user] = api_key
            return build_text_reply(from_user, to_user, "✅ BioMate账号绑定成功！现在您可以直接发送分析请求了。")
        return build_text_reply(from_user, to_user, "❌ 请提供有效的API密钥：bind <your-api-key>")

    # Unbind command
    if content.lower() == "unbind":
        _user_bindings.pop(from_user, None)
        return build_text_reply(from_user, to_user, "✅ 已解除BioMate账号绑定。")

    # Clear conversation history
    if content.lower() in ("clear", "清除", "新对话", "reset"):
        with _history_lock:
            _conversation_history.pop(from_user, None)
        return build_text_reply(from_user, to_user, "✅ 对话历史已清除，开始新对话。")

    # Help command
    if content.lower() in ("help", "帮助", "?", "？"):
        return build_text_reply(from_user, to_user,
            "BioMate 生命科学AI助手 (Open Claw引擎)\n\n"
            "发送分析请求，例如：\n"
            "• 对化合物列表进行ADMET筛选\n"
            "• RNA-seq差异表达分析\n"
            "• 蛋白质结构预测\n\n"
            "支持多轮对话——可直接追问上下文。\n\n"
            "命令：\n"
            "• bind <api-key>  绑定BioMate账号\n"
            "• unbind          解除绑定\n"
            "• clear           清除对话历史\n\n"
            f"打开应用：{BIOMATE_DEEP_LINK_BASE}"
        )

    # Scientific query → Open Claw engine (async, WeChat requires 5s response)
    user_api_key = _user_bindings.get(from_user)

    def _respond_async():
        try:
            reply_text, workflow_id = _open_claw_query(from_user, content, api_key=user_api_key)

            # ── Step 1: send workflow panel card FIRST so the user gets the
            # clickable link as soon as the AI identifies a workflow, before
            # reading the full text response.
            if workflow_id:
                send_workflow_card(
                    from_user,
                    workflow_name=workflow_id.replace("_", " ").title(),
                    description=reply_text[:200],
                    workflow_id=workflow_id,
                )

            # ── Step 2: send the full AI text response
            if len(reply_text) > 1800:
                reply_text = reply_text[:1800] + f"\n\n[查看完整结果请访问 {BIOMATE_DEEP_LINK_BASE}]"
            send_text_message(from_user, reply_text)

        except Exception as exc:
            log.exception("WeChat Open Claw async response failed")
            send_text_message(from_user, f"❌ BioMate查询失败：{exc}")

    threading.Thread(target=_respond_async, daemon=True).start()

    # Immediate reply (WeChat requires response within 5s).
    # Include the BioMate dashboard link so the user can open the panel
    # right away and watch it populate when the run starts.
    return build_text_reply(
        from_user, to_user,
        f"🤖 BioMate正在分析：{content[:60]}…\n\n"
        f"可提前打开应用等待结果：{BIOMATE_DEEP_LINK_BASE}/dashboard"
    )


# ──────────────────────────────────────────────────────────────────────────────
# Flask app (standalone deployment)
# ──────────────────────────────────────────────────────────────────────────────

def create_flask_app():
    """
    Minimal Flask app for WeChat Work webhook.
    Deploy at: https://<domain>/integrations/wechat/message

    GET  /integrations/wechat/message — URL validation (WeChat handshake)
    POST /integrations/wechat/message — incoming message handler
    """
    from flask import Flask, request, make_response

    app = Flask("biomate-wechat")

    @app.route("/integrations/wechat/message", methods=["GET"])
    def wechat_verify():
        """WeChat URL ownership verification (one-time setup)."""
        # SECURITY: Token is mandatory — refuse all requests if not configured
        if not WECHAT_TOKEN:
            log.error("WECHAT_TOKEN not set — refusing WeChat verification request")
            return make_response("WeChat integration not configured", 503)

        msg_signature = request.args.get("msg_signature", "")
        timestamp = request.args.get("timestamp", "")
        nonce = request.args.get("nonce", "")
        echostr = request.args.get("echostr", "")

        if verify_url_signature(WECHAT_TOKEN, timestamp, nonce, msg_signature):
            return echostr  # Echo back to confirm ownership
        log.warning(f"WeChat URL verification failed: sig={msg_signature}, ts={timestamp}")
        return make_response("Signature verification failed", 403)

    @app.route("/integrations/wechat/message", methods=["POST"])
    def wechat_message():
        """Handle incoming WeChat Work messages."""
        # SECURITY: Token is mandatory — all POST messages must be verified
        if not WECHAT_TOKEN:
            log.error("WECHAT_TOKEN not set — refusing WeChat message")
            return make_response("WeChat integration not configured", 503)

        timestamp = request.args.get("timestamp", "")
        nonce = request.args.get("nonce", "")
        msg_signature = request.args.get("msg_signature", "")

        # Validate required security parameters
        if not timestamp or not nonce or not msg_signature:
            log.warning("WeChat message missing required security params")
            return make_response("Missing security parameters", 400)

        xml_body = request.get_data(as_text=True)
        if not xml_body:
            return make_response("Empty body", 400)

        # Verify HMAC-SHA1 signature — mandatory, no bypass
        if not verify_url_signature(WECHAT_TOKEN, timestamp, nonce, msg_signature):
            log.warning(f"WeChat signature verification failed from {request.remote_addr}")
            return make_response("Signature error", 403)

        reply_xml = handle_wechat_message(xml_body, WECHAT_CORP_ID)
        if reply_xml:
            return make_response(reply_xml, 200, {"Content-Type": "application/xml"})
        return "success"  # WeChat requires "success" for no-reply messages

    @app.route("/integrations/wechat/health", methods=["GET"])
    def health():
        from flask import jsonify
        return jsonify({"status": "ok", "service": "biomate-wechat"})

    return app


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="BioMate WeChat Work Bot")
    parser.add_argument("--port", type=int, default=8091)
    args = parser.parse_args()
    app = create_flask_app()
    log.warning(f"BioMate WeChat bot listening on port {args.port}")
    app.run(host="0.0.0.0", port=args.port)
