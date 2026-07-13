"""Tests for local instrument dispatch (agent↔instrument edge) and the DBTL loop.

Covers the guardrail contract for `run_instrument_protocol` — that a dry-run
NEVER actuates hardware — plus protocol compilation, error handling, and the
DBTL controller's PROCEED / REFINE / PIVOT decisions. No real robot required.
"""

from __future__ import annotations

import pytest

from mcp import biomate_mcp_server as srv
from lab_instruments.opentrons_connector import OpenTronsConnector


class FakeOT:
    """Stand-in for OpenTronsConnector that records actuation calls instead of
    hitting the network. Keeps the real protocol generator so compilation is
    exercised for real."""

    calls: list = []

    def __init__(self, robot_ip):
        self.robot_ip = robot_ip

    # real static generator — we want to test the actual compiled protocol
    generate_plate_transfer_protocol = staticmethod(
        OpenTronsConnector.generate_plate_transfer_protocol
    )

    @classmethod
    def discover(cls, ips=None):
        return []

    def health_check(self):
        FakeOT.calls.append("health_check")
        return {"name": "fake-ot2"}

    def upload_protocol(self, code, name="p.py"):
        FakeOT.calls.append("upload_protocol")
        return "fake-protocol-id"

    def start_run(self, protocol_id):
        FakeOT.calls.append("start_run")
        return "fake-run-id"

    def get_run_status(self, run_id):
        return {"run_id": run_id, "status": "running"}


@pytest.fixture(autouse=True)
def _patch_loader(monkeypatch):
    FakeOT.calls = []
    monkeypatch.setattr(srv, "_load_opentrons", lambda: FakeOT)
    yield


TRANSFER_ARGS = {
    "robot_ip": "192.168.1.42",
    "source_labware": "corning_96_wellplate_360ul_flat",
    "dest_labware": "corning_96_wellplate_360ul_flat",
    "transfers": [{"from_well": "A1", "to_well": "A1", "volume_ul": 100}],
}


def test_dry_run_never_actuates():
    res = srv.dispatch_instrument_tool("run_instrument_protocol", dict(TRANSFER_ARGS))
    assert res["dry_run"] is True
    assert "from opentrons import protocol_api" in res["protocol_preview"]
    assert "transfer(100" in res["protocol_preview"]
    # The guardrail: NO hardware calls on a dry-run.
    assert FakeOT.calls == []


def test_confirm_true_actuates():
    args = dict(TRANSFER_ARGS, confirm=True)
    res = srv.dispatch_instrument_tool("run_instrument_protocol", args)
    assert res["run_id"] == "fake-run-id"
    assert res["status"] == "running"
    # reachability check + upload + start all happened, in order
    assert FakeOT.calls == ["health_check", "upload_protocol", "start_run"]


def test_transfers_require_labware():
    args = {"robot_ip": "1.2.3.4", "transfers": TRANSFER_ARGS["transfers"]}
    with pytest.raises(ValueError, match="source_labware"):
        srv.dispatch_instrument_tool("run_instrument_protocol", args)


def test_no_protocol_and_no_transfers_errors():
    with pytest.raises(ValueError, match="protocol_code"):
        srv.dispatch_instrument_tool("run_instrument_protocol", {"robot_ip": "1.2.3.4"})


def test_missing_robot_ip_errors():
    with pytest.raises(ValueError, match="robot_ip"):
        srv.dispatch_instrument_tool("run_instrument_protocol", {"transfers": []})


def test_list_instruments_empty_without_robots():
    res = srv.dispatch_instrument_tool("list_instruments", {})
    assert res == {"instruments": [], "count": 0}


def test_get_instrument_status():
    res = srv.dispatch_instrument_tool(
        "get_instrument_status", {"robot_ip": "1.2.3.4", "run_id": "r1"}
    )
    assert res["status"] == "running"


# ── DBTL controller ───────────────────────────────────────────────────────────

def test_dbtl_sim_converges_to_proceed():
    from lab_instruments.dbtl_controller import run_sim_demo, Decision
    trace = run_sim_demo()
    assert trace[-1].decision == Decision.PROCEED
    # metric increases monotonically as volume is refined upward
    metrics = [r.metric for r in trace]
    assert metrics == sorted(metrics)


def test_dbtl_pivots_when_stalled():
    from lab_instruments.dbtl_controller import (
        DBTLController, make_opentrons_design, threshold_policy, Decision,
    )

    def stalled(outputs, params):
        return {"metric": 0.5}  # flat — target never reached

    ctrl = DBTLController(
        design=make_opentrons_design("corning_96_wellplate_360ul_flat",
                                     "corning_96_wellplate_360ul_flat"),
        analyze=stalled,
        policy=threshold_policy(target=0.8, step_key="stim_volume_ul", step=40.0,
                                max_no_improve=2),
        max_iterations=8,
    )
    trace = ctrl.run({"stim_volume_ul": 20.0})
    assert trace[-1].decision == Decision.PIVOT
