"""
Cyclic-Peptide DBTL Wiring
==========================
Plugs BioMate's macrocycle **permeability model** (Design/Learn) and a
**Bayesian-optimization / active-learning acquisition policy** (Decide) into the
generic `DBTLController`, closing the design → synthesize → PAMPA + IL-17A-binding
→ refine loop for oral macrocyclic IL-17A inhibitors.

Real wiring (injected):
  - propose_fn(params) -> List[candidate]     (generative macrocycle design)
  - score_fn(candidate) -> predicted logPapp  (our permeability model)
  - assay results ingested via the lab_instruments connectors (plate_reader/lcms/SPR)

Runs — and is unit-testable — with no model/hardware/cloud via `run_sim_demo()`.
"""
from __future__ import annotations
import json
from typing import Any, Callable, Dict, List
from .dbtl_controller import DBTLController, Decision


# ── DESIGN: propose candidates, pre-filter by predicted permeability ──────────
def make_cyclic_peptide_design(propose_fn: Callable, score_fn: Callable):
    def design(params: Dict[str, Any]) -> str:
        cands = propose_fn(params)                      # generative design
        for c in cands:
            c["pred_perm"] = float(score_fn(c))         # permeability model
        thr = params.get("perm_threshold", -6.5)
        n = params.get("n_per_round", 30)
        picked = sorted([c for c in cands if c["pred_perm"] >= thr],
                        key=lambda c: -c["pred_perm"])[:n]
        return json.dumps({"round": params.get("round", 1), "candidates": picked})
    return design


# ── LEARN: ingest PAMPA + SPR results -> hit fraction metric ──────────────────
def make_assay_analyze(perm_cut: float = -6.0, kd_cut: float = 1e-6):
    def analyze(outputs: List[Dict[str, Any]], params: Dict[str, Any]) -> Dict[str, Any]:
        rows = [o for o in outputs if "pampa_logPapp" in o]
        if not rows:
            return {"metric": None, "n": 0}
        permeable = [o for o in rows if o["pampa_logPapp"] >= perm_cut]
        hits = [o for o in rows if o["pampa_logPapp"] >= perm_cut and o.get("il17a_kd", 1) <= kd_cut]
        return {"metric": len(hits) / len(rows), "n": len(rows),
                "n_permeable": len(permeable), "n_hits": len(hits),
                "best_perm": max(o["pampa_logPapp"] for o in rows)}
    return analyze


# ── DECIDE: Bayesian-optimization / active-learning acquisition ───────────────
def make_bo_policy(target: float = 0.3, max_no_improve: int = 2, step: float = 0.3):
    """PROCEED at target hit-fraction; PIVOT after `max_no_improve` flat rounds;
    else REFINE via an acquisition step — tighten the predicted-permeability
    selection threshold (exploit) and raise exploration (diversify). Between
    rounds the model is retrained on the new assay data by the caller (the
    flywheel); this policy chooses the *next batch* to synthesize."""
    def policy(metric, params, history):
        if metric is not None and metric >= target:
            return Decision.PROCEED, params, f"hit fraction {metric:.2f} >= target {target}"
        ms = [h.metric for h in history if h.metric is not None]
        if len(ms) >= max_no_improve and ms[-1] <= min(ms[-max_no_improve:]) + 1e-9:
            return Decision.PIVOT, params, "no improvement — pivot scaffold/target"
        nxt = dict(params)
        nxt["round"] = params.get("round", 1) + 1
        nxt["perm_threshold"] = params.get("perm_threshold", -6.5) + step   # exploit
        nxt["explore"] = min(1.0, params.get("explore", 0.2) + 0.1)         # diversify
        return Decision.REFINE, nxt, (f"acquire: threshold->{nxt['perm_threshold']:.2f} "
                                      f"explore->{nxt['explore']:.2f}")
    return policy


def make_controller(propose_fn, score_fn, *, target=0.3, max_iterations=6,
                    robot=None, confirm=False) -> DBTLController:
    return DBTLController(
        design=make_cyclic_peptide_design(propose_fn, score_fn),
        analyze=make_assay_analyze(),
        policy=make_bo_policy(target=target),
        robot=robot, confirm=confirm, max_iterations=max_iterations,
        protocol_name="cyclic_peptide_dbtl.py")


def make_controller_from_pickle(propose_fn, pkl_path, *, target=0.3,
                                max_iterations=6, robot=None, confirm=False):
    """Wire the PRODUCTION permeability scorer (pickled HistGBM on RDKit
    descriptors — the model that won the scaffold-split head-to-heads) in as the
    DESIGN prefilter's score_fn. pkl_path -> cycpep_perm_histgbm.pkl (sync from
    s3://knowledge-datasets-1759951976/cyclic_peptide/scorer/)."""
    from .cyclic_peptide_scorer import load_perm_scorer
    return make_controller(propose_fn, load_perm_scorer(pkl_path), target=target,
                           max_iterations=max_iterations, robot=robot, confirm=confirm)


# ── Sim demo — full closed loop, no model/hardware/cloud ─────────────────────
def run_sim_demo():
    import random
    rng = random.Random(0)

    def propose(params):
        ex = params.get("explore", 0.2)
        return [{"seq": f"cyc{params.get('round',1)}_{i}",
                 "pred_perm": -8 + 4 * rng.random() + ex} for i in range(40)]

    def score(c):
        return c["pred_perm"]

    class SimAssay:                       # "measures" perm correlated w/ prediction
        def health_check(self): return {"name": "sim-assay"}
        def upload_protocol(self, code, name="p"): self._c = json.loads(code); return "p"
        def start_run(self, pid): return "r"
        def wait_for_completion(self, rid): return {"status": "succeeded"}
        def get_run_outputs(self, rid):
            return [{"seq": c["seq"],
                     "pampa_logPapp": c["pred_perm"] + rng.gauss(0, 0.7),
                     "il17a_kd": 10 ** (-6 + rng.random())} for c in self._c["candidates"]]

    ctrl = make_controller(propose, score, target=0.4, max_iterations=6,
                           robot=SimAssay(), confirm=True)
    return ctrl.run({"round": 1, "perm_threshold": -6.5, "n_per_round": 40, "explore": 0.2})


if __name__ == "__main__":
    for r in run_sim_demo():
        print(f"round {r.iteration}: metric={r.metric} decision={r.decision.value} — {r.note}")
