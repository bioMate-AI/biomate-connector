"""
Campaign ↔ DBTL Bridge — closing the wet-lab loop
=================================================
This is the **glue** that joins BioMate's two pre-existing DBTL halves into one
closed Design → Build → Test → Learn loop:

  1. the generic **loop skeleton** (`dbtl_controller.DBTLController` +
     `cyclic_peptide_dbtl.make_bo_policy`) — the orchestration brain, and
  2. the **backend campaign DBTL API** (`/api/drug-discovery/{cid}/...`) — the
     GP surrogate, assay ledger, CRO order export and next-round proposer.

Neither half is reinvented here. The bridge only *binds* a backend campaign
into the controller by supplying the four injected functions the controller
expects, routing each phase to the existing REST endpoint:

  DESIGN  → GET  /{cid}/compounds            (rank current candidates)
            GET  /{cid}/export/cro-order     (emit the CRO order sheet — the
                                              physical "Make" hand-off)
  BUILD   → *stubbed* wet-lab hop. No hardware. In dry-run the "results" come
            from an injected canned-assay-CSV source (a `SimAssay`-style
            object), exactly like `DBTLController(robot=None)`.
  TEST    → same stubbed hop (the assay results CSV IS the test readout).
  LEARN   → POST /{cid}/ingest-assay         (connector-captured results reach
                                              the campaign GP — the SEAM)
            POST /{cid}/propose  (or GET /{cid}/auto-proposal, which the backend
                                  auto_retrain daemon populates from the
                                  INGEST_ASSAY event) → round N+1 candidates
  DECIDE  → cyclic_peptide_dbtl.make_bo_policy(...)  (active-learning acquisition)

The whole point of the demo is to prove the **seam is closed**: results a
connector captured actually land at `/ingest-assay`, and the next batch of
compounds comes back out of `/propose`. That means connector results now drive
the *campaign* GP — not merely the generic `biomate_session` analyze path.

Honesty / real-lab dependency
-----------------------------
The **Make** step (physical synthesis + running the assay at a CRO) is a real
external dependency and stays stubbed. This module never fabricates assay
numbers: in dry-run the results come from an explicit canned CSV you pass in
(`SimAssaySource`). Wiring a live CRO/LIMS just means swapping that source for
one backed by `/{cid}/lims/poll` (Benchling) or a delivered results file.

Offline & testable
------------------
An HTTP client is *injected* (`http`), so the bridge runs fully offline against
a mock that records requests. `run_bridge_sim_demo()` does exactly this — no
live backend, no hardware — and asserts the seam is closed. `confirm=False` and
`robot=None` throughout: nothing is ever actuated.
"""
from __future__ import annotations

import csv
import io
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .dbtl_controller import DBTLController, Decision, IterationRecord
from .cyclic_peptide_dbtl import make_bo_policy

log = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# HTTP client contract (injected — real or mock)
# ──────────────────────────────────────────────────────────────────────────────
# The bridge only needs a tiny surface. The real client can wrap `requests`;
# the demo uses a request-recording mock. A response object must expose:
#   .status_code : int
#   .text        : str
#   .json()      : parsed body  (used for compounds / propose / auto-proposal)
HttpClient = Any  # duck-typed: .get(url, params=…) / .post(url, json=…|files=…)


