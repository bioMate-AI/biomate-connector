# Connector Test Results + Function Gap Analysis — 2026-07-22

Scope: test every BioMate connector against the current system, and assess
whether the connector should expose more functions. Two evidence streams:
(1) offline pytest suites (code health), (2) live probes of dev-public
(runtime health). Inventory from `mcp/tools_manifest.json` (schema v2.0.0).

## 1. Test results

### Offline pytest (code health) — 321 passed / 9 failed / 29 skipped / 2 xfail (361)
- **~97% of runnable, code-exercising tests are green.** All mock suites pass:
  chatgpt(16), coze(18), feishu(38), instrument_dispatch(9),
  lab_instruments_e2e(67), oauth_router(15), oauth_security(8+2xfail),
  oauth_server(7), slack(35), telegram(22), wechat(13), tools_manifest(30/31).
- **29 skips are environment-gated by design** (live BioMate API/token: 17;
  running MCP server: 2; optional hardware paths: 10). Not failures.
- **All 9 failures are stale-test / spec-drift, NOT runtime bugs.** Root cause:
  the manifest grew 17 → 22 tools (added `search_literature`, `upload_file`,
  `list_instruments`, `get_instrument_status`, `run_instrument_protocol`) but
  fixtures/counts/committed spec weren't updated:
  - `test_connector_sandbox.py` (8): hard-coded `==17`, missing `VALID_SAMPLES`
    entries for the 5 new tools, and `operationIds unique` (19 HTTP ops vs 22
    tools — instrument tools are in TOOL_SCHEMAS but not emitted to OpenAPI paths).
  - `test_tools_manifest.py` (1): committed `openapi.json` frozen to dev URLs +
    `BioMateApiKey` scheme; generator now emits prod URLs. Fix = regenerate.
- **CI gap:** repo test env missing declared dev deps (fastapi, python-multipart,
  PyJWT, flask) → false collection errors. Add a `requirements-dev.txt`.

### Live runtime (dev-public.biomate.ai, probed 2026-07-22)
| Surface | Status |
|---|---|
| `GET /connectors/chatgpt/openapi.json` | ✅ 200, 19 tool paths, served live by Node/nginx |
| `POST /tools/search_workflow`,`resolve_accession`,`search_literature`,`recall_memory` | ✅ 200 (authed) |
| **`/mcp` (Streamable-HTTP MCP transport)** | ❌ **500** — `Dynamic require of "http" is not supported` (esbuild ESM bundling bug); Python `remote_mcp` not fronting the path |
| **`/oauth/token`, `/oauth/register`** | ❌ **404** — not deployed |
| `/oauth/authorize` | ⚠️ 200 but only the SPA catch-all, not the real consent page |
| `/.well-known/oauth-authorization-server`, `/.well-known/oauth-protected-resource` | ❌ **500** — same esbuild bug |

**Interpretation (corrected — 3 transports, not one).** `connectors.html`
documents three MCP transports; the outage is confined to ONE of them:

| Transport | Hosts | Auth | Status |
|---|---|---|---|
| **1. stdio JSON-RPC** | Claude Code, Claude Desktop, Cursor, Codex | API key (`bm_live_…`) | ✅ Working — `@biomate/mcp-server` + `@biomate/connect` both npm 0.1.0; local binary → backend, no remote service needed |
| **2. Streamable HTTP + OAuth 2.1** | Claude Science (hosted claude.ai) | OAuth/PKCE | ❌ Down on dev — the only transport needing the `remote_mcp` service |
| **3. REST Actions + webhooks** | ChatGPT Custom GPT, Slack, Feishu | key/OAuth | ✅ ChatGPT `openapi.json` 200; Slack/Feishu self-hosted |

So the *majority of the connector suite is up and tested* — stdio (the primary
IDE/desktop path, API-key auth) and ChatGPT Actions. **Only transport #2** (the
hosted, OAuth-based Streamable-HTTP endpoint) is non-functional on dev. Earlier
framing ("MCP/OAuth broken → connectors down") over-stated this: no IDE/desktop
or ChatGPT user is affected — only hosted claude.ai custom connectors.

