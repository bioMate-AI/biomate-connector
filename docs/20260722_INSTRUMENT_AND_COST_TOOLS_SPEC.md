# Instrument (Tier 2) + Cost/Quota (Tier 3) MCP Tool Specs — for review — 2026-07-22

Proposed connector functions to add, for review before implementation. Companion
to the DBTL spec (`20260722_DBTL_MCP_TOOLS_SPEC.md`, which is parked). Approved to
proceed: **instruments + cost/quota**. Each tool below names the existing code /
endpoint it wraps; nothing here is new science.

Legend: **local_only** = dispatched in the local MCP server (on-prem hardware /
LAN, excluded from cloud surfaces). **backend** = proxied to the BioMate cloud API.

---

## Tier 2 — Instruments (expose the 9 non-OpenTrons connectors)

The connectors fall into 4 shapes, so a small generic tool set covers them:

| Connector | Shape | Class · key methods |
|---|---|---|
| Illumina BaseSpace | cloud API | `BaseSpaceConnector` · list_projects/list_runs/get_run/list_run_samples/list_sample_files |
| Benchling (ELN) | cloud API | `BenchlingConnector` · search_entries/get_entry/list_samples · **create_assay_result/bulk_create_assay_results** |
| Nanopore MinKNOW | on-prem detector | `MinKNOWConnector` · list_positions/detect_completed_runs/get_output_files/run_to_biomate_params |
| CryoEM EPU | on-prem detector | `EPUConnector` · list_sessions/detect_completed_sessions/get_session_stats/session_to_biomate_params |
| LC-MS / flow / qPCR / plate reader | file watchers | `*Vendor` parsers + `*Watcher` (parse output files) |
| SiLA2 | generic device | `SiLA2Device` · connect/list_features/run_command/read_property |

### 1. `list_instrument_runs`  (local_only for on-prem; backend/creds for cloud)
List runs/sessions from a connected instrument platform. Wraps
`BaseSpaceConnector.list_runs`, `MinKNOWConnector.list_positions`,
`EPUConnector.list_sessions`.
- **input:** `platform` (enum `illumina|nanopore|cryoem`), `status` (opt),
  `limit` (opt), plus per-platform connection args (host/token — see Creds).
- **output:** `{runs:[{id, status, name, ts, platform}], count}`

### 2. `fetch_instrument_data`  (the high-value one)
Get the output files for a completed instrument run **plus BioMate-ready params**,
so the host can pipe it straight into `biomate_session`. Wraps
`MinKNOWConnector.get_output_files` + `run_to_biomate_params`,
`EPUConnector.session_to_biomate_params`, `BaseSpaceConnector.list_sample_files`.
- **input:** `platform`, `run_id`/`session_id`/`position`.
- **output:** `{files:[…], biomate_params:{…}, suggested_goal:"…"}`
- Chains: `fetch_instrument_data` → `biomate_session(goal, params)`.

### 3. `detect_completed_runs`  (local_only)
Poll a local instrument for newly-finished runs/sessions ready to analyze. Wraps
`MinKNOWConnector.detect_completed_runs`, `EPUConnector.detect_completed_sessions`.
- **input:** `platform`, connection args.
- **output:** `{completed:[{id, ts, ready:true}]}`

### 4. `parse_instrument_output`  (local_only)
Parse a local output file/dir from a file-watcher instrument (LC-MS / flow / qPCR /
plate reader) into structured measurements. Wraps the `*Vendor` parsers.
- **input:** `instrument` (enum `lcms|flow|qpcr|plate_reader`), `path`.
- **output:** `{measurements:[…], schema, source_path}`

### 5. `eln_search` / `eln_get_entry`  (backend/creds — Benchling API)
Read the ELN. Wraps `BenchlingConnector.search_entries/get_entry/list_samples/extract_entry_text`.
- **input:** `query` or `entry_id`; **output:** entries/samples (+ plain text).