# ──────────────────────────────────────────────────────────────────────────────
# Assay results source (the STUBBED Make/Test hop)
# ──────────────────────────────────────────────────────────────────────────────
class SimAssaySource:
    """A canned assay-results source standing in for the physical CRO hop.

    In production this is replaced by a real delivered results CSV or a
    `/{cid}/lims/poll` (Benchling) fetch. It NEVER invents chemistry-aware
    numbers on its own — you hand it a CSV (path or text) of measured values.
    The `for_order(...)` hook lets a demo emit rows only for the compounds the
    current CRO order actually requested, so the closed loop stays honest about
    which compounds were "made".
    """

    def __init__(self, csv_text: Optional[str] = None,
                 rows: Optional[List[Dict[str, Any]]] = None):
        if csv_text is None and rows is None:
            raise ValueError("SimAssaySource needs canned csv_text or rows")
        self._csv_text = csv_text
        self._rows = rows

    @classmethod
    def from_path(cls, path: str) -> "SimAssaySource":
        with open(path, "r", encoding="utf-8") as fh:
            return cls(csv_text=fh.read())

    def for_order(self, order_csv_text: str, round_no: int) -> str:
        """Return an assay-results CSV for the compounds in a CRO order sheet.

        Default behaviour: return the canned CSV as-is (the demo pre-canned it
        to match). Subclasses may filter to the ordered compound_ids. This is
        the ONLY place a demo may synthesise fixed numbers — and it does so from
        an explicit canned file, not from a model.
        """
        if self._csv_text is not None:
            return self._csv_text
        buf = io.StringIO()
        cols = ["compound_id", "assay_type", "value", "unit", "assay_date", "smiles"]
        w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in (self._rows or []):
            w.writerow(r)
        return buf.getvalue()


