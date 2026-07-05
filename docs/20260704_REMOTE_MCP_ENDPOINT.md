# Remote MCP Endpoint (Streamable HTTP + OAuth 2.1)

**Status:** Steps 0–2 done & verified locally. Steps 3–4 (prod cutover, submission backfill) pending.
**Date:** 2026-07-04

## Why this exists

Claude Code (CLI) uses the **stdio** connector (`mcp/biomate_mcp_server.py`) and needs
nothing remote. But claude.ai / Claude Desktop **custom connectors** and the **Claude
Directory** require a stable **remote HTTPS MCP endpoint** with OAuth. Probing
`test.stage-public.biomate.ai/mcp` returned SPA HTML (the frontend catch-all) and the
`.well-known/oauth-*` paths were also HTML — i.e. no real remote endpoint existed. This
package is that endpoint.

## Architecture decision: standalone service, not grafted into Galaxy

Runs as its **own ASGI process**, not inside Galaxy's FastAPI app, because:

- Galaxy's gunicorn recycles its single worker every ~3 min (`workers:1,
  max_requests:1000`), which would wipe in-process MCP session state.
- Avoids adding the MCP SDK (needs Python ≥3.10) into Galaxy's vendored core
  (which runs 3.8/3.9).
- Reuses the connector's canonical tool manifest + `dispatch_tool` **verbatim** —
  zero tool logic reimplemented.

The transport runs `stateless=True` so each request is independent and survives
restarts / horizontal scale-out.

### The `mcp/` name collision (important)

The connector ships a directory literally named `mcp/`, which **shadows the installed
MCP SDK package** (`import mcp`) whenever the repo root is on `sys.path`.
`remote_mcp/bootstrap.py` fixes it: it removes the repo root from `sys.path` (so the SDK
wins from site-packages), inserts the connector `mcp/` dir up front (so `tools_manifest`
and `biomate_mcp_server` import as top-level modules), and re-appends the repo root at the
**end** (so `oauth_server` / `remote_mcp` stay importable, at lowest priority). Every
module imports `bootstrap` before touching the SDK.

## Files (`remote_mcp/`)

| File | Role |
|---|---|
| `bootstrap.py` | `sys.path` sanitation for the `mcp/` shadow |
| `server.py` | Low-level MCP `Server` from `tools_manifest.to_mcp()` + `dispatch_tool`; per-tool scope map; per-user client cache |
| `app.py` | ASGI app: `/mcp` (guarded) + `.well-known/*` (RFC 9728/8414) + `/healthz`; top-level shim serves `/mcp` without a 307 redirect |
| `oauth_app.py` | DCR `/oauth/register` (RFC 7591), `/oauth/authorize`, `/oauth/token`, `BearerGuard` |
| `identity.py` | Per-request caller identity contextvar + `resolve_api_key` seam |
| `run.py` | uvicorn entry (passes the app object, not an import string) |
| `smoke.py` | Transport-only handshake test (no auth) |
| `oauth_smoke.py` | Full DCR→PKCE→token→bearer flow + negative 401 |

Reused from the existing repo (not rewritten): `mcp/tools_manifest.py`,
`mcp/biomate_mcp_server.py` (`BioMateClient`, `dispatch_tool`), `oauth_server/oauth/*`
(2.1 + PKCE core, JWT tokens, SQLite store).

## What was missing in `oauth_server/` and is now added here

- **DCR `/oauth/register`** (RFC 7591) — clients were previously registered by hand via
  `seed_oauth_clients.py`. claude.ai self-registers, so this was a hard gap.
- **Discovery documents** — `.well-known/oauth-protected-resource` (RFC 9728) and
  `.well-known/oauth-authorization-server` (RFC 8414) did not exist.
- **Bearer validation on the resource** — 401 + `WWW-Authenticate: Bearer
  resource_metadata="…"` so unauthenticated clients discover the AS.

## Run locally

```bash
V=mcp/.venv-remote/bin/python   # Python 3.13 venv with mcp SDK + cryptography<49

# Transport only (no auth) — proves the handshake:
$V -m remote_mcp.run &          # :8848
$V -m remote_mcp.smoke http://127.0.0.1:8848/mcp

# Full OAuth flow:
export BIOMATE_OAUTH_SIGNING_KEY=... BIOMATE_OAUTH_DB=/tmp/oauth.db
export BIOMATE_MCP_REQUIRE_AUTH=1 BIOMATE_OAUTH_DEV_AUTOCONSENT=1 BIOMATE_OAUTH_DEV_USER=test-user-123
$V -m remote_mcp.run &
$V -m remote_mcp.oauth_smoke http://127.0.0.1:8848
```

## Environment variables

| Var | Purpose | Prod |
|---|---|---|
| `BIOMATE_MCP_PUBLIC_URL` | Public origin of this endpoint (used in discovery) | `https://app.biomate.ai` |
| `BIOMATE_OAUTH_ISSUER` | AS origin (defaults to PUBLIC_URL) | `https://app.biomate.ai` |
| `BIOMATE_MCP_ALLOWED_HOSTS` | DNS-rebind Host allowlist — **MUST set in prod** | `app.biomate.ai` |
| `BIOMATE_MCP_REQUIRE_AUTH` | Enforce bearer on `/mcp` | `1` |
| `BIOMATE_OAUTH_SIGNING_KEY` | HS256 JWT signing key (shared w/ verifier) | secret |
| `BIOMATE_OAUTH_DB` | OAuth SQLite path (or swap store for Postgres) | persistent vol |
| `BIOMATE_MCP_TOOLS` | Enabled tools (`*` = all); default = 3 read-only | `*` after step 3 |
| `BIOMATE_API_URL` / `BIOMATE_API_KEY` | Backend REST target + fallback key | dev/prod backend |
| `BIOMATE_OAUTH_DEV_AUTOCONSENT` / `_DEV_USER` | **Dev only** — auto-approve authorize | unset |

## Step 3 — production cutover (TODO)

1. **Edge routing.** Point `app.biomate.ai/mcp`, `/.well-known/oauth-*`, and `/oauth/*`
   at this service (uvicorn) **before** the SPA catch-all. Need to confirm the prod edge
   (Caddy vs nginx) and where TLS terminates — the `SSL EOF` on the earlier probe means
   prod TLS behavior is unconfirmed.
2. **Real consent + login.** Replace `BIOMATE_OAUTH_DEV_AUTOCONSENT` with delegation to
   BioMate's login page (`authorize` redirects unauthenticated users to login with
   `next=` back to the flow) and a real consent screen.
3. **Per-user backend identity (the open contract).** `identity.resolve_api_key()` is a
   seam. The real fix: the **BioMate backend must accept the OAuth JWT as a bearer** (it's
   HS256 with the shared signing key, `iss=https://biomate.ai`, `aud=biomate-api`), so the
   incoming token is forwarded and calls run as that user. Until then it falls back to the
   shared `BIOMATE_API_KEY`. **This is the one piece that needs backend cooperation.**
4. **Verify remotely** — `curl` the two discovery docs from the public URL (JSON, not
   HTML), then run `oauth_smoke.py` against `https://app.biomate.ai`, then add the
   connector in claude.ai and complete a real authorize.

## Step 4 — submission backfill (TODO)

Flip the ⚠️ *Remote HTTPS MCP endpoint* line to ✅ in the Claude Directory submission copy
once step 3 verifies remotely.

## Edge routing snippets (ready to drop in — step 3)

Run the service on a private port (e.g. `127.0.0.1:8848`) behind the existing
TLS-terminating edge. Route these paths to it **before** the SPA catch-all:
`/mcp`, `/.well-known/oauth-protected-resource*`, `/.well-known/oauth-authorization-server`,
`/oauth/*`.

**Caddy:**

```caddy
app.biomate.ai {
    @mcp path /mcp /oauth/* /.well-known/oauth-protected-resource* /.well-known/oauth-authorization-server
    handle @mcp {
        reverse_proxy 127.0.0.1:8848
    }
    # ... existing SPA / API handlers below ...
}
```

**nginx:**

```nginx
# inside the app.biomate.ai server{} block, ABOVE the `location / { try_files ... index.html }`
location = /mcp                                            { proxy_pass http://127.0.0.1:8848; proxy_http_version 1.1; proxy_set_header Host $host; proxy_buffering off; }
location = /.well-known/oauth-authorization-server         { proxy_pass http://127.0.0.1:8848; proxy_set_header Host $host; }
location ^~ /.well-known/oauth-protected-resource          { proxy_pass http://127.0.0.1:8848; proxy_set_header Host $host; }
location ^~ /oauth/                                        { proxy_pass http://127.0.0.1:8848; proxy_set_header Host $host; }
```

Notes: `proxy_buffering off` on `/mcp` keeps the SSE stream flowing; forward the
real `Host` so `BIOMATE_MCP_ALLOWED_HOSTS` validation and discovery URLs are
correct. The service reads `X-Forwarded-*` via the origin envs, not headers, so
set `BIOMATE_MCP_PUBLIC_URL=https://app.biomate.ai`.

**systemd unit (service host):**

```ini
[Service]
Environment=BIOMATE_MCP_PUBLIC_URL=https://app.biomate.ai
Environment=BIOMATE_MCP_ALLOWED_HOSTS=app.biomate.ai
Environment=BIOMATE_MCP_REQUIRE_AUTH=1
Environment=BIOMATE_MCP_TOOLS=*
Environment=BIOMATE_OAUTH_SIGNING_KEY=%SECRET%
Environment=BIOMATE_OAUTH_DB=/var/lib/biomate/oauth.db
Environment=BIOMATE_API_URL=https://app.biomate.ai
ExecStart=/opt/biomate-connector/mcp/.venv-remote/bin/python -m remote_mcp.run
WorkingDirectory=/opt/biomate-connector
```

## Two external blockers for step 3

1. **Prod edge type + TLS termination** — Caddy or nginx? Where does TLS
   terminate (the earlier `SSL EOF` on `app.biomate.ai` left this unconfirmed)?
   Determines which snippet above to apply.
2. **Backend accepts the OAuth JWT** — needs backend-team change: validate the
   HS256 JWT (shared `BIOMATE_OAUTH_SIGNING_KEY`, `iss=https://biomate.ai`,
   `aud=biomate-api`) as a bearer, so per-user calls run as `sub`. Until then,
   `resolve_api_key` uses the shared key (single-tenant only).

## DEPLOYED (2026-07-04) — `https://mcp.stage-public.biomate.ai/mcp`

Live on the test/stage box and externally verified.

| Fact | Value |
|---|---|
| Public URL | `https://mcp.stage-public.biomate.ai/mcp` |
| Instance | `i-0482a6fa98c6f301a` (test/stage, us-west-2, public IP `32.186.97.78`) |
| Access | SSM only (`aws ssm send-command --region us-west-2 --instance-ids i-0482a6fa98c6f301a`) |
| Service | systemd `biomate-remote-mcp` (enabled + active), bound `127.0.0.1:8848` |
| Code | `/opt/biomate/connector/{remote_mcp,mcp,oauth_server}` + `.venv` (py3.12) |
| Env file | `/opt/biomate/connector/remote_mcp.env` (chmod 600; holds `BIOMATE_OAUTH_SIGNING_KEY`) |
| OAuth DB | `/opt/biomate/connector/data/oauth.db` (SQLite) |
| DNS | Route53 zone `stage-public.biomate.ai` (`Z0363771X6YPSJQMFRN8`), A record → `32.186.97.78` |
| Edge | Caddy (`biomate-caddy`, host-net) — site block appended to `/opt/biomate/Caddyfile`; TLS via Let's Encrypt (valid to Oct 2026) |
| Tools live | 3 read-only (`search_workflow`, `get_run`, `list_runs`) — `BIOMATE_MCP_TOOLS` unset |

Ops: `systemctl restart biomate-remote-mcp`; logs `journalctl -u biomate-remote-mcp`.
Redeploy code: tar `remote_mcp mcp oauth_server` → S3 (**bucket `biomate-test-data` is
us-east-1** — presign with `--region us-east-1`) → presigned URL → box `curl | tar` into
`/opt/biomate/connector`, then `systemctl restart`. Instance role has **no** S3 read, so
presigned (not `aws s3 cp`) is required on the box.

Externally verified (curl `--resolve` from a laptop whose local DNS oddly maps the host to
a private IP; ACME + Let's Encrypt use real public DNS so cert issuance was unaffected):
healthz 200; PRM + ASM discovery JSON; `POST /mcp` no-auth → 401 + RFC 9728
`resource_metadata`; DCR `POST /oauth/register` → 201 + `client_id` (`auth_method=none`).

### Login + consent (Step 3e — DONE)

`/oauth/authorize` is a real login+consent screen served on this first-party origin. On
submit it authenticates the user **server-side against `POST /api/auth/login`** (no
cross-domain cookie, no main-app change), then issues the code. Handles deny, bad
credentials, MFA-enabled accounts, trial-expired, and rate-limit. Verified over HTTPS:
consent renders; a bogus login returns "Invalid email or password" (proving the login call
reaches the real backend). `BIOMATE_OAUTH_DEV_AUTOCONSENT` remains a local-only shortcut.

### Per-user identity (Step 3f — DONE, via API keys, no backend change)

BioMate's auth middleware already treats per-user `bm_live_` API keys as first-class
connector identity ("connector traffic runs as that user"). So at authorize time — after
login — the service mints a per-user key via `POST /api/user/api-keys` (with the user's
session token) and stores it **encrypted** (`remote_mcp/credentials.py`, Fernet keyed off
`BIOMATE_OAUTH_SIGNING_KEY`) in the OAuth SQLite. `identity.resolve_api_key(user_id)`
returns it, so `BioMateClient` calls run as that user; falls back to the shared
`BIOMATE_API_KEY` only if minting failed. **No change to the security-critical backend auth
path was needed** (an earlier plan to make the backend accept the OAuth JWT was dropped in
favor of this). Backend endpoints confirmed present on the test box (`/api/user/api-keys`,
`/api/auth/login` both return 401 unauthenticated).

### Happy-path VERIFIED end-to-end (2026-07-05)

Ran the full real-user flow against the live endpoint from the box: register a throwaway
account on `test.stage-public` → DCR → authorize POST with real credentials → **302 + code**
→ token (access + refresh, scope `runs:read workflows:search`) → MCP `initialize` +
`tools/call search_workflow` → **OK, returned real workflow data** (`bulk_rnaseq_de_analysis`)
→ confirmed an encrypted per-user `bm_live_` key row was stored for that user_id. This proves
login (3e) + per-user identity (3f) end-to-end: the tool call ran *as that user* via the
minted key. (Throwaway user `627dcdb8-…` remains on the test DB; harmless.)

### Tool set widened (2026-07-05)

`BIOMATE_MCP_TOOLS` on staging is now set to the **13 `dispatch_tool`-backed tools**
(`search_workflow, get_workflow_spec, run_workflow, get_run, cancel_run, list_runs,
preview_file, export_report, analyze_results, explain_error, query_database, recall_memory,
upload_file`). Verified live: `tools/list` → 13, and `query_database` returned real UniProt
data (P01308 INS_HUMAN) as the authenticated user. **Not** enabled: `biomate_session`
(streaming-only) and `resolve_accession` / `browse_data` / `fetch_public_data` — these are in
the manifest but have no `dispatch_tool` handler (pre-existing manifest/dispatch gap), so
they'd error if called. Enabling them needs handlers (+ streaming transport for
`biomate_session`).

### Remaining

- **Prod cutover** to `app.biomate.ai` still pending (this is live on staging).
- **Manifest/dispatch gap:** `resolve_accession`, `browse_data`, `fetch_public_data` are
  advertised in the manifest but unimplemented in `dispatch_tool` — either implement or drop
  from the manifest.

## Verified (2026-07-04, local)

- Transport handshake: `initialize` (protocol `2025-11-25`) + `tools/list` (3 read-only tools).
- Discovery: both `.well-known` docs return JSON, scopes match `oauth_server.SCOPES`.
- Full OAuth: DCR → S256 PKCE → authorize (302 + state) → token (JWT + refresh) →
  bearer-authenticated `/mcp`; no-bearer → 401 + RFC 9728 `resource_metadata` hint.
