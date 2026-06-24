# Deploying the BioMate Slack connector (EC2)

The Slack adapter is a small Flask app (`slack_bot.py`) exposing:

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/integrations/slack/command` | `/biomate` slash-command handler (Slack-signature verified) |
| `GET`  | `/integrations/slack/health` | health probe |

Slack requires the slash-command **Request URL to be HTTPS with a publicly-trusted
cert**. Two ways to get that on an EC2 box — pick one.

---

## Option A — Docker + Caddy (you have a domain)

Best for a stable deployment. Caddy fetches & renews a Let's Encrypt cert automatically.

```bash
cd connectors/slack
cp .env.example .env          # fill in SLACK_*, BIOMATE_*, CONNECTOR_DOMAIN
docker compose up -d --build
```

Prereqs:
- A DNS **A record** for `CONNECTOR_DOMAIN` pointing at the EC2 public IP.
- EC2 **security group** inbound: TCP **80** and **443** open to the world (Let's
  Encrypt HTTP-01 challenge + serving).

Slash-command Request URL → `https://<CONNECTOR_DOMAIN>/integrations/slack/command`

---

## Option B — ngrok (no domain, fastest for a test run)

```bash
cd <repo root>
pip install -r connectors/slack/requirements.txt
set -a; source connectors/slack/.env; set +a     # or export the vars manually
PYTHONPATH=. python -m connectors.slack.slack_bot --port 8090 &
ngrok http 8090
```

ngrok prints `https://xxxx.ngrok-free.app`. No inbound security-group ports needed
(ngrok is an outbound tunnel).

Slash-command Request URL → `https://xxxx.ngrok-free.app/integrations/slack/command`

---

## Slack app configuration (both options)

At api.slack.com/apps → your app:

1. **Slash Commands** → Create `/biomate` → Request URL = the HTTPS URL above.
2. **OAuth & Permissions** → Bot Token Scopes: `commands`, `chat:write`, `files:write`.
3. **Install to Workspace** → copy the Bot User OAuth Token (`xoxb-…`) into `.env`.
4. **Basic Information** → copy the Signing Secret into `.env`.

> **Auth model:** single-tenant. There is no per-user `/biomate login`; every
> request authenticates with the shared `BIOMATE_API_KEY`. Only `help` and
> `clear` subcommands exist besides scientific queries.

---

## Smoke test before using Slack

```bash
curl https://<your-url>/integrations/slack/health      # → {"status":"ok","service":"biomate-slack"}
```

A wrong/absent `X-Slack-Signature`, or a timestamp older than 5 min, returns **403**
(replay protection). Verified by `tests/test_slack_bot.py::TestVerifySlackSignature`.

## Scaling note

The image runs **1 gunicorn worker** with 8 threads on purpose: per-user
conversation history lives in process memory, so a follow-up landing on a
different worker would lose context. To run multiple workers/replicas, move
`_conversation_history` to a shared store (Redis/DB) first.

## Then run the L5 checklist

See **Surface 7** in `docs/20260621_TEST_PLAN.md` for the tap-through scenarios.
