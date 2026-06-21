# BioMate × Feishu / Lark

> Drive real bioinformatics pipelines from Feishu (飞书) / Lark — RNA-seq, CryoSPARC, ADMET, PBPK, AlphaFold — without leaving the chat.

中文用户请见 [README.zh-CN.md](./README.zh-CN.md).

## Setup

### 1. Create an app

Open the Feishu/Lark developer console:

- Feishu (China): https://open.feishu.cn/
- Lark (international): https://open.larksuite.com/

Create a **custom app**, then:

1. **Add bot capability** (Features → Bot).
2. **Permissions / scopes**: add `im:message` and `im:message:send_as_bot`.
3. **Event Subscriptions**: set the request URL to
   `https://<your-domain>/connect/feishu/webhook` and subscribe to
   **`im.message.receive_v1`**.
4. Copy the app's **App ID**, **App Secret**, and the **Verification Token**.

> **Encrypt Key:** this connector does **not** implement encrypt-mode
> decryption. Leave the Encrypt Key **disabled**, or add a
> WBizMsgCrypt-equivalent decrypt step before `handle_event`.

### 2. Configure environment

```bash
export FEISHU_APP_ID="cli_..."
export FEISHU_APP_SECRET="..."
export FEISHU_VERIFY_TOKEN="..."
export FEISHU_BASE="https://open.feishu.cn"          # Lark: https://open.larksuite.com
export BIOMATE_API_URL="https://api.biomate.ai"
export BIOMATE_API_KEY="sk-..."                       # optional service-account fallback
export BIOMATE_DEEP_LINK_BASE="https://app.biomate.ai"
```

### 3. Run the bot

```bash
python connectors/feishu/feishu_bot.py --port 8093
```

Put it behind a public HTTPS URL. When you save the event request URL in the
console, Feishu sends a `url_verification` challenge — the bot echoes it
automatically.

## Use it

In Feishu, DM the bot (or @-mention it in a group) and bind your account once:

```
bind sk-your-biomate-api-key
```

Then just ask:

```
Screen aspirin and caffeine for hERG and CYP3A4 inhibition
```

```
RNA-seq differential expression, treated vs control, GRCh38
```

```
查询 UniProt P04637
```

The bot replies with a text summary and, when a runnable workflow is identified,
an interactive card with a **Run in BioMate** button.

## Commands

| Command | What it does |
|---|---|
| `help` / `帮助` | Show help |
| `bind <api-key>` | Link your BioMate account |
| `unbind` | Remove the binding |
| `clear` / `清除` | Clear conversation history |

## Environment variables

| Var | Required | Default | Notes |
|---|---|---|---|
| `FEISHU_APP_ID` | yes | — | App ID from the console |
| `FEISHU_APP_SECRET` | yes | — | App Secret |
| `FEISHU_VERIFY_TOKEN` | yes | — | Verification Token; checked on inbound events |
| `FEISHU_BASE` | no | `https://open.feishu.cn` | Use `https://open.larksuite.com` for Lark |
| `BIOMATE_API_URL` | yes | `http://localhost:5000` | BioMate API base |
| `BIOMATE_API_KEY` | no | — | Service-account fallback if a user hasn't bound |
| `BIOMATE_DEEP_LINK_BASE` | no | `https://app.biomate.ai` | Used for the Run button |

## Notes

- Feishu retries events on non-2xx, so the webhook returns 200 immediately and
  replies asynchronously; duplicate `message_id`s are de-duplicated.
- The binding store is in-memory (matches the reference connectors); replace
  with a persistent store for production.

## License

MIT.
