# BioMate Connectors — Comprehensive Test Plan

**Branch:** `connectors-v2-launch` (pushed to `origin/connectors-v2-launch`)
**Worktree:** `/home/yzhang/biomate_worktrees/connectors_v2`
**Author:** prepared for handoff, 2026-05-21
**Scope:** OAuth 2.1 server + `@biomate/connect` installer + 6 surface adapters + MCP tools + marketing assets

---

## 0. Handoff context — what exists and where

| Asset | Location | Status |
|---|---|---|
| OAuth 2.1 server (Python) | `oauth-server/` | Built, 7/7 unit tests passing |
| Client seed script | `backend/scripts/seed_oauth_clients.py` | Built |
| OAuth tests | `backend/tests/connectors/test_oauth_server.py` | Passing |
| @biomate/connect installer (TS/npm) | `connectors/installer/` | Built, 8/8 unit tests passing |
| MCP server + tools manifest | `backend/lib/mcp/` | From prior phases, drift CI guards it |
| ChatGPT OpenAPI + GPT config | `connectors/chatgpt/` | Done in prior commit |
| Claude Skill bundle | `skills/biomate/` | Done in prior commit |
| Per-surface adapter dirs | `connectors/{claude-code,claude-desktop,cursor,codex,chatgpt,open-claw,wechat,slack}/` | READMEs + config snippets |
| Live API routing test | `backend/tests/test_connector_live.py` | 4/4 Anthropic cases pass (OpenAI requires key) |
| Sandbox validation suite | `backend/tests/test_connector_sandbox.py` | 35 offline cases pass |
| Marketing assets | `connectors/marketing/{videos,landing,blog,social}/` | Drafts complete |
| Directory submissions | `connectors/submissions/` | Pre-filled, ready to submit |
| Architecture doc | `docs/20260513_CONNECTOR_ARCHITECTURE_V2.md` | Full design, 200+ lines |
| Sandbox results doc | `docs/20260513_CONNECTOR_SANDBOX_RESULTS.md` | 35-case validation report |

**Not yet done (Task 9 in tracker):** push `bioMate-AI/biomate-connectors` public repo. Pending user review.

---

## 1. Test strategy

Tests are organized in 5 layers, run in order. A surface ships only when all 5 layers are green for that surface.

| Layer | Tests against | Cost | Speed |
|---|---|---|---|
| L1 — Unit | Code in isolation, no I/O | $0 | seconds |
| L2 — Integration | Local services (OAuth server + mock API) | $0 | seconds |
| L3 — Sandbox | Manifest schemas vs real client SDKs (no network) | $0 | seconds |
| L4 — Live API | Real Anthropic/OpenAI/Gemini, tool routing only (no workflow run) | ~$0.05/run | minutes |
| L5 — End-to-end | Full surface install + workflow run on BioMate cloud | $0.05–$2/run | 5–15 min |

CI runs L1+L2+L3 on every PR. L4 runs nightly. L5 runs pre-release.

---

## 2. L1 — Unit tests (already passing)

### 2.1 OAuth server — `backend/tests/connectors/test_oauth_server.py`

| # | Test | What it proves |
|---|---|---|
| 2.1.1 | `test_full_flow` | authorize → consent → token exchange produces a valid JWT with correct sub/surface/scopes |
| 2.1.2 | `test_pkce_mismatch_rejected` | A wrong code_verifier returns `invalid_grant` |
| 2.1.3 | `test_code_single_use` | Replaying an authorization code is rejected (OAuth 2.1 §4.1.2) |
| 2.1.4 | `test_refresh_token_rotation` | After refresh, the old refresh token is unusable (OAuth 2.1 §6.1) |
| 2.1.5 | `test_revoke_surface` | `revoke_surface(user, 'cursor')` invalidates all that user's cursor tokens |
| 2.1.6 | `test_unknown_scopes_dropped` | Unknown scopes are silently filtered |
| 2.1.7 | `test_no_recognized_scopes_rejected` | Requesting only unknown scopes → `invalid_scope` |

**Run:**

```bash
cd backend
BIOMATE_OAUTH_SIGNING_KEY=$(python -c "import secrets,base64;print(base64.urlsafe_b64encode(secrets.token_bytes(64)).decode())") \
  PYTHONPATH=lib python -m pytest tests/connectors/test_oauth_server.py -v
```