### 6. `register_eln_result`  (backend/creds — Benchling write-back)
Push a completed run's results back to the ELN. Wraps
`BenchlingConnector.create_assay_result` / `bulk_create_assay_results`.
- **input:** `schema_id`, `results:[{field:value}]` (or a BioMate `run_id` to pull
  findings from), `entry_id` (opt link).
- **output:** `{created:[assay_result_id…], count}`
- **write action** → `destructive_hint=false` but not read-only; consider a
  `confirm` guardrail like `run_instrument_protocol`.

### 7. `run_sila2_command`  (local_only, advanced)
Execute a SiLA2 command on a generic lab device. Wraps `SiLA2Device.run_command`
/ `list_features` / `read_property`.
- **input:** `endpoint`, `feature`, `command`, `params`.
- **output:** command result. **Physical/stateful** → `confirm` guardrail.

**Creds/config:** cloud connectors need secrets — BaseSpace token, Benchling API
key; on-prem need host/IP (MinKNOW, SiLA2 endpoint). Mirror the OpenTrons pattern
(env/config, never in the tool call). Cloud ones (Illumina/Benchling) could be
`backend` tools if we want the secret held server-side instead of local_only.

---

## Tier 3 — Cost / Quota (backend tools, `/api/billing/*`)

### 8. `get_quota`  → **GET `/api/billing/quota`** (exists ✅)
Return the user's plan limits and current usage %. Ready to wire — backend
endpoint already implemented (`billing_router.py:268`).
- **output:** `{plan, limits:{…}, used:{…}, pct_consumed}`

### 9. `get_usage`  → **GET `/api/billing/usage`** (exists ✅; + `/history`, `/cohort`)
Current-month cost/usage breakdown. Ready to wire (`billing_router.py:187`).
- **input:** `period` (opt: `month|history|cohort`).
- **output:** breakdown by category (LLM tokens, vCPU-h, GPU-h, storage).

### 10. `estimate_cost`  → ⚠️ **needs a pre-run estimate endpoint**
"How many credits will this workflow cost before I run it?" — the most useful cost
tool, but there is **no pre-run estimate endpoint today**: only running/completed
jobs have costs (`/api/billing/jobs/running`), and there's an internal inline
price-table estimate (`billing_internal.py:133`). To ship this we'd add a small
`POST /api/billing/estimate` that takes `{workflow_id, params, compute_tier}` and
returns a credit range from the same price table — then wrap it. Flagged as a
backend dependency, not a pure connector wrap.
- **input:** `workflow_id`, `params`, `compute_tier`.
- **output:** `{credits_low, credits_high, usd_low, usd_high, basis}`
- Ties into the new pay-as-you-go pricing ("exact cost shown before each run").

---

## Recommended sequencing
1. **Cheapest, highest-value first:** `get_quota` + `get_usage` (2 backend wraps,
   endpoints exist, no creds/hardware) → clears the "no cost visibility in the
   connector" gap that the pay-as-you-go pricing implies.
2. **`fetch_instrument_data`** (+ `list_instrument_runs`, `detect_completed_runs`)
   — the instrument→analysis handoff; highest instrument value.
3. **`eln_search` / `register_eln_result`** — closes the results→ELN loop.
4. **`estimate_cost`** — after the small backend endpoint is added.
5. `parse_instrument_output`, `run_sila2_command` — niche, last.

## Implementation notes (same pattern as existing tools)
- Add `ToolSchema` entries to `mcp/tools_manifest.py`; `backend` tools use a real
  `/api/…` backend_path (like `search_workflow`); on-prem tools use
  `local_only=True` + `local:<name>` (like the instrument/DBTL tools).
- Add dispatch: backend tools go through `client` in `dispatch_tool`; local tools
  get a `dispatch_*_tool` handler mirroring `dispatch_instrument_tool`.
- Regenerate manifest + openapi (`python -m mcp.tools_manifest`); update the
  tool-count assertion; add `VALID_SAMPLES`.
- Write actions (`register_eln_result`, `run_sila2_command`) get a `confirm`
  guardrail.
