"""
DBTL Loop Controller
====================
A minimal, stateful **Design → Build → Test → Learn** loop that closes BioMate's
orchestration brain (analysis via `biomate_session`) with physical actuation
(OpenTrons) on the lab's local network. This is the piece that turns the
reactive instrument connectors into a self-driving loop.

One iteration:

  DESIGN  → turn a parameter set into an OpenTrons protocol
  BUILD   → upload + start the run on the robot (guarded by `confirm`)
  TEST    → wait for completion; collect outputs (+ downstream assay result)
  LEARN   → analyze the result → a scalar metric + findings
  DECIDE  → PROCEED | REFINE | PIVOT via a policy; REFINE adjusts params & loops

The controller is **dependency-injected** so it runs — and is unit-testable —
without hardware or the cloud:

  - `design(params) -> protocol_code`     (real: OpenTrons protocol generator)
  - `actuate(code) -> (run_id, status, outputs)`  (real: OpenTronsConnector; sim: SimRobot)
  - `analyze(outputs, params) -> {metric, ...}`    (real: BioMate; sim: a model)
  - `policy(metric, params, history) -> (Decision, next_params, note)`

`confirm=False` (default) never actuates: `actuate` is only invoked when a real
robot is attached AND confirm is True; otherwise BUILD/TEST is a dry-run that
returns the compiled protocol. See `run_sim_demo()` for a full closed loop with
no hardware.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)


class Decision(str, Enum):
    PROCEED = "proceed"   # target met — advance / finish
    REFINE = "refine"     # adjust parameters and iterate
    PIVOT = "pivot"       # approach is failing — stop / change strategy


@dataclass
class IterationRecord:
    iteration: int
    params: Dict[str, Any]
    run_id: Optional[str]
    run_status: Optional[str]
    metric: Optional[float]
    decision: Decision
    note: str = ""


# Injected function signatures
DesignFn = Callable[[Dict[str, Any]], str]
# analyze: (outputs, params) -> dict that MUST contain a numeric "metric"
AnalyzeFn = Callable[[List[Dict[str, Any]], Dict[str, Any]], Dict[str, Any]]
# policy: (metric, params, history) -> (decision, next_params, note)
PolicyFn = Callable[[Optional[float], Dict[str, Any], List[IterationRecord]],
                    Tuple[Decision, Dict[str, Any], str]]


@dataclass
class DBTLController:
    design: DesignFn
    analyze: AnalyzeFn
    policy: PolicyFn
    # A robot driver exposing the OpenTronsConnector surface (health_check,
    # upload_protocol, start_run, wait_for_completion, get_run_outputs). None =>
    # dry-run (never actuates).
    robot: Optional[Any] = None
    confirm: bool = False            # must be True (and robot set) to actuate
    max_iterations: int = 5
    protocol_name: str = "biomate_dbtl.py"
    history: List[IterationRecord] = field(default_factory=list)

    def run(self, initial_params: Dict[str, Any]) -> List[IterationRecord]:
        """Execute the loop until PROCEED/PIVOT or max_iterations. Returns the trace."""
        params = dict(initial_params)
        for i in range(1, self.max_iterations + 1):
            code = self.design(params)                              # DESIGN
            run_id, status, outputs = self._build_and_test(code)    # BUILD + TEST
            result = self.analyze(outputs, params)                  # LEARN
            metric = result.get("metric")
            decision, next_params, note = self.policy(metric, params, self.history)  # DECIDE
            self.history.append(
                IterationRecord(i, dict(params), run_id, status, metric, decision, note)
            )
            log.info("[DBTL %d] metric=%s decision=%s %s", i, metric, decision.value, note)
            if decision in (Decision.PROCEED, Decision.PIVOT):
                break
            params = next_params                                    # REFINE → loop
        return self.history

    def _build_and_test(self, code: str) -> Tuple[Optional[str], str, List[Dict[str, Any]]]:
        # Guardrail: only actuate with a real robot AND explicit confirm.
        if self.robot is None or not self.confirm:
            return (None, "dry_run", [{"type": "dry_run", "protocol_preview": code}])
        self.robot.health_check()                                   # verify reachable
        protocol_id = self.robot.upload_protocol(code, self.protocol_name)
        run_id = self.robot.start_run(protocol_id)
        status = self.robot.wait_for_completion(run_id)
        outputs = self.robot.get_run_outputs(run_id)
        return (run_id, status.get("status"), outputs)


# ──────────────────────────────────────────────────────────────────────────────
# Real wiring helpers
# ──────────────────────────────────────────────────────────────────────────────

def make_opentrons_design(source_labware: str, dest_labware: str,
                          pipette: str = "p300_single_gen2") -> DesignFn:
    """DESIGN fn that compiles a params dict into an OpenTrons transfer protocol.

    Expects params to contain a `transfers` list, or a `stim_volume_ul` +
    `wells` shorthand which is expanded into per-well transfers.
    """
    try:  # works both as a package import and as a bare script
        from opentrons_connector import OpenTronsConnector
    except ImportError:
        from lab_instruments.opentrons_connector import OpenTronsConnector

    def design(params: Dict[str, Any]) -> str:
        transfers = params.get("transfers")
        if transfers is None:
            vol = params["stim_volume_ul"]
            wells = params.get("wells", ["A1", "A2", "A3"])
            transfers = [{"from_well": w, "to_well": w, "volume_ul": vol} for w in wells]
        return OpenTronsConnector.generate_plate_transfer_protocol(
            source_labware=source_labware, dest_labware=dest_labware,
            pipette=pipette, transfers=transfers,
        )
    return design


def make_biomate_analyze(client: Any, goal_template: str, metric_key: str = "metric") -> AnalyzeFn:
    """LEARN fn that hands the run outputs to BioMate for analysis and pulls a
    scalar metric out of the structured findings. Requires the cloud backend.

    `goal_template` is formatted with the params dict, e.g.
    "Deconvolve immune fractions from {result_file} and report activation score".
    """
    def analyze(outputs: List[Dict[str, Any]], params: Dict[str, Any]) -> Dict[str, Any]:
        goal = goal_template.format(**params, outputs=outputs)
        findings = client.biomate_session(goal=goal, inputs={"instrument_outputs": outputs})
        metric = (findings or {}).get(metric_key)
        return {"metric": metric, "findings": findings}
    return analyze


def threshold_policy(target: float, step_key: str, step: float,
                     tol: float = 0.0, max_no_improve: int = 2) -> PolicyFn:
    """A simple DECIDE policy: PROCEED once metric >= target; else REFINE by
    nudging params[step_key] up by `step`; PIVOT if the metric has stalled
    (no improvement) for `max_no_improve` consecutive iterations."""
    def policy(metric: Optional[float], params: Dict[str, Any],
               history: List[IterationRecord]) -> Tuple[Decision, Dict[str, Any], str]:
        if metric is None:
            return (Decision.PIVOT, params, "no metric returned — cannot decide")
        if metric >= target - tol:
            return (Decision.PROCEED, params, f"target {target} met (metric={metric:.3f})")
        # stall detection
        recent = [h.metric for h in history[-max_no_improve:] if h.metric is not None]
        if len(recent) >= max_no_improve and max(recent) >= metric - 1e-9:
            return (Decision.PIVOT, params,
                    f"metric stalled at ~{metric:.3f} for {max_no_improve} rounds — pivot")
        nxt = dict(params)
        nxt[step_key] = params.get(step_key, 0) + step
        return (Decision.REFINE, nxt, f"metric {metric:.3f} < {target} — raise {step_key} to {nxt[step_key]}")
    return policy


# ──────────────────────────────────────────────────────────────────────────────
# Simulation harness (no hardware / no cloud) — proves the loop end to end
# ──────────────────────────────────────────────────────────────────────────────

class SimRobot:
    """Fake OpenTrons that echoes a completed run. Lets the loop run offline."""
    def health_check(self): return {"name": "sim-ot2"}
    def upload_protocol(self, code, name="p.py"): return "sim-protocol"
    def start_run(self, protocol_id): return "sim-run"
    def wait_for_completion(self, run_id): return {"status": "succeeded"}
    def get_run_outputs(self, run_id): return [{"type": "sim", "well_volumes": True}]


def run_sim_demo() -> List[IterationRecord]:
    """Closed DBTL loop, fully simulated: titrate cytokine-stimulation volume in a
    PBMC activation assay to reach a target activation score.

    Models a saturating dose-response: activation = Emax * v / (EC50 + v), so the
    policy must REFINE the volume upward until the metric crosses the target.
    """
    EMAX, EC50, TARGET = 1.0, 40.0, 0.80  # target needs v >= 160 uL

    def sim_analyze(outputs: List[Dict[str, Any]], params: Dict[str, Any]) -> Dict[str, Any]:
        v = params["stim_volume_ul"]
        activation = EMAX * v / (EC50 + v)
        return {"metric": round(activation, 4), "activation": activation}

    controller = DBTLController(
        design=make_opentrons_design(
            "corning_96_wellplate_360ul_flat", "corning_96_wellplate_360ul_flat"),
        analyze=sim_analyze,
        policy=threshold_policy(target=TARGET, step_key="stim_volume_ul", step=40.0),
        robot=None,          # dry-run — no actuation
        confirm=False,
        max_iterations=8,
    )
    return controller.run({"stim_volume_ul": 20.0, "wells": ["A1", "A2", "A3"]})


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    trace = run_sim_demo()
    print("\n=== DBTL trace (simulated PBMC cytokine titration) ===")
    for r in trace:
        print(f"  iter {r.iteration}: stim_volume_ul={r.params['stim_volume_ul']:>5} "
              f"metric={r.metric:.3f} -> {r.decision.value:8} | {r.note}")
    final = trace[-1]
    print(f"\nConverged in {len(trace)} iterations: "
          f"{final.decision.value} at volume={final.params['stim_volume_ul']} "
          f"(metric={final.metric:.3f})")