### 2.2 Installer — `connectors/installer/src/__tests__/`

| # | Test | What it proves |
|---|---|---|
| 2.2.1 | PKCE verifier length ∈ [43, 128] | RFC 7636 conformance |
| 2.2.2 | challenge = base64url(sha256(verifier)) | S256 method correctness |
| 2.2.3 | method is 'S256' | OAuth 2.1 — never 'plain' |
| 2.2.4 | verifier/challenge URL-safe (no +,/,=) | URL embedding safety |
| 2.2.5 | `upsertJSON` creates new file | Fresh install on a fresh machine |
| 2.2.6 | `upsertJSON` preserves existing keys | Doesn't clobber a user's theme/other mcpServers |
| 2.2.7 | `upsertTOML` appends new block | Codex config-file format |
| 2.2.8 | `upsertTOML` replaces existing biomate block on re-install | Idempotency |

**Run:**

```bash
cd connectors/installer
npm install
npx vitest run
```

### 2.3 Tools manifest drift — `backend/tests/test_tools_manifest.py`

Already passing in CI. Asserts that `tools_manifest.json` matches what `tools_manifest.py` exports, and that `OPEN_CLAW_TOOLS` (in `frontend/server/routes.ts`) and the MCP server's `TOOLS` dict both render identical schemas. **Fails the build if any surface drifts.**

---

## 3. L2 — Integration tests (local services)

These run against a localhost OAuth server + a stub BioMate API. Quick, deterministic, free.

### 3.1 OAuth router HTTP layer — `backend/tests/connectors/test_oauth_router.py` (TO WRITE)

| # | Scenario | Expected |
|---|---|---|
| 3.1.1 | `GET /oauth/authorize` unauthenticated | 302 → `/login?next=...` |
| 3.1.2 | `GET /oauth/authorize` with valid params, authenticated | 200 with consent HTML |
| 3.1.3 | `GET /oauth/authorize` invalid client_id | 302 to redirect_uri with `error=invalid_client` |
| 3.1.4 | `GET /oauth/authorize` redirect_uri not registered | 302 with `error=invalid_request` |
| 3.1.5 | `GET /oauth/authorize` `code_challenge_method=plain` | 302 with `error=invalid_request` (OAuth 2.1 forbids plain) |
| 3.1.6 | `POST /oauth/authorize` decision=allow | 302 to redirect_uri with `code` + `state` |
| 3.1.7 | `POST /oauth/authorize` decision=deny | 302 with `error=access_denied` |
| 3.1.8 | `POST /oauth/token` grant_type=authorization_code happy | 200, `access_token` + `refresh_token` + `scope` |
| 3.1.9 | `POST /oauth/token` wrong code_verifier | 400, `invalid_grant` |
| 3.1.10 | `POST /oauth/token` grant_type=refresh_token | 200, new tokens (verify rotation) |
| 3.1.11 | `POST /oauth/token` grant_type=client_credentials | 400, `unsupported_grant_type` |
| 3.1.12 | `POST /oauth/revoke` known token | 200, subsequent refresh fails |
| 3.1.13 | `POST /oauth/revoke` unknown token | 200 (RFC 7009 — silently succeed) |
| 3.1.14 | `GET /oauth/grants` authenticated | 200, list of {surface, scopes, expires_at} |
| 3.1.15 | `POST /oauth/grants/revoke` surface=cursor | 200, `{"revoked": N}`, then cursor tokens fail |

### 3.2 Installer end-to-end flow — `connectors/installer/src/__tests__/flow.test.ts` (TO WRITE)

Spin up a fake OAuth server (Express on a random port) that mirrors the real `/oauth/authorize` + `/oauth/token` endpoints. Drive the installer end-to-end.

