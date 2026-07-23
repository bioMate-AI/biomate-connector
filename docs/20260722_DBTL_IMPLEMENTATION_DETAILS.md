# DBTL Tool Implementation — Details for Review — 2026-07-22

Status: the decision to keep DBTL internal is **not absolute** — this documents
exactly what was built so it can be reviewed for whether/how to expose it.

- **Implemented** in `biomate-connector` commit `79d7792` (branch
  `feat/instrument-actuation-dbtl`, **not pushed**).
- **Exposure reverted** in a follow-up commit (manifest back to 22 tools, no DBTL
  in any public surface). The implementation code + this doc are the review
  artifact; re-exposing is a small, well-understood change.
- Wraps **existing** `lab_instruments/` code — no new science.

---

## What was built — 3 MCP tools (design → score → decide)

All three: `tier="instrument"`, `local_only=True`, `read_only_hint=True`,
`destructive_hint=False`, `idempotent_hint=True`, `open_world_hint=False`,
`backend_path="local:dbtl/<name>"`. `local_only=True` means they dispatch inside
the locally-running MCP server and are **excluded from cloud surfaces**
(ChatGPT GPT-Action / OpenAPI / lite) — exposed only over local stdio MCP.

### 1. `design_candidates` → `macrocycle_designer.propose(params)`
- **inputSchema keys (exact `propose` params):** `n` (int, default 200),
  `length` (int or `[min,max]`, default `[6,8]`), `max_nmethyl` (int, default 2),
  `alphabet` (array[str], optional), `seed` (int, default 0).
- **Handler:** passes args straight to `propose()` (coerces `length` list→tuple
  since `propose` calls `randint(*length)`). Returns `{candidates, count}` where
  each candidate = `{seq, residues, n_methyl, nmethyl_idx, smiles}`.
- **Runtime dep:** RDKit (lazy import).

### 2. `score_candidates` → `cyclic_peptide_scorer.load_perm_scorer(pkl)`
- **inputSchema:** `candidates` (array; each requires `smiles`, optional `seq`).
- **Handler:** loads the pickled HistGBM permeability model from local path in
  env `BIOMATE_PERM_SCORER_PKL` (clear `ValueError` if unset/missing, pointing to
  `s3://knowledge-datasets-1759951976/cyclic_peptide/scorer/`); scores each
  candidate; returns `{scored:[{…, predicted_logPapp}], model, ranked_desc:true}`
  sorted by predicted logPapp desc. **Scorer cached per-process per pickle path.**
- **Runtime dep:** RDKit + the model pkl on the local machine.

### 3. `propose_next_round` → `cyclic_peptide_dbtl.make_bo_policy` + `Decision`
- **inputSchema:** `results` (array of `{candidate, measured_metric}`),
  `objective` (enum `permeability|affinity|hit_fraction`, default `hit_fraction`),
  `target` (number, default 0.3), `max_no_improve` (int, default 2),
  `params` (object, optional — current design params).
- **Handler / metric aggregation (key decision):**
  `hit_fraction` → fraction of measurements `>= target` (matches
  `make_assay_analyze`'s hit-fraction semantics); `permeability`/`affinity` →
  batch mean. Runs `make_bo_policy(target, max_no_improve)` → returns
  `{decision (PROCEED|REFINE|PIVOT), rationale, next_params, metric}`.
- **Limitation:** one-shot — `history` is empty, so **PIVOT cannot fire** (it needs
  `max_no_improve` flat rounds); that requires `run_dbtl_loop`. No RDKit needed;
  runs fully offline.

## Wiring (mirrors the instrument-tool pattern)
- `mcp/tools_manifest.py`: 3 `ToolSchema` entries after `run_instrument_protocol`.
- `mcp/biomate_mcp_server.py`: `_load_dbtl()` (package-qualified imports —
  `cyclic_peptide_dbtl` uses `from .dbtl_controller import …`, so the **repo root**
  goes on `sys.path`, not `lab_instruments/`), `_get_perm_scorer()` (cache +
  env-var guard), `dispatch_dbtl_tool()`, and a branch in `dispatch_tool()` next
  to the instrument branch.

## Tests (at the exposed state, before revert)
`python3.13 -m pytest tests/test_tools_manifest.py tests/test_connector_sandbox.py
tests/test_mcp_e2e.py -q` → **84 passed / 2 skipped**. Manifest regenerated to 25
tools. Also fixed pre-existing drift (17→count, `VALID_SAMPLES` gaps) and a
`test_openapi_operation_ids_unique` bug (scoped to non-`local_only` tools).

## Runtime verification
- `propose_next_round` — ran fully (permeability→`proceed`, hit_fraction→`refine`
  with correct `next_params`).
- `score_candidates` — correctly raises the env-var `ValueError` when unset.
- `design_candidates` — dispatch reaches `propose()`; **not run e2e** (RDKit absent
  in the test env). Wiring verified; needs RDKit + model in the connector runtime.

## `run_dbtl_loop` — the full orchestrator (NOT built; internal by design)
The automated `design→score→decide→refine` loop (`make_controller` /
`DBTLController.run` in `lab_instruments/`) was **never wrapped** — it is the
flywheel/moat and stays internal/server-side.

---

## Options for your review

| Option | What external hosts can do | Exposes |
|---|---|---|
| **A. Keep all internal** (current) | nothing DBTL | nothing |
| **B. Expose primitives, local-only** (what 79d7792 did) | run design/score/decide over local stdio MCP (Claude Desktop/Cursor/Code) — human drives the loop | the 3 primitives; NOT the auto-loop; NOT on ChatGPT/cloud |
| **C. Expose primitives on cloud too** | + ChatGPT/GPT-Action | primitives on all surfaces |
| **D. Expose the full `run_dbtl_loop`** | one call runs the whole automated loop | the flywheel itself |

**Considerations:** the *primitives* (B) reveal the design/score/decide building
blocks but not the orchestration policy or the retrain-between-rounds flywheel —
a knowledgeable user could approximate a loop by chaining them with their own
policy. The *scorer* (score_candidates) surfaces the production permeability model
only as a black-box logPapp number (model weights stay server-side / in S3, never
shipped). Option **D** would reveal the acquisition/policy orchestration that is
the actual moat — not recommended. Reverting between B and A is a ~30-line change.

To move from A→B: re-apply the `79d7792` manifest + dispatch changes (they're in
git history), regenerate the manifest, ensure RDKit + `BIOMATE_PERM_SCORER_PKL`
are in the connector runtime.