## 2. Priorities to fix (before adding functions)

- **P2 (was P0, downgraded) — Streamable-HTTP transport #2 down on dev.** Affects
  only hosted claude.ai custom connectors, NOT stdio (IDE/desktop) or ChatGPT
  Actions. Deferred by decision 2026-07-22 — dev-host confirmed to have no
  connector code / no `:8848` service, so this is a real infra standup, not a
  quick fix, and it serves a secondary audience. Three underlying issues:
  - **(a) code, FIXED** `server/routes.ts` `mcpProxy` used dynamic
    `require("http")` → throws `Dynamic require of "http"` once bundled (ESM),
    500-crashing `/mcp` + both `.well-known` routes. Switched to static import.
  - **(b) code, FIXED** Node proxied only `/mcp` + 2 `.well-known` paths to the
    remote_mcp app; `/oauth/register|authorize|token` were never proxied (fell
    to SPA catch-all → 404/index.html). Added the 3 `/oauth/*` proxy routes.
    Both (a)+(b) committed, tsc-clean.
  - **(c) deploy, OPEN — the real blocker.** The `remote_mcp` Starlette service
    (`python -m remote_mcp.run`, listens `127.0.0.1:8848`) is **not deployed/
    running on the dev host**, and no pipeline deploys it (`sync_to_dev.sh`
    ships the biomate frontend/backend, not the connector repo). Until a
    process runs on :8848, the proxied routes return 502. Fix requires standing
    up remote_mcp on the dev EC2 (systemd unit or container running the
    launcher, `BIOMATE_MCP_HOST=127.0.0.1 BIOMATE_MCP_PORT=8848`) + a sync step
    for the connector repo. Cross-repo infra decision.
- **P1 — test drift (quick chores):** update `VALID_SAMPLES` + `==17`→`==22`;
  regenerate committed `openapi.json`; decide if instrument tools belong in
  OpenAPI paths (they're MCP-only today).

## 3. Should we have more functions? — Yes; sequence after the P0 fix

The connector's **host coverage is mature** (~16 surfaces) and its 22 MCP tools
cover workflow lifecycle + data + analysis well. The gap is **code that exists
in `lab_instruments/` but isn't exposed as MCP tools** — so hosts can't reach it.
Exposing these is wiring, not new subsystems.

### Tier 1 — DBTL loop → **KEEP INTERNAL (decision 2026-07-22, do NOT expose)**
`lab_instruments/` has `dbtl_controller.py`, `macrocycle_designer.py`,
`cyclic_peptide_scorer.py`, `cyclic_peptide_dbtl.py`. These were briefly wrapped
as MCP tools (design/score/decide) then **reverted** — the user's decision is that
the **entire DBTL capability is the moat and stays internal-only**, not revealed
in any connector surface (not even local MCP). The code stays for internal/
server-side use; it is NOT registered in `tools_manifest.py`. So the public
"more functions" recommendation drops Tier 1 and leads with Tier 2 + Tier 3.

### Tier 2 — Instrument breadth (only OpenTrons is MCP-exposed today)
9 connectors exist but aren't MCP tools: Illumina/BaseSpace, Nanopore/MinKNOW,
CryoEM, LC-MS, flow cytometer, qPCR, plate reader, Benchling (ELN), SiLA2. Add:
- `submit_sequencing_run` (Illumina/Nanopore)
- `fetch_instrument_data` (LC-MS/flow/qPCR/plate reader)
- `register_eln_entry` / `push_results_to_eln` (Benchling) — results write-back

### Tier 3 — Product-aligned utility
- `estimate_cost` / `get_quota` — credits preview (ties to new pay-as-you-go pricing)
- `compare_runs` — cross-run comparison for iterative optimization

### What NOT to add
Host coverage is already broad; more chat surfaces add little. Focus on
FUNCTION depth (DBTL + instruments + write-back), not more hosts.