| # | Scenario | Expected |
|---|---|---|
| 3.2.1 | `claude-code` install on fresh machine | `~/.claude.json` exists with `mcpServers.biomate` block |
| 3.2.2 | `claude-code` install when `~/.claude.json` has unrelated keys | Other keys preserved, biomate added |
| 3.2.3 | `claude-code` re-install (run twice) | biomate block replaced, not duplicated |
| 3.2.4 | `cursor` install | `~/.cursor/mcp.json` written |
| 3.2.5 | `codex` install | `~/.codex/config.toml` has `[mcp_servers.biomate]` block |
| 3.2.6 | `chatgpt` install | Prints instructions, doesn't try to write anywhere |
| 3.2.7 | `open-claw` install | Prints WeChat linking flow |
| 3.2.8 | Unknown surface | Exit 1, usage printed |
| 3.2.9 | OAuth callback timeout (3 min) | Error message, server closed |
| 3.2.10 | OAuth callback `error=access_denied` | Error message surfaced cleanly |
| 3.2.11 | `state` mismatch on callback | Rejected as "invalid callback" |
| 3.2.12 | Token exchange returns 400 | Error message includes the server's reply |

### 3.3 Manifest → adapters drift — `backend/tests/test_tools_manifest_drift.py` (extended)

Already exists; add cases for:

| # | Scenario | Expected |
|---|---|---|
| 3.3.1 | `connectors/chatgpt/openapi.json` operations match manifest tools | All 14 tools appear, identical params |
| 3.3.2 | `skills/biomate/references/tool_catalog.md` describes all 14 tools | grep-style assertion |
| 3.3.3 | `OPEN_CLAW_TOOLS` in `frontend/server/routes.ts` has same set | Generated from manifest, no drift |

---

## 4. L3 — Sandbox validation (offline, real SDKs)

Already covered by `backend/tests/test_connector_sandbox.py` (35 cases). Verifies the tools manifest is accepted by:

- `anthropic` SDK as `tools=[...]`
- `openai` SDK as `tools=[...]`
- `mcp` Python SDK as `tools/list` response
- ChatGPT Actions OpenAPI 3.1 schema validator
- JSON Schema Draft 7 / 2020-12 validators

**Scenarios already covered (excerpt):**
- All 14 tools serialize without `additionalProperties=false` blocking
- All param schemas have `type` and `description`
- Streaming-only fields (`stream`, `progress_token`) appear only on tools that support them
- No tool name collisions
- Enum values are non-empty

**Add (TO WRITE):**

| # | Scenario | Expected |
|---|---|---|
| 4.1 | New surface adapter included in `_shared/` | Sandbox runs against it without code change |
| 4.2 | Removing a tool from manifest | Drift test fails until adapters regenerated |
| 4.3 | Adding a required param without `description` | Sandbox fails (catches missing docs) |

---

## 5. L4 — Live API tests (real LLM, no workflow run)

Already covered by `backend/tests/test_connector_live.py`. Asserts that real Anthropic Claude and OpenAI GPT-4o, given the manifest as `tools`, pick the *intended* tool for each test prompt.

**Existing assertions:**

| # | Prompt | Expected tool | Expected key arg |
|---|---|---|---|
| 5.1 | "Screen these SMILES for hERG and CYP3A4…" | `biomate_session` | `inputs.compounds=[aspirin,caffeine]` |
| 5.2 | "What workflows do you have for CryoSPARC?" | `search_workflow` | `domain=cryo_em` |
| 5.3 | "Cancel my run with id run-abc-123" | `cancel_run` | `run_id=run-abc-123` |
| 5.4 | "Look up UniProt P04637" | `query_database` | `database=uniprot` |

**Add (TO WRITE) — 12 more cases to cover all 14 tools:**

| # | Prompt | Expected tool |
|---|---|---|
| 5.5 | "Show me what params WGS variant-calling pipeline needs" | `get_workflow_spec` |
| 5.6 | "Run workflow 12849 with stream=true" | `run_workflow` |
| 5.7 | "List my runs from last week" | `list_runs` |
| 5.8 | "What's the status of run-xyz?" | `get_run` |
| 5.9 | "Show me the volcano plot from run-xyz" | `preview_file` |
| 5.10 | "Generate a methods report for run-xyz" | `export_report` |
| 5.11 | "What do these DE results mean?" | `analyze_results` |
| 5.12 | "My run failed — explain the error" | `explain_error` |
| 5.13 | "Pull my prior CRISPR screens" | `recall_memory` |
| 5.14 | "I have a local FASTQ to upload" | `upload_file` |

**Run:**

```bash
ANTHROPIC_API_KEY=sk-... OPENAI_API_KEY=sk-... \
  python -m pytest backend/tests/test_connector_live.py -v
```

