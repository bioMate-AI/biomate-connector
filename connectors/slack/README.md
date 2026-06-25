# BioMate × Slack

> Run real bioinformatics from your lab's Slack workspace. `/biomate <request>`
> kicks off a BioMate workflow; the bot replies with the result and a
> **Run in BioMate** button that deep-links into the web panel.

```
Slack workspace ──slash command──▶ your host (Caddy :443)
                                        │  /integrations/slack/command
                                        ▼
                                   slack-bot :8090 ──▶ BioMate API (/api/chat/stream)
```

**Auth model — read this first.** The Slack adapter is **single-tenant**: every
request authenticates with one shared `BIOMATE_API_KEY`. There is **no per-user
`/biomate login`** — all workflows run under the account that owns that key.
(Per-user OAuth is a possible future enhancement, not current behaviour.)

---

## Step 1 — Create the Slack app (browser, ~3 min)

The thing you create here is a **Slack app** (a bot integration), not the Slack
desktop client. Do it once, in a browser.

1. Open **https://api.slack.com/apps** and sign in to your workspace.
2. **Create New App → From an app manifest**.
3. Pick your workspace → **Next**.
4. Paste this manifest (replace `YOUR_DOMAIN` with the public HTTPS host you'll
   deploy the bot on — see Step 2):

   ```yaml
   display_information:
     name: BioMate
   features:
     bot_user:
       display_name: BioMate
       always_online: true
     slash_commands:
       - command: /biomate
         url: https://YOUR_DOMAIN/integrations/slack/command
         description: Run BioMate scientific analyses
         usage_hint: Screen aspirin for hERG
   oauth_config:
     scopes:
       bot:
         - commands
         - chat:write
         - files:write
   settings:
     org_deploy_enabled: false
     socket_mode_enabled: false
     token_rotation_enabled: false
   ```

5. **Next → Create**.
6. Left menu **Install App → Install to Workspace → Allow**.
7. Copy the two secrets you'll give the bot:
   - **OAuth & Permissions** → **Bot User OAuth Token** (`xoxb-…`) → `SLACK_BOT_TOKEN`
   - **Basic Information** → **Signing Secret** → `SLACK_SIGNING_SECRET`

You also need a **`BIOMATE_API_KEY`** — generate one in the BioMate web app
(log in → account settings → API Keys). This is the shared key all Slack
queries run under.

---

## Step 2 — Deploy the bot

The bot is a small Flask app serving two routes:

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/integrations/slack/command` | the `/biomate` slash command (Slack-signature verified) |
| `GET`  | `/integrations/slack/health` | health probe |

Slack requires the slash-command Request URL to be **HTTPS with a trusted
cert**. Pick whichever fits your host.

### Option A — Standalone Docker + Caddy (you have a domain)

Caddy fetches & renews a Let's Encrypt cert automatically.

```bash
cd connectors/slack
cp .env.example .env          # fill in SLACK_*, BIOMATE_API_KEY, CONNECTOR_DOMAIN
docker compose up -d --build
```

Prereqs: a DNS **A record** for `CONNECTOR_DOMAIN` → your host's public IP, and
inbound **80 + 443** open. Request URL → `https://<CONNECTOR_DOMAIN>/integrations/slack/command`.

### Option B — Co-locate behind an existing Caddy (recommended if one is already running)

This is how it's deployed on the BioMate test host, sharing the Caddy that
already serves the frontend and the Feishu bot. No new domain or cert needed —
add one path route to the existing `Caddyfile`:

```caddyfile
your-existing-domain {
    encode gzip
    handle /connect/feishu/* { reverse_proxy localhost:8093 }
    handle /integrations/slack/* { reverse_proxy localhost:8090 }   # ← add this
    handle { reverse_proxy localhost:3000 }
}
```

Then run the bot container and reload Caddy:

```bash
docker build -t biomate-slack-bot:latest connectors/slack
docker run -d --name biomate-slack --restart unless-stopped \
  -p 127.0.0.1:8090:8090 --env-file /path/to/slack.env biomate-slack-bot:latest
docker exec <caddy-container> caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile
```

Request URL → `https://your-existing-domain/integrations/slack/command`.

### `slack.env` contents (both options)

```bash
SLACK_BOT_TOKEN=xoxb-…
SLACK_SIGNING_SECRET=…
BIOMATE_API_URL=https://test.stage-public.biomate.ai   # the BioMate backend to drive
BIOMATE_API_KEY=…                                       # shared service key (Step 1)
BIOMATE_DEEP_LINK_BASE=https://test.stage-public.biomate.ai
```

---

## Step 3 — Wire the Request URL & smoke-test

1. Back in the Slack app, **Slash Commands → /biomate**, confirm the Request URL
   matches your deployed host (the manifest already set it).
2. Smoke-test the public endpoint:

   ```bash
   curl https://<your-host>/integrations/slack/health      # → {"status":"ok","service":"biomate-slack"}
   ```

   A wrong/absent `X-Slack-Signature`, or a timestamp older than 5 min, returns
   **403** (replay protection).

---

## Step 4 — Use it in Slack

In any channel where the app is present, or in a DM to the bot:

```
/biomate help
```
```
/biomate Screen aspirin CC(=O)Oc1ccccc1C(=O)O for hERG and CYP3A4
```
```
/biomate RNA-seq differential expression on s3://biomate-demo/rnaseq/, treated vs control, GRCh38
```

The bot returns an **immediate ack** within 3 s, then posts the real result
asynchronously: a text summary plus a **Run in BioMate** button that opens the
workflow in the web panel, pre-loaded and ready to **Confirm and run**.

### Subcommands

| Command | What it does |
|---|---|
| `/biomate <any request>` | agentic BioMate session (`biomate_session`) — the main path |
| `/biomate help` | usage help |
| `/biomate clear` (or `reset`) | wipe this user's conversation history |

There is no `login`/`logout`/`bind` — see the auth model at the top.

---

## Operations

```bash
docker logs -f biomate-slack                 # tail bot logs
docker restart biomate-slack                 # restart (keeps current env)
```

**Rotating `BIOMATE_API_KEY` (or any env):** `docker restart` does **not**
re-read `--env-file`. Edit `slack.env`, then recreate the container:

```bash
docker rm -f biomate-slack
docker run -d --name biomate-slack --restart unless-stopped \
  -p 127.0.0.1:8090:8090 --env-file /path/to/slack.env biomate-slack-bot:latest
```

**Scaling:** the image runs **1 gunicorn worker** (8 threads) on purpose —
per-user conversation history lives in process memory. To run multiple
workers/replicas, move `_conversation_history` to a shared store (Redis/DB) first.

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `/biomate` command doesn't appear in Slack | App not installed, or slash command not saved. Re-check Step 1.6 and the Request URL. |
| Bot replies but text is garbled (`â`, `â ï¸`) | UTF-8 mojibake — fixed in this repo (`resp.encoding = "utf-8"`). Rebuild the image. |
| `403` on every command | Signing secret mismatch, or the host clock is >5 min off. Verify `SLACK_SIGNING_SECRET`. |
| Ack appears, then "workflow engine error / no workflow found" | Backend issue (e.g. empty workflow catalog), **not** the connector. Check the BioMate backend. |
| `:x: BioMate AI engine unavailable` | `BIOMATE_API_KEY` missing/invalid, or `BIOMATE_API_URL` unreachable. |

## License

MIT.
