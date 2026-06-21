# Deploying the BioMate Feishu/Lark connector (self-host)

This is a **self-hosted** connector: a company with a BioMate account runs this
bot on its own host to wire **its own Feishu/Lark tenant** to BioMate. The bot
is a small Flask app; the bundled `docker-compose.yml` runs it behind Caddy with
automatic HTTPS.

```
Feishu tenant ──webhook──▶ your host (Caddy :443) ──▶ feishu-bot :8093 ──▶ BioMate API
                                          ▲                                   │
                       card "Open in BioMate" button (/go) ───auto-login──────┘
```

## Prerequisites

- A host (VM/EC2/etc.) with Docker + Docker Compose, ports **80** and **443**
  open to the internet.
- A DNS **A record** pointing a name (e.g. `connect.yourcompany.com`) at the host.
- A BioMate account + the backend URL you connect to (e.g.
  `https://test.stage-public.biomate.ai`).
- A Feishu/Lark **custom app** you can admin.

## 1. Create the Feishu app

In the Feishu open platform (https://open.feishu.cn/, Lark:
https://open.larksuite.com/):

1. Create a **custom app** → note **App ID**, **App Secret**, **Verification Token**.
2. Enable the **Bot** capability.
3. Permissions: add `im:message`, `im:message:send_as_bot` (and `im:chat:readonly`
   if you want the bot to list chats).
4. **Disable the Encrypt Key** (this connector does not implement encrypt-mode
   decryption).
5. Publish a version (permission changes need a published, admin-approved version).

You'll set the event request URL in step 4 below, after the host is up.

## 2. Configure + launch

```bash
cd connectors/feishu
cp .env.example .env
# edit .env: FEISHU_*; BIOMATE_API_URL + BIOMATE_DEEP_LINK_BASE;
#            CONNECTOR_PUBLIC_URL=https://connect.yourcompany.com
#            CONNECTOR_DOMAIN=connect.yourcompany.com
docker compose up -d --build
```

Caddy fetches a Let's Encrypt cert for `CONNECTOR_DOMAIN` on first boot (needs the
DNS record + ports 80/443). Verify:

```bash
curl -fsS https://connect.yourcompany.com/connect/feishu/health
# {"status":"ok","service":"biomate-feishu"}
```

## 3. Point the magic auto-login at this host

`CONNECTOR_PUBLIC_URL` in `.env` **must** equal your public HTTPS base
(`https://connect.yourcompany.com`). The "Open in BioMate" card button links to
`…/connect/feishu/go`, which mints a **fresh** one-time login token **at click
time** and 302s the user into BioMate already logged-in. Minting at click time
(not when the card is sent) is what makes the single-use token survive IM link
previews and slow clicks. The `/go` link is HMAC-signed and self-expires, so it
can't be forged for another user.

> If `CONNECTOR_PUBLIC_URL` is blank, buttons fall back to a bare deep link and
> users may land on the login page. Set it.

## 4. Register the Feishu event subscription

In the app console → **Event Subscription**:

- Request URL: `https://connect.yourcompany.com/connect/feishu/webhook`
- Subscribe to **`im.message.receive_v1`**.

On save, Feishu sends a `url_verification` challenge; the bot echoes it
automatically, so the URL saves green.

## 5. Use it

In Feishu, DM the bot (or @-mention it in a group) and bind once:

```
bind <your-biomate-api-key>
```

Then ask anything: `筛选 aspirin 的 hERG 抑制`, `RNA-seq differential expression …`.
The bot replies with the analysis and, when a workflow is generated, a card whose
button logs you straight into BioMate.

## Operations

- **Logs:** `docker compose logs -f feishu-bot`
- **Update:** `git pull && docker compose up -d --build`
- **Env vars:** see [`.env.example`](./.env.example) for the full list.

## Known limitations

- **Bindings + history are in-memory** (per the bot's current design): a restart
  drops who-is-bound and conversation context (users just `bind` again). The
  Docker image runs **one** gunicorn worker so this in-memory state stays
  coherent. To scale to multiple workers/replicas, move `_user_bindings` (and the
  history/dedup stores) to Redis/DB first — otherwise binds made on one worker are
  invisible to another.
- **Encrypt mode is not implemented** — keep the app's Encrypt Key disabled.
- **`view_url`:** the card currently auto-logs the user into the app home. Once
  BioMate's `/api/chat/stream` emits a per-workflow `view_url`, the same button
  lands them on the specific workflow automatically — no connector change needed.

## Security notes

- No long-lived secret is ever placed in a URL: the card links to the bot's `/go`
  route (HMAC-signed, expiring); the actual BioMate login token is one-time,
  ~5-min, minted server-side at click time.
- Keep `.env` off version control (the repo `.gitignore` already excludes it).
- Set a dedicated `CONNECTOR_SIGNING_SECRET` in production.
