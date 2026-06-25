# BioMate × Feishu / Lark

> Drive real bioinformatics pipelines from Feishu (飞书) / Lark — RNA-seq, CryoSPARC, ADMET, PBPK, AlphaFold — without leaving the chat.

中文用户请见 [README.zh-CN.md](./README.zh-CN.md). For the Docker/Caddy self-host
package see [DEPLOY.md](./DEPLOY.md).

```
Feishu tenant ──webhook──▶ your host (Caddy :443)
                               │  /connect/feishu/webhook
                               ▼
                          feishu-bot :8093 ──▶ BioMate API (/api/chat/stream)
                               ▲
   card "Open in BioMate" (/connect/feishu/go) ──click-time auto-login──┘
```

**Auth model.** Per-user: each Feishu user runs `bind <api-key>` once to link
their BioMate account. An optional shared `BIOMATE_API_KEY` acts as a fallback
for users who haven't bound. The card's **Open in BioMate** button mints a
one-time login token at click time, so the user lands in BioMate already
logged-in on the exact workflow page.

---

## Step 1 — Create the Feishu/Lark app (browser, ~5 min)

Open the developer console:
- Feishu (China): **https://open.feishu.cn/**
- Lark (international): **https://open.larksuite.com/**

Create a **custom app**, then:

1. **Add bot capability** (Features → Bot).
2. **Permissions / scopes**: add `im:message` and `im:message:send_as_bot`.
3. **Event Subscriptions** → Request URL =
   `https://<your-domain>/connect/feishu/webhook`, and subscribe to
   **`im.message.receive_v1`**. (When you save, Feishu sends a `url_verification`
   challenge — the bot echoes it automatically, so deploy Step 2 first or save
   after the bot is up.)
4. Copy the **App ID**, **App Secret**, and **Verification Token**.

> **Encrypt Key:** this connector does **not** implement encrypt-mode
> decryption. Leave the Encrypt Key **disabled** in the console.

---

## Step 2 — Deploy the bot

The bot is a small Flask app serving three routes:

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/connect/feishu/webhook` | event subscription endpoint (verification challenge + messages) |
| `GET`  | `/connect/feishu/go` | click-time auto-login redirect for the card button |
| `GET`  | `/connect/feishu/health` | health probe |

Feishu requires the webhook URL to be **HTTPS with a trusted cert**.

### Option A — Standalone Docker + Caddy (you have a domain)

Bundled package; Caddy fetches & renews a Let's Encrypt cert automatically.

```bash
cd connectors/feishu
cp .env.example .env          # fill in FEISHU_*, BIOMATE_*, CONNECTOR_DOMAIN
docker compose up -d --build
```

Prereqs: a DNS **A record** for `CONNECTOR_DOMAIN` → host IP, inbound **80 + 443**
open. Full details in [DEPLOY.md](./DEPLOY.md).

### Option B — Co-locate behind an existing Caddy

How it runs on the BioMate test host, sharing the Caddy that serves the
frontend and Slack bot. Add one path route to the existing `Caddyfile`:

```caddyfile
your-existing-domain {
    encode gzip
    handle /connect/feishu/* { reverse_proxy localhost:8093 }       # ← add this
    handle /integrations/slack/* { reverse_proxy localhost:8090 }
    handle { reverse_proxy localhost:3000 }
}
```

Then run the bot container and reload Caddy:

```bash
docker build -t biomate-feishu-bot:latest connectors/feishu
docker run -d --name biomate-feishu --restart unless-stopped \
  -p 127.0.0.1:8093:8093 --env-file /path/to/feishu.env biomate-feishu-bot:latest
docker exec <caddy-container> caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile
```

### Run without Docker (dev)

```bash
set -a; source connectors/feishu/.env; set +a
python connectors/feishu/feishu_bot.py --port 8093
```

### `feishu.env` contents

```bash
FEISHU_APP_ID=cli_…
FEISHU_APP_SECRET=…
FEISHU_VERIFY_TOKEN=…
FEISHU_BASE=https://open.feishu.cn          # Lark: https://open.larksuite.com
BIOMATE_API_URL=https://test.stage-public.biomate.ai
BIOMATE_DEEP_LINK_BASE=https://test.stage-public.biomate.ai
CONNECTOR_PUBLIC_URL=https://<your-domain>  # enables the signed /go auto-login button
CONNECTOR_SIGNING_SECRET=…                  # recommended; falls back to FEISHU_APP_SECRET
BIOMATE_API_KEY=                            # optional shared fallback for unbound users
```

---

## Step 3 — Save the webhook URL & smoke-test

1. With the bot running, save the Event Request URL in the Feishu console —
   the `url_verification` challenge should pass immediately.
2. Smoke-test:

   ```bash
   curl https://<your-domain>/connect/feishu/health     # → {"status":"ok","service":"biomate-feishu"}
   ```

---

## Step 4 — Use it in Feishu

DM the bot (or @-mention it in a group). Bind your account once:

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
an interactive card with an **Open in BioMate** button — click it to land in the
web panel, already logged in, on that workflow.

### Commands

| Command | What it does |
|---|---|
| `bind <api-key>` | link your BioMate account |
| `unbind` | remove the binding (falls back to shared key if set) |
| `help` / `帮助` | show help |
| `clear` / `清除` | clear conversation history |

---

## Operations

```bash
docker logs -f biomate-feishu       # tail logs
docker restart biomate-feishu       # restart (keeps current env)
```

To change env (rotate a key), edit `feishu.env` then **recreate** the container
(`docker rm -f` + `docker run …`) — `docker restart` does not re-read `--env-file`.

- **Event dedup:** Feishu retries on non-2xx, so the webhook returns 200
  immediately and replies asynchronously; duplicate `message_id`s are dropped.
- **In-memory state:** bindings + conversation history live in process memory;
  a restart drops them (users just `bind` again). Use Redis for multi-worker.

---

## Environment variables

| Var | Required | Default | Notes |
|---|---|---|---|
| `FEISHU_APP_ID` | yes | — | App ID from the console |
| `FEISHU_APP_SECRET` | yes | — | App Secret; also HMAC fallback for `/go` |
| `FEISHU_VERIFY_TOKEN` | yes | — | Verification Token; checked on inbound events |
| `FEISHU_BASE` | no | `https://open.feishu.cn` | Use `https://open.larksuite.com` for Lark |
| `BIOMATE_API_URL` | yes | `http://localhost:5000` | BioMate API base |
| `BIOMATE_DEEP_LINK_BASE` | yes | `https://app.biomate.ai` | App root for fallback links |
| `CONNECTOR_PUBLIC_URL` | no | — | This bot's HTTPS base; enables the signed `/go` button |
| `CONNECTOR_SIGNING_SECRET` | no | `FEISHU_APP_SECRET` | Dedicated HMAC key for `/go` link signing |
| `BIOMATE_API_KEY` | no | — | Shared service-account fallback if a user hasn't bound |

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Saving the webhook URL fails verification | Bot not reachable over HTTPS yet, or wrong `FEISHU_VERIFY_TOKEN`. Deploy Step 2 first. |
| Replies garbled (`â`, CJK mojibake) | UTF-8 issue — fixed in this repo (`resp.encoding = "utf-8"`). Rebuild the image. |
| Bot silent on group messages | @-mention it, and confirm `im.message.receive_v1` is subscribed. |
| **Open in BioMate** button 404s / re-prompts login | `CONNECTOR_PUBLIC_URL` unset, or user not bound. |
| Ack appears, then "no workflow found" | Backend issue (e.g. empty workflow catalog), **not** the connector. |

## License

MIT.