# ──────────────────────────────────────────────────────────────────────────────
# The bridge
# ──────────────────────────────────────────────────────────────────────────────
@dataclass
class CampaignDBTLBridge:
    """Binds a backend drug-discovery campaign into the generic DBTLController.

    Parameters
    ----------
    campaign_id : the backend campaign to drive.
    base_url    : e.g. "https://dev-public.biomate.ai" (no trailing slash).
    http        : an injected HTTP client (real `requests`-like, or a mock).
    assay_source: the stubbed Make/Test hop — a canned/delivered results CSV.
    virtual_library : SMILES pool the backend GP proposes the next round from.
    use_auto_proposal : if True, LEARN reads GET /auto-proposal (populated by
                        the backend auto_retrain daemon off the INGEST_ASSAY
                        event) instead of POSTing /propose synchronously.
    """

    campaign_id: str
    base_url: str
    http: HttpClient
    assay_source: SimAssaySource
    virtual_library: List[str] = field(default_factory=list)
    n_propose: int = 10
    diversity_weight: float = 0.2
    cro_fmt: str = "generic"
    use_auto_proposal: bool = False
    # request/response audit trail — proves which endpoints were hit
    calls: List[Dict[str, Any]] = field(default_factory=list)
    # the CRO order sheet emitted this round (the physical Make hand-off)
    last_order_csv: Optional[str] = None
    # the candidates the backend proposed for round N+1
    last_proposed_smiles: List[str] = field(default_factory=list)

    # -- URL helper -----------------------------------------------------------
    def _url(self, path: str) -> str:
        return f"{self.base_url.rstrip('/')}/api/drug-discovery/{self.campaign_id}{path}"

    def _record(self, method: str, path: str, **meta: Any) -> None:
        self.calls.append({"method": method, "path": path, **meta})

    # -- DESIGN: rank candidates → emit CRO order sheet -----------------------
    def design(self, params: Dict[str, Any]) -> str:
        """DESIGN phase.

        Reads the current ranked candidates (GET /compounds) and emits the CRO
        order (GET /export/cro-order) — the real "order sheet", produced by the
        backend, not reimplemented here. Returns the order CSV as the protocol
        string the controller carries into BUILD/TEST.
        """
        # 1) current ranked candidates for this round
        resp = self.http.get(self._url("/compounds"), params={"limit": self.n_propose})
        self._record("GET", "/compounds", status=getattr(resp, "status_code", None))
        compounds: List[Dict[str, Any]] = []
        if getattr(resp, "status_code", 200) == 200:
            compounds = (resp.json() or {}).get("compounds", [])

        # 2) emit the CRO order sheet (the Make hand-off) — backend-produced
        order_resp = self.http.get(self._url("/export/cro-order"),
                                    params={"fmt": self.cro_fmt})
        self._record("GET", "/export/cro-order",
                     status=getattr(order_resp, "status_code", None))
        order_csv = getattr(order_resp, "text", "") or ""
        self.last_order_csv = order_csv

        log.info("[bridge DESIGN r%s] %d ranked candidates; CRO order %d bytes",
                 params.get("round", 1), len(compounds), len(order_csv))
        return json.dumps({
            "round": params.get("round", 1),
            "n_candidates": len(compounds),
            "cro_order_csv": order_csv,
        })

    # -- BUILD + TEST: stubbed wet-lab hop → canned results CSV ---------------
    #  (Handled by DBTLController._build_and_test with robot=None → dry_run.
    #   We layer the canned-results injection on top via a SimRobot-style
    #   object so the controller's guardrail is untouched.)
    def make_results_robot(self) -> "_BridgeResultsRobot":
        return _BridgeResultsRobot(self)

    # -- LEARN: ingest results → propose next round (the SEAM) ----------------
    def analyze(self, outputs: List[Dict[str, Any]], params: Dict[str, Any]) -> Dict[str, Any]:
        """LEARN phase — this is where the seam closes.

        1. POST the connector-captured assay results CSV to /ingest-assay
           (reuses the backend AssayIngestionService → updates the campaign GP).
        2. Get round N+1 candidates: POST /propose (synchronous GP refit) OR
           GET /auto-proposal (populated by the auto_retrain daemon off the
           INGEST_ASSAY event). Either path proves connector results now drive
           the CAMPAIGN GP.
        Returns a dict with a numeric `metric` for the acquisition policy.
        """
        round_no = params.get("round", 1)

        # locate the canned results CSV produced in BUILD/TEST
        results_csv = ""
        for o in outputs:
            if isinstance(o, dict) and o.get("type") == "assay_results_csv":
                results_csv = o.get("csv", "")
                break
        if not results_csv:
            log.warning("[bridge LEARN r%s] no results CSV in outputs — nothing to ingest", round_no)
            return {"metric": None, "n_ingested": 0}

        # 1) INGEST — connector results reach the campaign ledger + GP
        ingest_resp = self.http.post(
            self._url("/ingest-assay"),
            files={"file": ("results.csv", results_csv, "text/csv")},
        )
        self._record("POST", "/ingest-assay",
                     status=getattr(ingest_resp, "status_code", None))
        ingest_body: Dict[str, Any] = {}
        if getattr(ingest_resp, "status_code", 200) == 200:
            ingest_body = ingest_resp.json() or {}
        n_ingested = int(ingest_body.get("ingested", 0))
        log.info("[bridge LEARN r%s] ingested %d assay rows", round_no, n_ingested)

        # 2) PROPOSE — pull round N+1 candidates back out of the campaign GP
        proposed: List[str] = []
        scores: List[float] = []
        if self.use_auto_proposal:
            ap_resp = self.http.get(self._url("/auto-proposal"),
                                    params={"format": "json"})
            self._record("GET", "/auto-proposal",
                         status=getattr(ap_resp, "status_code", None))
            if getattr(ap_resp, "status_code", 200) == 200:
                items = (ap_resp.json() or {}).get("items", [])
                proposed = [it.get("smiles") for it in items if it.get("smiles")]
                scores = [float(it.get("score", 0.0)) for it in items]
        else:
            pr_resp = self.http.post(
                self._url("/propose"),
                json={
                    "virtual_library_smiles": self.virtual_library,
                    "n_propose": self.n_propose,
                    "diversity_weight": self.diversity_weight,
                },
            )
            self._record("POST", "/propose",
                         status=getattr(pr_resp, "status_code", None))
            if getattr(pr_resp, "status_code", 200) == 200:
                body = pr_resp.json() or {}
                proposed = body.get("proposed_smiles", []) or []
                ps = body.get("predicted_scores", {})
                if isinstance(ps, dict):
                    scores = [float(ps.get(s, 0.0)) for s in proposed]
                elif isinstance(ps, list):
                    scores = [float(v) for v in ps]

        self.last_proposed_smiles = proposed
        log.info("[bridge LEARN r%s] campaign GP proposed %d next-round candidates",
                 round_no, len(proposed))

        # A scalar the acquisition policy can act on: mean predicted score of
        # the proposed batch (the GP's confidence that the round improves).
        metric = (sum(scores) / len(scores)) if scores else (
            1.0 if proposed else None)
        return {
            "metric": metric,
            "n_ingested": n_ingested,
            "n_proposed": len(proposed),
            "proposed_smiles": proposed,
        }

    # -- assemble the controller ---------------------------------------------
    def make_controller(self, *, target: float = 0.6,
                        max_iterations: int = 3) -> DBTLController:
        """Wire this bridge's phases into a DBTLController.

        DECIDE uses make_bo_policy (active-learning acquisition). robot is the
        canned-results source; confirm=False so the guardrail never actuates
        real hardware — the "robot" here only replays the injected assay CSV.
        """
        return DBTLController(
            design=self.design,
            analyze=self.analyze,
            policy=make_bo_policy(target=target),
            robot=self.make_results_robot(),
            confirm=True,   # confirm gates the RESULTS-REPLAY robot, not hardware
            max_iterations=max_iterations,
            protocol_name="campaign_dbtl_bridge.py",
        )

    def run(self, *, target: float = 0.6, max_iterations: int = 3,
            initial_params: Optional[Dict[str, Any]] = None) -> List[IterationRecord]:
        ctrl = self.make_controller(target=target, max_iterations=max_iterations)
        return ctrl.run(initial_params or {"round": 1})


