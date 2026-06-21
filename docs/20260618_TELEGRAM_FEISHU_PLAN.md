# Plan: Add Telegram + Feishu/Lark connectors to biomate-connector

**Date:** 2026-06-18
**Repo:** `biomate-connector` (this repo — the single source of truth for all BioMate third-party integrations)
**Status:** planned (not started)

## Goal

Let users drive BioMate from **Telegram** and **Feishu/Lark**, the two chat
surfaces still missing from this repo. Every other surface (Claude Code/Desktop,
Cursor, Codex, ChatGPT, Coze, Slack, WeChat) already exists; Telegram + Feishu
close the gap from the original ask ("各家平台通过各自的 Skill 调用 BioMate,
包括 telegram、飞书").

Both are IM-bot connectors and must follow the **existing WeChat/Slack pattern**
exactly — do not invent a new architecture.

## Reference implementations (read these first)

- `connectors/wechat/wechat_bot.py` — the canonical IM-bot template:
  - Flask app with a webhook route
  - `_open_claw_query(user_id, message, api_key=..., _base_url_override=...)`
    → POSTs to `{BIOMATE_API_URL}/api/chat/stream`, parses the SSE stream
    (events: `delta` / `tool_event` / `done`), returns `(reply_text, workflow_id)`
  - Account binding: user sends `bind <biomate_api_key>` → stored per IM user_id
  - Replies with text + a workflow card carrying a deep link
    (`{BIOMATE_DEEP_LINK_BASE}?workflow=<id>`)
  - Env: `BIOMATE_API_URL`, `BIOMATE_API_KEY`, `BIOMATE_DEEP_LINK_BASE`
- `connectors/slack/slack_bot.py` — second IM reference (signature verification).
- `mcp/tools_manifest.py` (+ `mcp/tools_manifest.json`) — the 14-tool single
  source of truth. IM bots route NL through `/api/chat/stream` (the
  `biomate_session` path), so they do not redefine tool schemas — keep it that way.
- `tests/test_wechat_open_claw.py`, `tests/test_slack_bot.py` — test style:
  mock `requests`/SSE, no real network, no real BioMate calls.

## Conventions to match (hard requirements)

- **Reuse the `_open_claw_query` SSE-parsing pattern** from `wechat_bot.py`
  (copy its shape; each bot keeps its own copy as WeChat/Slack do — there is no
  shared helper module today).
- **`bind <api_key>` account binding** per IM user (in-memory dict is acceptable
  for the bot, matching wechat_bot.py; note it as replace-with-store for prod).
- **Standalone Flask app** + a `create_flask_app()` factory + `if __name__ ==
  "__main__"` runner with `--port`, mirroring wechat_bot.py.
- **No OAuth** for IM bots (they use `bind <api_key>`, same as WeChat). The
  `oauth-server/` is for ChatGPT/MCP browser surfaces only.
- Sanitized language: this repo has had internal infra names (Galaxy / Nextflow /
  nf-core / AWS Batch) scrubbed (commit 5130f08) — keep docs surface-level
  ("BioMate cloud", "2,455 indexed workflows"), do NOT reintroduce internal names.
- Bilingual READMEs (`README.md` + `README.zh-CN.md`), matching wechat/.

## Work items

### 1. `connectors/telegram/`
- `telegram_bot.py`:
  - `POST /connect/telegram/webhook` — parse Telegram update (`message.text`,
    `message.chat.id`); `/start`,`/help`,`/bind <key>`; else `_open_claw_query`
    → reply via `https://api.telegram.org/bot<token>/sendMessage`
  - 4096-char truncation (Telegram limit); workflow card as a follow-up message
    with the deep link
  - Env: `TELEGRAM_BOT_TOKEN` + the shared `BIOMATE_*`
  - `create_flask_app()` + `__main__` runner (default port e.g. 8092)
- `README.md` + `README.zh-CN.md`: BotFather setup, `setWebhook` curl, env vars,
  example prompts.

### 2. `connectors/feishu/`
- `feishu_bot.py`:
  - `POST /connect/feishu/webhook` — handle `type=url_verification` challenge
    echo; handle `im.message.receive_v1` events; dedup by `message_id`
    (Feishu retries on non-2xx); parse text, strip `@_user_*` mentions
  - `tenant_access_token` fetch+cache (from `FEISHU_APP_ID`/`FEISHU_APP_SECRET`)
  - reply via `{FEISHU_BASE}/open-apis/im/v1/messages` (`receive_id_type=chat_id`)
  - async reply thread so the webhook returns 200 fast
  - Env: `FEISHU_APP_ID`, `FEISHU_APP_SECRET`, `FEISHU_VERIFY_TOKEN`,
    `FEISHU_BASE` (default `https://open.feishu.cn`; Lark = `https://open.larksuite.com`)
  - `create_flask_app()` + `__main__` runner (default port e.g. 8093)
- `README.md` + `README.zh-CN.md`: Feishu open-platform app setup, event
  subscription URL, env vars, examples. Note: encrypt-mode decryption is NOT
  implemented — disable Encrypt Key or add WBizMsgCrypt-equivalent.

### 3. Tests
- `tests/test_telegram_bot.py`, `tests/test_feishu_bot.py` — mirror
  `tests/test_wechat_open_claw.py`: mock the SSE stream + outbound send; assert
  bind flow, url_verification (Feishu), text reply, workflow-card/deep-link,
  truncation. No real network.

### 4. Docs / wiring
- `connectors/README.md`: add Telegram + Feishu rows to the surface table; update
  the architecture diagram's IM list.
- root `README.md`: add to "AI tools you already use" list + the surface table.
- If the `@biomate/connect` CLI (`connectors/installer/`) enumerates surfaces,
  register `telegram` and `feishu` (check how `open-claw`/`slack` are listed).

## Acceptance

- `python -m pytest tests/test_telegram_bot.py tests/test_feishu_bot.py` green.
- Each bot's `create_flask_app()` imports and a webhook POST dispatches to a
  mocked `_open_claw_query` and returns 200.
- READMEs let a user wire the webhook end-to-end.
- Live check (optional, when a BioMate token + the bot tokens exist): point
  `BIOMATE_API_URL` at a running BioMate, send a real message, confirm a reply.

## Notes / context for the executing session

- Original mistake: an earlier session built this in the **wrong repo**
  (`biomate` main, `backend/lib/connect/`). That commit was reverted; do all work
  HERE in `biomate-connector`.
- IM bots talk to BioMate via `/api/chat/stream` (the `biomate_session` engine),
  not the workflow DB directly — so no DB-path concerns here.
- Keep each new connector self-contained under `connectors/<name>/` like wechat/.