Skips cleanly when keys are absent. Total cost across 14 cases: ~$0.15.

---

## 6. L5 — End-to-end (real surface, real run)

Per-surface manual smoke tests. Run on every release. Each takes 5–15 min and ~$0.05–$2 on BioMate cloud.

### 6.1 Claude Code

| # | Scenario | Steps | Expected |
|---|---|---|---|
| 6.1.1 | Fresh install | (1) Fresh VM (2) `npx @biomate/connect claude-code` (3) Restart Claude Code (4) Prompt: "Screen aspirin for hERG" | `~/.claude.json` written, MCP indicator green, ADMET runs, methods PDF produced |
| 6.1.2 | Re-install (idempotency) | Run installer twice | No duplicate biomate entry; second install replaces first |
| 6.1.3 | Existing `.claude.json` with other keys | Pre-populate with `theme=dark` and an unrelated mcpServer | Other keys preserved after install |
| 6.1.4 | Long-running workflow (RNA-seq) | "Run RNA-seq pipeline DE on s3://..." | Phase events stream inline; final report URL valid |
| 6.1.5 | QC gate failure path | Screen a known hERG-active compound (terfenadine) | Gate fails inline, auto-loop offers remediated params with was→now diff |
| 6.1.6 | Cancellation | Start an RNA-seq run; "Cancel that run" | `cancel_run` called, run goes to CANCELLED in <30s |
| 6.1.7 | Token expiry → refresh | Wait 31 min, send a new prompt | Installer's refresh token rotates transparently |
| 6.1.8 | Revoke from web → next call fails | Revoke at biomate.ai/account/connectors → send a prompt | First call fails with auth error; clean error message |
| 6.1.9 | Offline behavior | Disconnect network mid-run | Clean error; resumable when network returns |

### 6.2 Claude Desktop

Same 9 scenarios as 6.1, with one extra:

| # | Scenario | Expected |
|---|---|---|
| 6.2.10 | Thumbnails render | Run ADMET; QC card includes a thumbnail | Thumbnail visible inline in chat |

### 6.3 Cursor

Same 9 scenarios as 6.1, plus:

| # | Scenario | Expected |
|---|---|---|
| 6.3.10 | Add result file to context | Workflow returns S3 URL → right-click "Add to context" | File downloaded into Cursor's context buffer |

### 6.4 Codex CLI

Same 9 scenarios as 6.1, with surface-specific:

| # | Scenario | Expected |
|---|---|---|
| 6.4.10 | poll_run fallback | No `notifications/progress` support → model loops on poll_run | Progress events arrive as delta lists; user sees periodic updates |

### 6.5 ChatGPT (Custom GPT)

| # | Scenario | Expected |
|---|---|---|
| 6.5.1 | First call → OAuth prompt | "Screen aspirin…" in BioMate GPT | OAuth modal opens, returns, tool call succeeds |
| 6.5.2 | Token refresh in GPT context | Wait 31 min, send another prompt | Refresh handled by ChatGPT Actions OAuth |
| 6.5.3 | OpenAPI schema validates in editor | Import openapi.json in GPT editor | No schema errors |
| 6.5.4 | All 14 tools callable | Probe each tool with a targeted prompt | All return valid responses |
| 6.5.5 | GPT store cover image | Inspect GPT store listing | Card looks correct; no clipped text |
| 6.5.6 | Long methods PDF download | "Generate IND §2.6.1 narrative…" | DOCX file attachment renders in chat |

### 6.6 Open Claw / WeChat

| # | Scenario | Expected |
|---|---|---|
| 6.6.1 | `/connect <code>` flow | `npx @biomate/connect open-claw` → terminal prints code → WeChat send `/connect <code>` | Account linked, confirmation message |
| 6.6.2 | Mandarin prompt | "筛选 aspirin 的 hERG 抑制" | Bot replies in Chinese with formatted result table |
| 6.6.3 | English prompt to WeChat | "Screen aspirin for hERG" | Bot replies in English |
| 6.6.4 | Long workflow notification | Start RNA-seq run | Push notification when each phase completes |
| 6.6.5 | Deep link in WeChat | Tap result link | biomate.ai/runs renders in WeChat's in-app browser |
| 6.6.6 | `/disconnect` | Send `/disconnect` | Confirmation, subsequent prompts return "please /connect" |
| 6.6.7 | Tencent HMAC signature | Replay a tampered webhook payload | Server rejects with 403 |

