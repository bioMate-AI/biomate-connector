# Tier-1 DBTL MCP Tool Specs — 2026-07-22

> **DECISION (2026-07-22): DBTL stays INTERNAL — do NOT expose any DBTL tool
> publicly.** Per the user, the entire DBTL capability (design, score, decide,
> and the loop) is the moat and is used internally only. None of the tools below
> are registered in the public connector manifest. The 3 tools briefly added in
> commit `79d7792` were reverted. This document is retained as the **internal
> implementation reference** for the `lab_instruments/` DBTL code — not a public
> connector surface. Do not re-add these to `mcp/tools_manifest.py`.

Purpose (INTERNAL): the **closed DBTL loop** (Design→Build→Test→Learn) as MCP tools so
any host (Claude Code/Desktop, ChatGPT, etc.) can drive it. **This is wiring, not
new science** — every tool wraps code that already exists in
`biomate-connector/lab_instruments/`. Add to `mcp/tools_manifest.py` (v2.0.0) +
`dispatch_tool`; they inherit the existing transports.

Scorer model: pickled HistGBM on RDKit descriptors at
`s3://knowledge-datasets-1759951976/cyclic_peptide/scorer/` (via `load_perm_scorer`).

---

## 1. `design_candidates`  — Design (in-silico library)
Wraps `lab_instruments/macrocycle_designer.py :: propose(params) -> List[Dict]`
(+ `build_cyclic_smiles`).

**Description.** Generate a library of candidate cyclic (macrocyclic) peptides for
a design goal. Returns each candidate's sequence, residues, N-methylation pattern,
and a ready-to-use head-to-tail cyclic SMILES. Use before `score_candidates`.

**inputSchema**
```json
{ "type":"object","required":["length","n_candidates"],"properties":{
  "length":{"type":"integer","minimum":4,"maximum":15,"description":"residues per macrocycle"},
  "n_candidates":{"type":"integer","minimum":1,"maximum":1000,"description":"how many to generate"},
  "residues":{"type":"array","items":{"type":"string"},"description":"amino-acid alphabet to sample (default: natural + common non-natural)"},
  "n_methyl":{"type":"array","items":{"type":"integer"},"description":"optional N-methylation position indices to enforce/sample"},
  "seed_motif":{"type":"object","description":"optional fixed residues by position, e.g. {\"0\":\"F\",\"3\":\"Nle\"}"}
}}
```
**output**: `{ candidates: [{seq, residues, n_methyl, smiles}], count }`
**tier/cost**: read-class, cheap (in-silico, no compute job).

---

## 2. `score_candidates`  — Test (in-silico property prediction)
Wraps `lab_instruments/cyclic_peptide_scorer.py :: load_perm_scorer(pkl) ->
score_fn(candidate) -> logPapp` (`PermeabilityScorer`).

**Description.** Predict membrane permeability (log Papp) for candidate cyclic
peptides using the production HistGBM scorer over 3D RDKit descriptors. Accepts the
output of `design_candidates` (or raw SMILES). Returns candidates ranked by
predicted permeability so the host can prioritize what to synthesize.

**inputSchema**
```json
{ "type":"object","required":["candidates"],"properties":{
  "candidates":{"type":"array","items":{"type":"object","required":["smiles"],
     "properties":{"smiles":{"type":"string"},"seq":{"type":"string"}}},
     "description":"candidates to score (each needs `smiles`)"},
  "model":{"type":"string","enum":["permeability_logpapp_histgbm"],"default":"permeability_logpapp_histgbm"}
}}
```
**output**: `{ scored: [{smiles, seq?, predicted_logPapp}], ranked_desc: true, model }`
**tier/cost**: read-class; loads the pickled model (cache per-process).

---

## 3. `propose_next_round`  — Learn + Decide (choose next batch)
Wraps `lab_instruments/cyclic_peptide_dbtl.py :: make_bo_policy(...)` +
`dbtl_controller.py :: Decision` (PROCEED / REFINE / PIVOT).

**Description.** Given measured assay results from the last synthesized batch,
decide whether to PROCEED (target met), REFINE (adjust params, keep going), or
PIVOT (flat rounds → change strategy), and propose the next batch of candidates to
synthesize. This is the "Learn" step that closes the loop between wet-lab results
and the next design round.

