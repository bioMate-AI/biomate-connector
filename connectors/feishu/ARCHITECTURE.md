# Feishu connector — working architecture & hard-won lessons

This documents the design that actually got Feishu ↔ BioMate working end-to-end
(real conversation → workflow card → click → auto-login → review params → run),
and the non-obvious gotchas solved along the way. Read this before changing the
auth/deep-link/deploy plumbing — most of it is load-bearing.

## End-to-end flow (what a user experiences)

```
Feishu user sends a message
  → Feishu pushes im.message.receive_v1 → bot /connect/feishu/webhook
  → bot routes to BioMate /api/chat/stream (under the user's linked token)
  → bot replies with text + (when a runnable workflow is generated) a card
  → user taps the card button
  → /connect/feishu/go mints a FRESH one-time login token → /api/auth/magic
  → browser lands logged-in on /?session=<id> = the same chat + generated workflow
  → user reviews parameters in the panel, then Confirm & Run
```

First-time account linking (no token pasting):

```
unbound user sends anything
  → bot replies with a "Link account" card
  → button → https://<host>/connect/feishu/link?u=<open_id>&exp&sig   (FULL page load)
  → same domain as BioMate ⇒ browser sends the biomate_token cookie
  → bot verifies the signed open_id + that the token works, then binds. Done.
```

## Why these specific choices (the lessons)

1. **Deploy on the SAME public host/domain as BioMate, path-routed by the
   existing reverse proxy.** The bot lives at
   `https://test.stage-public.biomate.ai/connect/feishu/*` via a Caddy
   `handle /connect/feishu/*` block → `localhost:8093`. This single decision
   makes the cookie-based account linking and the magic deep-link work, because
   the bot and BioMate are same-origin. (Caddy serves the public domain on the
   instance's public IP; the same name resolves to a private IP over the VPN —
   verify the *public* view with DoH, e.g. `https://dns.google/resolve?...`,
   because the VPN's resolver hijacks even `dig @8.8.8.8`.)

2. **`/api/chat/stream` does not emit `view_url` and chat-generated workflows
   have no per-id route.** So to deep-link the user back to *their* generated
   workflow, the bot passes a per-user `context.sessionId` to `/api/chat/stream`
   (the backend persists the conversation + workflow under that session for the
   bound user), and the card button targets `/?session=<id>` — which the SPA
   already loads (App.tsx `?session=` handler). **Zero backend change.**

3. **Auto-login token must be minted at CLICK time, not when the card is sent.**
   The one-time login token is single-use and ~5-min. Baking it into the card
   meant IM link-previews or slow clicks burned/expired it (→ `/login?error=
   invalid_or_expired`). The `/connect/feishu/go` redirect mints a fresh token on
   each real click. The `/go` URL is HMAC-signed (open_id + redirect + expiry) so
   it can't be forged for another user and it self-expires.

4. **Account linking via same-domain cookie, NOT `bind <token>`.** Pasting a JWT
   is not a product. After the user logs into BioMate, the browser auto-sends the
   `biomate_token` cookie to the bot's same-domain `/connect/feishu/link`
   endpoint (HttpOnly only blocks JS reads, not the browser sending it
   server-side). The bot reads it and binds.
   - **Do NOT route linking through `/login?next=/connect/feishu/link`.** The SPA
     client-routes `next` (wouter `setLocation`) and renders its own 404 for this
     server-only path. The link must be a **direct full-page load** to
     `/connect/feishu/link`; the not-logged-in case shows a "log in then tap
     again" page.

5. **Plan A (review-in-panel) over Plan B (auto-run).** Auto-running a workflow
   from one chat line is wrong: real workflows need input/parameter review
   (`datasets_assigned: false`), and silently launching spends compute without
   consent. The card hands off to the web panel where the user reviews params and
   clicks Run. (`/api/workflows/execute` also currently 500s on staging.)

## Deployment gotchas

- **`--env-file` does not strip quotes.** `FEISHU_BASE="https://open.feishu.cn"`
  in an env file passed to `docker run --env-file` keeps the literal quotes →
  `"https://open.feishu.cn"/open-apis/...` → requests "No connection adapters".
  Write env values **without** surrounding quotes.
- **Feishu event subscription must be "send to request URL" mode, not the
  long-connection/WebSocket mode.** In long-connection mode the request URL
  verifies fine but receives **zero** events (one delivery channel per app).
- **Receiving messages needs the right scopes published**: `im:message.p2p_msg`
  (DMs) and `im:message.group_at_msg` (group @), plus a published version.
- **Encrypt Key must be disabled** (this bot doesn't implement decryption).
- The container runs **one** worker; bindings/sessions are in-memory (a restart
  drops them → users re-link). Durable store (Redis is already on the box) is the
  scale-up path.

## The endpoints the bot uses on BioMate

- `POST /api/chat/stream` — NL → streamed SSE (delta / workflow_ready / final /
  done). Pass `context.sessionId` to persist.
- `POST /api/auth/login-token` (Bearer = user token) → one-time login token.
- `GET  /api/auth/magic?token=<ott>&redirect=<path>` → sets `biomate_token`
  cookie, 302s to `redirect`.
- `GET  /api/sessions/:id` — what the SPA loads for `?session=<id>`.