### 6.7 Slack (pilot only, public in 2 weeks)

| # | Scenario | Expected |
|---|---|---|
| 6.7.1 | `Add to Slack` install | Standard OAuth, scopes granted | App installed in workspace |
| 6.7.2 | `/biomate login` | Slack user → BioMate user link | Account mapping persisted |
| 6.7.3 | `/biomate run <prompt>` | Workflow runs | Phase updates edit the thread message in place |
| 6.7.4 | Multiple users same channel | 3 users run workflows simultaneously | No cross-talk in thread updates |
| 6.7.5 | Methods PDF as Slack attachment | Workflow completes | PDF attached to final thread message |
| 6.7.6 | `/biomate logout` | Disconnect | Subsequent commands → "please /biomate login" |

---

## 7. Security tests

| # | Scenario | Expected |
|---|---|---|
| 7.1 | Code interception (CSRF on /oauth/authorize) | Send a request with valid client_id but different user's session | Code bound to authenticated session only |
| 7.2 | Code replay across clients | User A's code presented by client B | Rejected: client_id mismatch |
| 7.3 | Refresh token database leak | Dump DB; try presenting raw token hashes | Hashes can't be reversed; HMAC key is required to re-hash |
| 7.4 | JWT signing key rotation | Rotate `BIOMATE_OAUTH_SIGNING_KEY` | Old access tokens fail validation; new ones work |
| 7.5 | Open redirect via redirect_uri | Try `redirect_uri=https://evil.com/...` | Rejected — must match registered URIs |
| 7.6 | PKCE downgrade attack | Send `code_challenge_method=plain` | Rejected — OAuth 2.1 forbids plain |
| 7.7 | Scope escalation | After consenting to `runs:read`, send refresh request asking for `runs:write` | Refresh tokens cannot widen scope (verify in code; not natively prevented by all servers) |
| 7.8 | Stale authz code | Use a code 61 seconds after issue | Rejected — TTL is 60s |
| 7.9 | Surface impersonation | Token issued for `cursor`; call MCP server claiming `surface=codex` | Server uses claim from token, not request — verify |
| 7.10 | Brute-force protection | Send 100 wrong code_verifiers/min | Rate-limited (TODO: add rate limit middleware) |
| 7.11 | Refresh token reuse detection | Present a rotated refresh token | Replay detected; entire token family revoked (defense-in-depth — TODO: implement) |
| 7.12 | TLS enforcement | Hit `/oauth/token` over HTTP in production | 308 → HTTPS; or refuse |
| 7.13 | Tencent HMAC tamper (Open Claw) | Mutate webhook body, keep signature | Rejected |

**Known follow-ups (filed as issues to address before GA):**
- 7.10 rate-limit middleware
- 7.11 refresh token reuse detection / token family revocation
- 7.7 explicit assertion in `_exchange_refresh_token` that scope set is unchanged

---

## 8. Performance + reliability tests

| # | Scenario | Threshold |
|---|---|---|
| 8.1 | `/oauth/token` latency (p50, p95) | <50ms p50, <200ms p95 |
| 8.2 | `/oauth/authorize` (consent screen) | <100ms p50 |
| 8.3 | Refresh token rotation under concurrency | 50 concurrent rotations on same token → exactly 1 succeeds |
| 8.4 | OAuth DB size at 100K active grants | <50MB; queries <10ms |
| 8.5 | Installer browser-launch time | <3s from `npx` invocation to browser open |
| 8.6 | Installer OAuth round trip | <30s from browser open to "Connected!" message (excluding human consent time) |
| 8.7 | MCP server cold start | <1s tools/list response |
| 8.8 | `biomate_session` first event latency | <2s from tool call to first progress event |
| 8.9 | SSE → MCP progress event throughput | Sustain 50 events/sec without drops |
| 8.10 | Long-poll fallback (Codex) | poll_run returns within 5s, even when no new events |

---

## 9. Cross-surface compatibility matrix

Run this matrix on every release. Each cell is one E2E test (~5 min).

