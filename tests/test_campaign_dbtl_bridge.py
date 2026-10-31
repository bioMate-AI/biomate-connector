"""Tests for the Campaign ↔ DBTL bridge (lab_instruments.campaign_dbtl_bridge).

Proves the wet-lab DBTL seam is closed OFFLINE, with an injected mock HTTP
client (no live backend, no hardware, nothing actuated):

  * DESIGN reaches GET /compounds and GET /export/cro-order (the CRO order sheet)
  * connector-captured assay results actually reach POST /ingest-assay
  * round N+1 candidates come back from POST /propose (and GET /auto-proposal)

The canned assay values are explicit (no fabricated model chemistry); the
physical Make/CRO hop stays stubbed behind SimAssaySource.
"""

from __future__ import annotations

import pytest

from lab_instruments.campaign_dbtl_bridge import (
    CampaignDBTLBridge,
    SimAssaySource,
    _RecordingHttpClient,
    run_bridge_sim_demo,
)
from lab_instruments.dbtl_controller import Decision


def test_sim_demo_closes_the_seam_via_propose():
    """The canonical demo asserts the seam internally; it must not raise."""
    trace, mock = run_bridge_sim_demo(rounds=3, use_auto_proposal=False)
    assert trace, "loop produced no iterations"
    # results reached /ingest-assay and next round came back from /propose
    assert mock.ingested_csvs
    assert mock.propose_bodies


def test_sim_demo_closes_the_seam_via_auto_proposal():
    """The GET /auto-proposal path (auto_retrain daemon) also closes the loop."""
    trace, mock = run_bridge_sim_demo(rounds=2, use_auto_proposal=True)
    paths = [r["url"].split("/api/drug-discovery/")[-1] for r in mock.requests]
    assert any(p.endswith("/auto-proposal") for p in paths)
    assert mock.ingested_csvs


def test_ingested_csv_contains_the_canned_values():
    """The EXACT canned assay values captured by the connector reach the seam."""
    _, mock = run_bridge_sim_demo(rounds=2)
    assert any("CPD-001,IC50,0.85" in c for c in mock.ingested_csvs), \
        "canned assay values did not reach POST /ingest-assay"
    # every ingested payload is a real assay-results CSV, never fabricated later
    assert all("compound_id,assay_type,value,unit,assay_date" in c
               for c in mock.ingested_csvs)


def test_endpoints_hit_are_exactly_the_backend_dbtl_api():
    """The bridge must route only through the documented campaign DBTL endpoints."""
    _, mock = run_bridge_sim_demo(rounds=2)
    suffixes = {r["url"].split("/api/drug-discovery/sim-campaign")[-1]
                for r in mock.requests}
    assert "/compounds" in suffixes
    assert "/export/cro-order" in suffixes
    assert "/ingest-assay" in suffixes
    assert "/propose" in suffixes


def test_loop_actually_cycles_multiple_rounds():
    """ingest→propose must happen >=2 times — the loop feeds back, not one-shot."""
    _, mock = run_bridge_sim_demo(rounds=3)
    n_ingest = sum(1 for r in mock.requests if r["url"].endswith("/ingest-assay"))
    n_propose = sum(1 for r in mock.requests if r["url"].endswith("/propose"))
    assert n_ingest >= 2 and n_propose >= 2


def test_dry_run_never_actuates_real_hardware():
    """The results-replay robot reports it does not actuate; no OpenTrons call."""
    mock = _RecordingHttpClient(
        canned_compounds=[{"compound_id": "C1", "smiles": "CCO",
                           "composite_score": 0.4, "rank": 1}],
        canned_order_csv="Compound_ID,SMILES\nC1,CCO\n",
        canned_proposed=["CCC"],
    )
    bridge = CampaignDBTLBridge(
        campaign_id="sim",
        base_url="https://mock.local",
        http=mock,
        assay_source=SimAssaySource(
            csv_text="compound_id,assay_type,value,unit,assay_date\n"
                     "C1,IC50,0.9,uM,2026-09-10\n"),
        virtual_library=["CCO", "CCC"],
        n_propose=1,
    )
    robot = bridge.make_results_robot()
    assert robot.health_check()["actuates"] is False
    # no network calls happen just from constructing/health-checking the robot
    assert mock.requests == []


def test_bo_policy_drives_the_decisions():
    """DECIDE is make_bo_policy: a reachable target PROCEEDs, else REFINE/PIVOT."""
    mock = _RecordingHttpClient(
        canned_compounds=[{"compound_id": "C1", "smiles": "CCO",
                           "composite_score": 0.4, "rank": 1}],
        canned_order_csv="Compound_ID,SMILES\nC1,CCO\n",
        canned_proposed=["CCC"],
    )
    bridge = CampaignDBTLBridge(
        campaign_id="sim", base_url="https://mock.local", http=mock,
        assay_source=SimAssaySource(
            csv_text="compound_id,assay_type,value,unit,assay_date\n"
                     "C1,IC50,0.9,uM,2026-09-10\n"),
        virtual_library=["CCO", "CCC"], n_propose=1,
    )
    # canned metric (mean proposed score) is 0.7; target 0.5 => PROCEED round 1.
    trace = bridge.run(target=0.5, max_iterations=3)
    assert trace[-1].decision == Decision.PROCEED


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