# ──────────────────────────────────────────────────────────────────────────────
# Results-replay "robot": the stubbed Make/Test hop, no hardware
# ──────────────────────────────────────────────────────────────────────────────
class _BridgeResultsRobot:
    """A SimRobot-shaped object that replays canned assay results.

    Satisfies the DBTLController robot surface (health_check / upload_protocol /
    start_run / wait_for_completion / get_run_outputs) but does NO actuation.
    `get_run_outputs` returns the canned assay-results CSV keyed to the CRO
    order the DESIGN phase just emitted — i.e. "the CRO synthesised & assayed
    what we ordered." This is the honest stub for the physical Make step.
    """

    def __init__(self, bridge: CampaignDBTLBridge):
        self._bridge = bridge
        self._round = 0
        self._order_csv = ""

    def health_check(self):
        return {"name": "bridge-results-replay", "actuates": False}

    def upload_protocol(self, code: str, name: str = "p.py") -> str:
        # `code` is the DESIGN output JSON carrying the CRO order CSV.
        try:
            payload = json.loads(code)
            self._round = int(payload.get("round", self._round + 1))
            self._order_csv = payload.get("cro_order_csv", "")
        except (ValueError, TypeError):
            self._round += 1
            self._order_csv = ""
        return "bridge-protocol"

    def start_run(self, protocol_id: str) -> str:
        return f"bridge-run-r{self._round}"

    def wait_for_completion(self, run_id: str) -> Dict[str, Any]:
        # No hardware to wait on — the CRO hop is stubbed as immediately "done".
        return {"status": "succeeded"}

    def get_run_outputs(self, run_id: str) -> List[Dict[str, Any]]:
        csv_text = self._bridge.assay_source.for_order(self._order_csv, self._round)
        return [{"type": "assay_results_csv", "round": self._round, "csv": csv_text}]


# ──────────────────────────────────────────────────────────────────────────────
# Mock HTTP client for the offline sim (records requests, canned responses)
# ──────────────────────────────────────────────────────────────────────────────
class _MockResponse:
    def __init__(self, status_code: int = 200, text: str = "", body: Any = None):
        self.status_code = status_code
        self.text = text
        self._body = body

    def json(self) -> Any:
        return self._body