|  | Claude Code | Claude Desktop | Cursor | Codex | ChatGPT | Open Claw | Slack |
|---|---|---|---|---|---|---|---|
| ADMET screening | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| RNA-seq pipeline | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| WGS variant-calling pipeline (WGS) | ✓ | ✓ | ✓ | ✓ | — | — | ✓ |
| CryoSPARC | ✓ | ✓ | ✓ | ✓ | — | — | ✓ |
| AlphaFold/ESMFold | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| PBPK | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| IND §2.6.1 narrative | ✓ | ✓ | ✓ | ✓ | ✓ | — | ✓ |
| Auto-loop QC remediation | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| File upload (`upload_file`) | ✓ | ✓ | ✓ | ✓ | ✓ | — | ✓ |
| Recall memory | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |

**Excluded cells (—):** documented surface limitations. WeChat/Open Claw doesn't surface multi-step parameter UIs well; ChatGPT message limits make CryoSPARC and Sarek's large output sets awkward.

---

## 10. Localization tests

| # | Scenario | Expected |
|---|---|---|
| 10.1 | Mandarin prompt → English engine | Bot detects zh, replies in zh; backend processes en | Result table in zh; underlying tools run normally |
| 10.2 | Methods PDF zh template | `export_report(language=zh)` | PDF in Chinese with translated section headings |
| 10.3 | SMILES with Unicode comments | "筛选 这个化合物 CC(=O)..." | Backend strips Unicode, SMILES validates |
| 10.4 | Mixed-language history | Prior runs in zh; new prompt in en | recall_memory returns zh-language findings translated to en |

---

## 11. Failure modes — chaos scenarios

| # | Inject failure | Expected behavior |
|---|---|---|
| 11.1 | BioMate cloud returns INSUFFICIENT_CAPACITY | `biomate_session` event reports "queue depth: 12, est wait 5 min"; doesn't crash |
| 11.2 | S3 PutObject 503 | Retry with exponential backoff; surface clear error after 3 fails |
| 11.3 | BioMate backend restart mid-run | SSE reconnects; client-side `poll_run` catches up |
| 11.4 | Postgres connection pool exhausted | OAuth `/token` returns 503 with `Retry-After`; installer retries |
| 11.5 | Anthropic API rate limit during routing | Backend backs off; user sees "high traffic, retrying"; ≤30s recovery |
| 11.6 | Workflow stuck (no events for 10 min) | `get_run` returns `last_event_at`; UI shows "no progress in 10 min — check?" |
| 11.7 | Refresh token expired (>30 days) | Surface forces re-OAuth with clean message |
| 11.8 | Manifest signing key rotated mid-session | All active access tokens become invalid; refresh succeeds; resume |
| 11.9 | Two installs from two laptops with same account | Both work; each gets its own refresh token; revoking one doesn't kill the other |
| 11.10 | Disk full on user's machine during install | Atomic write fails cleanly; doesn't corrupt existing `.claude.json` |

---

## 12. Documentation + DX tests

| # | Scenario | Expected |
|---|---|---|
| 12.1 | A new user follows `connectors/claude-code/README.md` cold | Reaches first successful run in <5 min |
| 12.2 | Demo prompts in each README produce real, completed runs | Test with `pytest -m readme_smoke` |
| 12.3 | Error messages from installer are actionable | `npm test -- error-strings` checks no raw stack traces leak to user |
| 12.4 | `npx @biomate/connect --help` output is current | Lists all surfaces with one-line each |
| 12.5 | Each surface README's manual config snippet works | Drop into a fresh config file → restart → MCP indicator green |
| 12.6 | `https://biomate.ai/connectors/<surface>` exists for every surface | URL probe in CI |
| 12.7 | OpenAPI spec lints clean | `spectral lint openapi.json` returns 0 errors |

---

## 13. Test ownership + handoff

### What is automated vs manual

| Layer | Automation | Owner |
|---|---|---|
| L1 unit | CI (GitHub Actions) | Backend + frontend on-call |
| L2 integration | CI nightly | Backend on-call |
| L3 sandbox | CI on every PR | Backend on-call |
| L4 live API | Nightly (requires keys in CI secrets) | Backend on-call |
| L5 E2E per surface | Manual checklist, weekly | Founder / QA contractor |
| Security audits | Quarterly + on auth changes | External security audit + internal review |
| Performance | Monthly synthetic load | DevOps |