**inputSchema**
```json
{ "type":"object","required":["results","target"],"properties":{
  "results":{"type":"array","items":{"type":"object","required":["candidate","measured_metric"],
     "properties":{"candidate":{"type":"object"},"measured_metric":{"type":"number"}}},
     "description":"prior batch: candidate + its measured value"},
  "objective":{"type":"string","enum":["permeability","affinity","hit_fraction"],"default":"hit_fraction"},
  "target":{"type":"number","description":"target metric (e.g. hit-fraction 0.3, or logPapp cutoff -6.0)"},
  "max_no_improve":{"type":"integer","default":2,"description":"flat rounds before PIVOT"}
}}
```
**output**: `{ decision: "PROCEED|REFINE|PIVOT", rationale, next_batch: [candidate...], next_params }`
**tier/cost**: read-class, cheap.

---

> **Status (2026-07-22):** Tools 1–3 IMPLEMENTED in the connector repo
> (`biomate-connector` commit `79d7792`, branch `feat/instrument-actuation-dbtl`,
> not pushed): registered in `mcp/tools_manifest.py` (now 25 tools) + dispatched
> via `dispatch_dbtl_tool` in `biomate_mcp_server.py`; `local_only=True` (MCP-only,
> excluded from OpenAPI). Manifest-drift tests fixed → `test_tools_manifest.py` +
> `test_connector_sandbox.py` + `test_mcp_e2e.py` = 84 passed / 2 skipped.
> `propose_next_round` runs fully; `score_candidates` needs `BIOMATE_PERM_SCORER_PKL`
> (model synced from S3) + RDKit in the connector runtime; `design_candidates`
> needs RDKit (wiring verified, not run e2e — RDKit absent in the test env).
> Tool 4 (`run_dbtl_loop`) DEFERRED — see below.

## 4. `run_dbtl_loop`  — Orchestrate the in-silico loop — **KEPT PRIVATE (do NOT expose)**

> **Decision (2026-07-22):** the fully-automated closed-loop orchestrator is **not**
> exposed as a public MCP tool — it is the moat (the DBTL flywheel). Expose the
> **primitives** (`design_candidates`, `score_candidates`, `propose_next_round`) so
> hosts can compose their own loop with a human in it, but keep the end-to-end
> automated `design→score→decide→refine` orchestration **internal only**
> (`lab_instruments/cyclic_peptide_dbtl.make_controller` / `DBTLController.run`,
> called server-side, never registered in `tools_manifest.py`). Spec retained
> below for the internal implementation, not for the public surface.

Wraps `cyclic_peptide_dbtl.py :: make_controller_from_pickle(...)` →
`dbtl_controller.py :: DBTLController.run(initial_params) -> List[IterationRecord]`.

**Description.** Run the full in-silico DBTL loop — design → score → decide — for up
to `max_iterations`, returning the iteration trace (params, metric, decision per
round) and the best candidates found. The wet-lab Build/Test steps (OpenTrons
protocol + assay) are substituted with the in-silico scorer here; swap in
`make_opentrons_design` + `make_biomate_analyze` to run against real hardware.

**inputSchema**
```json
{ "type":"object","required":["initial_params"],"properties":{
  "initial_params":{"type":"object","description":"seed design params (length, n_candidates, residues…)"},
  "target":{"type":"number","default":0.3},
  "max_iterations":{"type":"integer","default":6,"minimum":1,"maximum":20}
}}
```
**output**: `{ trace: [{iteration, params, metric, decision}], best_candidates, final_decision }`
**tier/cost**: write-class (multi-round compute) — gate behind auth; may run seconds–minutes.

---

## Implementation notes
- All four dispatch into existing `lab_instruments` functions — no new models.
- Register in `mcp/tools_manifest.py`; regenerate `tools_manifest.json` +
  `connectors/chatgpt/openapi.json` (`python -m mcp.tools_manifest`) — this also
  clears the current manifest-drift test failures (17→22, now 22→26).
- Decide OpenAPI-path exposure: like the 3 instrument tools, these may be MCP-only
  (excluded from ChatGPT GPT-Action paths) — resolves `operationIds unique` test.
- Ships on transports #1 (stdio) and #3 (Actions) with zero extra infra; transport
  #2 unaffected.
- Scorer needs the pickled model reachable (S3 path above) + RDKit in the connector
  runtime — already a dependency of the offline `test_lab_instruments_e2e` suite.