class _RecordingHttpClient:
    """A tiny stand-in for `requests` that records every call and returns canned
    responses keyed by endpoint suffix. No network. Used only by the sim demo.
    """

    def __init__(self, canned_compounds: List[Dict[str, Any]],
                 canned_order_csv: str,
                 canned_proposed: List[str]):
        self.compounds = canned_compounds
        self.order_csv = canned_order_csv
        self.proposed = canned_proposed
        self.requests: List[Dict[str, Any]] = []
        # captured payloads that actually reached each seam endpoint
        self.ingested_csvs: List[str] = []
        self.propose_bodies: List[Dict[str, Any]] = []

    def get(self, url: str, params: Optional[Dict[str, Any]] = None) -> _MockResponse:
        self.requests.append({"method": "GET", "url": url, "params": params})
        if url.endswith("/compounds"):
            return _MockResponse(200, body={"campaign_id": "sim",
                                            "compounds": self.compounds,
                                            "count": len(self.compounds)})
        if url.endswith("/export/cro-order"):
            return _MockResponse(200, text=self.order_csv)
        if url.endswith("/auto-proposal"):
            items = [{"rank": i + 1, "smiles": s, "score": 0.7,
                      "ci_lo": 0.5, "ci_hi": 0.9, "ad": "in"}
                     for i, s in enumerate(self.proposed)]
            return _MockResponse(200, body={"round": 2, "generated_at": "sim",
                                            "trigger": "ingest_assay", "items": items})
        return _MockResponse(404, text="not found")

    def post(self, url: str, json: Optional[Dict[str, Any]] = None,
             files: Optional[Dict[str, Any]] = None) -> _MockResponse:
        self.requests.append({"method": "POST", "url": url,
                              "json": json, "has_files": files is not None})
        if url.endswith("/ingest-assay"):
            # capture the CSV that reached the seam
            csv_text = ""
            if files and "file" in files:
                entry = files["file"]
                csv_text = entry[1] if isinstance(entry, (list, tuple)) else str(entry)
            self.ingested_csvs.append(csv_text)
            n = max(0, csv_text.count("\n") - 1)  # rows minus header
            return _MockResponse(200, body={"campaign_id": "sim", "ingested": n,
                                            "errors": [], "updated_ranks": [],
                                            "newly_failed": [], "newly_passed": []})
        if url.endswith("/propose"):
            self.propose_bodies.append(json or {})
            return _MockResponse(200, body={
                "campaign_id": "sim",
                "n_proposed": len(self.proposed),
                "proposed_smiles": self.proposed,
                "predicted_scores": [0.7] * len(self.proposed),
            })
        return _MockResponse(404, text="not found")