### What's needed to run the suite end-to-end

```bash
# L1+L2+L3 (no external dependencies)
cd /home/yzhang/biomate_worktrees/connectors_v2
BIOMATE_OAUTH_SIGNING_KEY=$(python -c "import secrets,base64;print(base64.urlsafe_b64encode(secrets.token_bytes(64)).decode())") \
  PYTHONPATH=backend/lib python -m pytest backend/tests/connectors/ backend/tests/test_tools_manifest.py backend/tests/test_connector_sandbox.py -v
cd connectors/installer && npm test

# L4 (real LLM keys required)
ANTHROPIC_API_KEY=sk-ant-... OPENAI_API_KEY=sk-... \
  python -m pytest backend/tests/test_connector_live.py -v

# L5 (manual; see section 6)
```

### What's in the repo for the next person

| Doc | Purpose |
|---|---|
| `docs/20260513_CONNECTOR_ARCHITECTURE_V2.md` | Full architectural rationale |
| `docs/20260513_CONNECTOR_SANDBOX_RESULTS.md` | What was validated and how |
| `connectors/README.md` | Marketing / public-facing entry point |
| `connectors/TEST_PLAN.md` | This file |
| `connectors/marketing/` | Launch assets (videos, blog, social, landing) |
| `connectors/submissions/` | Directory submission packages |
| `oauth-server/` | OAuth source |
| `connectors/installer/` | Installer source |

### Open issues / known gaps (filed before GA)

1. **Rate limiting on `/oauth/token`** — not implemented; risk: brute-force PKCE attempts. Mitigation: deploy behind WAF or add `slowapi`.
2. **Refresh token reuse detection** — current rotate-and-revoke detects replay of the *old* token, but doesn't kill the family. Mitigation: add `token_family_id` column and revoke family on replay.
3. **Scope escalation on refresh** — code currently re-uses the original scopes (not user-supplied), which is correct, but lacks explicit test. Add 3.1.x case.
4. **Slack public release** — pending pilot completion. Submission package ready.
5. **MCP server `notifications/progress` retry semantics** — long-poll fallback isn't exhaustively tested against unreliable networks.
6. **Open Claw multilingual response detection** — currently keyword-based; LLM-based language detection would be more robust.

### What to do next session (when ready to ship)

1. Run **all L1+L2+L3 suites** — must be green.
2. Run **L4 live API** with both Anthropic + OpenAI keys — must be ≥13/14 (one tolerance for nondeterminism).
3. Walk through **6.1 Claude Code** E2E by hand (gold path).
4. If green: create + push `bioMate-AI/biomate-connectors` (Task #9 in tracker).
5. File the 6 open issues above as GitHub issues on the new public repo.
6. Launch day: follow `connectors/submissions/README.md` weekly schedule.

---

## 14. Test fixtures + demo data

| Fixture | S3 path | Used by |
|---|---|---|
| ADMET demo SMILES | `s3://biomate-demo/compounds/admet_3.smi` | All ADMET E2E tests |
| RNA-seq 6-sample FASTQ | `s3://biomate-demo/rnaseq/{T1,T2,T3,C1,C2,C3}_R{1,2}.fq.gz` | RNA-seq E2E |
| WGS 4-sample FASTQ | `s3://biomate-demo/wgs/sample{1,2,3,4}_R{1,2}.fq.gz` | Sarek E2E |
| CryoSPARC particle stack (25K particles) | `s3://biomate-demo/cryo/particles_25k.cs` | CryoSPARC E2E |
| 3.0 Å reference map | `s3://biomate-demo/cryo/ref_3A.mrc` | CryoSPARC E2E |
| Prior-run IDs for IND narrative | accounts: demo@biomate.ai → runs `ind-admet-001`, `ind-pbpk-001`, `ind-boin-001` | ChatGPT IND demo |

All fixtures are public-read; the demo account is provisioned with paid-tier quota so tests don't hit limits.

---

**End of plan.** This document, the architecture doc, the sandbox results doc, all per-surface READMEs, all marketing assets, and all submission packages are committed on `connectors-v2-launch` and ready for handoff.