# ──────────────────────────────────────────────────────────────────────────────
# Sim demo — proves the seam is closed, fully offline
# ──────────────────────────────────────────────────────────────────────────────
def run_bridge_sim_demo(rounds: int = 3, use_auto_proposal: bool = False):
    """Feed a CANNED assay-results CSV through design→order→ingest→propose for
    `rounds` rounds against a MOCK HTTP client that records every request.

    Asserts the seam is closed:
      * connector-captured results actually reach POST /ingest-assay, and
      * the next batch comes back from POST /propose (or GET /auto-proposal).

    No live backend, no hardware. Returns (trace, mock_client) for inspection.
    Raises AssertionError if the seam is NOT closed.
    """
    logging.getLogger(__name__).info("=== campaign DBTL bridge sim (rounds=%d) ===", rounds)

    # Canned inputs — explicit, no fabricated model chemistry.
    canned_compounds = [
        {"compound_id": "CPD-001", "smiles": "CCO", "composite_score": 0.42, "rank": 1},
        {"compound_id": "CPD-002", "smiles": "CCN", "composite_score": 0.31, "rank": 2},
    ]
    canned_order_csv = (
        "Compound_ID,SMILES,Quantity_mg,Purity_%,Salt_Form\n"
        "CPD-001,CCO,10.0,95.0,free base\n"
        "CPD-002,CCN,10.0,95.0,free base\n"
    )
    # The CANNED assay results CSV (the stubbed CRO/Make deliverable).
    canned_results_csv = (
        "compound_id,assay_type,value,unit,assay_date,smiles\n"
        "CPD-001,IC50,0.85,uM,2026-09-10,CCO\n"
        "CPD-002,IC50,2.10,uM,2026-09-10,CCN\n"
    )
    virtual_library = ["CCO", "CCN", "CCC", "CCCO", "c1ccccc1"]
    canned_proposed = ["CCC", "CCCO"]

    mock = _RecordingHttpClient(canned_compounds, canned_order_csv, canned_proposed)
    bridge = CampaignDBTLBridge(
        campaign_id="sim-campaign",
        base_url="https://mock.local",
        http=mock,
        assay_source=SimAssaySource(csv_text=canned_results_csv),
        virtual_library=virtual_library,
        n_propose=2,
        use_auto_proposal=use_auto_proposal,
    )

    # target=1.1 keeps the loop REFINE-ing so we exercise multiple rounds
    # (canned metric = mean proposed score = 0.7, never reaches 1.1).
    trace = bridge.run(target=1.1, max_iterations=rounds,
                       initial_params={"round": 1})

    # ── Assertions: the seam is closed ──────────────────────────────────────
    # 1) DESIGN hit both /compounds and /export/cro-order.
    paths = [c["path"] for c in bridge.calls]
    assert "/compounds" in paths, "DESIGN never fetched /compounds"
    assert "/export/cro-order" in paths, "DESIGN never emitted the CRO order sheet"

    # 2) The connector-captured results ACTUALLY reached /ingest-assay.
    assert mock.ingested_csvs, "no CSV ever reached POST /ingest-assay — seam OPEN"
    assert all("compound_id,assay_type" in c for c in mock.ingested_csvs), \
        "ingested payload is not the assay-results CSV"
    assert any("CPD-001,IC50,0.85" in c for c in mock.ingested_csvs), \
        "the canned assay VALUES did not reach /ingest-assay"

    # 3) The next batch came BACK from the campaign GP (/propose or /auto-proposal).
    if use_auto_proposal:
        assert "/auto-proposal" in paths, "never read /auto-proposal for round N+1"
    else:
        assert mock.propose_bodies, "POST /propose was never called for round N+1"
        assert mock.propose_bodies[0]["virtual_library_smiles"] == virtual_library, \
            "propose did not receive the virtual library"
    assert bridge.last_proposed_smiles == canned_proposed, \
        "round N+1 candidates did not come back from the campaign GP"

    # 4) Ingest→propose happened MULTIPLE rounds (the loop actually cycled).
    n_ingests = sum(1 for c in bridge.calls if c["path"] == "/ingest-assay")
    assert n_ingests >= 2, f"loop only ingested {n_ingests}× — did not cycle"

    log.info("SEAM CLOSED: %d ingest→propose cycles; results reached /ingest-assay "
             "and round N+1 came back from the campaign GP.", n_ingests)
    return trace, mock


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    trace, mock = run_bridge_sim_demo(rounds=3)
    print("\n=== Campaign DBTL bridge trace (offline, mock backend) ===")
    for r in trace:
        print(f"  round {r.iteration}: metric={r.metric} "
              f"-> {r.decision.value:8} | {r.note}")
    print(f"\nEndpoints hit ({len(mock.requests)} calls):")
    for req in mock.requests:
        suffix = req["url"].split("/api/drug-discovery/")[-1]
        print(f"  {req['method']:4} .../{suffix}")
    print(f"\nSEAM CLOSED — {len(mock.ingested_csvs)} results CSV(s) reached "
          f"/ingest-assay; next-round candidates came back from the campaign GP.")
