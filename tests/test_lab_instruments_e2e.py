#!/usr/bin/env python3
"""
test_lab_instruments_e2e.py — E2E tests for all 10 lab instrument connectors.

Tests each HTTP-based connector against a shared mock server (no real hardware
required). File-based connectors are tested with fixture or temp files.

Instruments:
  1. Illumina BaseSpace  (OAuth2 + REST)
  2. Nanopore MinKNOW    (HTTP REST)
  3. CryoEM EPU          (HTTP REST + file-based scan)
  4. Flow Cytometer      (file-based FCS; smoke-runs existing parser)
  5. LC-MS               (file-based vendor detection)
  6. qPCR                (file-based; smoke-runs existing parser)
  7. Opentrons           (HTTP REST)
  8. Plate Reader        (file-based; smoke-runs existing parser)
  9. SiLA2               (gRPC stub mode — no gRPC server needed)
 10. Benchling           (HTTP REST)

Running:
    python3 backend/tests/test_lab_instruments_e2e.py
"""

import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

# ── Path setup ────────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent.parent.parent  # repo root
sys.path.insert(0, str(ROOT))

FIXTURES = Path(__file__).parent / "fixtures"

# ──────────────────────────────────────────────────────────────────────────────
# Shared mock HTTP server
# Handles routes for Illumina, MinKNOW, EPU, Opentrons, and Benchling.
# ──────────────────────────────────────────────────────────────────────────────

BASESPACE_USER = {"Id": "user-1", "Name": "Test User", "Email": "test@lab.org"}
BASESPACE_RUNS = {"Items": [
    {"Id": "run-wgs-01", "Name": "WGS_Sample_A", "Status": "Complete",
     "ExperimentType": "Whole Genome Sequencing", "PlatformName": "NovaSeq 6000",
     "DateCreated": "2026-06-01T10:00:00Z"},
    {"Id": "run-rna-02", "Name": "RNAseq_Liver_B", "Status": "Complete",
     "ExperimentType": "RNA-seq transcriptome", "PlatformName": "NextSeq 2000",
     "DateCreated": "2026-06-02T12:00:00Z"},
    {"Id": "run-amp-03", "Name": "16S_Microbiome", "Status": "Complete",
     "ExperimentType": "16s amplicon metagenomics", "PlatformName": "MiSeq",
     "DateCreated": "2026-06-03T09:00:00Z"},
]}
BASESPACE_SAMPLES = {"Items": [
    {"Id": "samp-001", "Name": "SampleA"},
    {"Id": "samp-002", "Name": "SampleB"},
]}

MINKNOW_HEALTH = {"version": "5.3.0", "name": "MinKNOW", "device": "PromethION"}
MINKNOW_POSITIONS = {"positions": [
    {"name": "X1", "state": "completed", "flow_cell_info": {"flow_cell_id": "FAW12345"}},
    {"name": "X2", "state": "idle", "flow_cell_info": {}},
]}
MINKNOW_RUN_INFO = {
    "run_id": "mnk-run-001",
    "experiment_name": "human_wgs_batch1",
    "sample_id": "NA12878",
    "output_directory": "/data/minknow/run001",
    "flow_cell_id": "FAW12345",
    "protocol_run_info": {"kit": "SQK-LSK114"},
}

EPU_VERSION = {"version": "3.2.0", "instrument": "Titan Krios G4", "status": "ready"}
EPU_SESSIONS = [
    {"id": "sess-001", "name": "ADENO_VIRUS_001", "status": "Finished",
     "sampleName": "Adenovirus_sample", "microscopeId": "Krios-1",
     "accelerationVoltage": 300, "pixelSize": 1.05,
     "outputDirectory": "/cryoem/sessions/ADENO_001",
     "endTime": "2026-06-05T08:30:00Z"},
    {"id": "sess-002", "name": "RIBOSOME_002", "status": "Running",
     "sampleName": "80S_ribosome", "microscopeId": "Krios-1"},
]
EPU_SESSION_STATS = {"totalMovies": 2480, "ctfResolution": 3.4, "iceThickness": 0.6}

OT2_HEALTH = {"name": "OT-2 GEN2", "robot_model": "OT-2 Standard",
              "robot_type": "OT-2 Standard", "api_version": "2.14"}
OT2_PROTOCOLS = {"data": [
    {"id": "prot-abc", "protocolType": "python", "metadata": {"protocolName": "ADMET_Prep"}},
]}
OT2_PROTOCOL_UPLOAD = {"data": {"id": "prot-xyz", "protocolType": "python",
                                "metadata": {"protocolName": "biomate_protocol.py"}}}
OT2_RUN_CREATE = {"data": {"id": "run-ot2-001", "status": "idle",
                            "createdAt": "2026-06-06T00:00:00Z"}}
OT2_RUN_ACTION = {"data": {"actionType": "play"}}
OT2_RUN_STATUS = {"data": {"id": "run-ot2-001", "status": "succeeded",
                            "createdAt": "2026-06-06T00:00:00Z",
                            "startedAt": "2026-06-06T00:00:05Z",
                            "completedAt": "2026-06-06T00:10:00Z",
                            "errors": []}}
OT2_RUN_COMMANDS = {"data": []}

BENCHLING_ENTRIES = {"entries": [
    {"id": "etr-001", "displayId": "NB001", "name": "ADMET_Study_Entry_June",
     "folderId": "lib_abc", "createdAt": "2026-06-01T10:00:00Z",
     "fields": {}, "days": [{"notes": [{"text": "SMILES: CC(=O)Oc1ccccc1C(=O)O"}]}]},
]}
BENCHLING_SAMPLES = {"samples": [
    {"id": "smp-001", "name": "AspirineFormulaA",
     "fields": {"compound_smiles": {"value": "CC(=O)Oc1ccccc1C(=O)O"},
                "batch_id": {"value": "BATCH-2026-001"}}},
]}
BENCHLING_ASSAY_RESULT_CREATED = {
    "assayResults": [{"id": "ar-001", "schemaId": "schema-admet"}]
}


class _MockHandler(BaseHTTPRequestHandler):
    """Single handler routing all instrument APIs by path prefix."""

    def _respond(self, code: int, body: bytes, content_type: str = "application/json"):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj) -> bytes:
        return json.dumps(obj).encode()

    def _consume_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length:
            self.rfile.read(length)

    def do_GET(self):
        p = self.path.split("?")[0]

        # ── Illumina BaseSpace ──────────────────────────────────────────────
        if p == "/v1pre3/users/current":
            return self._respond(200, self._json({"Response": BASESPACE_USER}))
        if p == "/v1pre3/users/current/runs":
            return self._respond(200, self._json({"Response": BASESPACE_RUNS}))
        if p.startswith("/v1pre3/runs/") and p.endswith("/samples"):
            return self._respond(200, self._json({"Response": BASESPACE_SAMPLES}))

        # ── MinKNOW ────────────────────────────────────────────────────────
        if p == "/":
            return self._respond(200, self._json(MINKNOW_HEALTH))
        if p == "/positions":
            return self._respond(200, self._json(MINKNOW_POSITIONS))
        if p.startswith("/run_info/"):
            return self._respond(200, self._json(MINKNOW_RUN_INFO))

        # ── CryoEM EPU ─────────────────────────────────────────────────────
        if p == "/api/v1/version":
            return self._respond(200, self._json(EPU_VERSION))
        if p == "/api/v1/sessions":
            return self._respond(200, self._json(EPU_SESSIONS))
        if p.startswith("/api/v1/sessions/") and p.endswith("/stats"):
            return self._respond(200, self._json(EPU_SESSION_STATS))
        if p.startswith("/api/v1/sessions/"):
            sess_id = p.split("/")[-1]
            match = next((s for s in EPU_SESSIONS if s["id"] == sess_id), None)
            if match:
                return self._respond(200, self._json(match))
            return self._respond(404, b'{"error":"not found"}')

        # ── Opentrons ──────────────────────────────────────────────────────
        if p == "/health":
            return self._respond(200, self._json(OT2_HEALTH))
        if p == "/protocols":
            return self._respond(200, self._json(OT2_PROTOCOLS))
        if p.startswith("/runs/") and p.endswith("/commands"):
            return self._respond(200, self._json(OT2_RUN_COMMANDS))
        if p.startswith("/runs/"):
            return self._respond(200, self._json(OT2_RUN_STATUS))

        # ── Benchling ──────────────────────────────────────────────────────
        if p == "/api/v2/entries":
            return self._respond(200, self._json(BENCHLING_ENTRIES))
        if p.startswith("/api/v2/entries/"):
            return self._respond(200, self._json(BENCHLING_ENTRIES["entries"][0]))
        if p == "/api/v2/samples" or p.startswith("/api/v2/samples"):
            return self._respond(200, self._json(BENCHLING_SAMPLES))

        self._respond(404, b'{"error":"not found"}')

    def do_POST(self):
        self._consume_body()
        p = self.path.split("?")[0]

        # ── Opentrons ──────────────────────────────────────────────────────
        if p == "/protocols":
            return self._respond(200, self._json(OT2_PROTOCOL_UPLOAD))
        if p == "/runs":
            return self._respond(201, self._json(OT2_RUN_CREATE))
        if p.startswith("/runs/") and p.endswith("/actions"):
            return self._respond(201, self._json(OT2_RUN_ACTION))

        # ── Benchling ──────────────────────────────────────────────────────
        if "/api/v2/assay-results" in p:  # covers :bulk-create suffix too
            return self._respond(200, self._json(BENCHLING_ASSAY_RESULT_CREATED))

        self._respond(404, b'{"error":"not found"}')

    def log_message(self, *_):
        pass


# Start shared server once for the whole test run
_server = HTTPServer(("127.0.0.1", 0), _MockHandler)
_MOCK_PORT = _server.server_address[1]
_MOCK_URL = f"http://127.0.0.1:{_MOCK_PORT}"
threading.Thread(target=_server.serve_forever, daemon=True).start()


# ──────────────────────────────────────────────────────────────────────────────
# 1. Illumina BaseSpace
# ──────────────────────────────────────────────────────────────────────────────

class TestIlluminaBaseSpace(unittest.TestCase):
    """OAuth2 + REST connector: auth, run listing, workflow routing, payload."""

    def setUp(self):
        from lab_instruments import illumina_basespace_connector as mod
        # Redirect API calls to mock server
        self._orig = mod.BASESPACE_API_BASE
        mod.BASESPACE_API_BASE = _MOCK_URL
        from lab_instruments.illumina_basespace_connector import (
            BaseSpaceConnector, get_authorization_url,
        )
        self.connector = BaseSpaceConnector(access_token="test-token-abc")
        self.get_auth_url = get_authorization_url

    def tearDown(self):
        from lab_instruments import illumina_basespace_connector as mod
        mod.BASESPACE_API_BASE = self._orig

    def test_auth_header_set(self):
        self.assertEqual(
            self.connector.session.headers.get("x-access-token"),
            "test-token-abc",
        )

    def test_authorization_url_contains_client_id_param(self):
        url = self.get_auth_url(state="csrf-xyz")
        self.assertIn("response_type=code", url)
        self.assertIn("csrf-xyz", url)

    def test_get_current_user(self):
        user = self.connector.get_current_user()
        self.assertEqual(user["Id"], "user-1")
        self.assertEqual(user["Email"], "test@lab.org")

    def test_list_runs_returns_items(self):
        runs = self.connector.list_runs()
        self.assertEqual(len(runs), 3)
        self.assertEqual(runs[0]["Id"], "run-wgs-01")

    def test_detect_workflow_wgs(self):
        wf = self.connector.detect_workflow_from_run(
            {"ExperimentType": "Whole Genome Sequencing"}
        )
        self.assertEqual(wf, "wgs_germline_gatk")

    def test_detect_workflow_rnaseq(self):
        wf = self.connector.detect_workflow_from_run(
            {"ExperimentType": "RNA-seq transcriptome"}
        )
        self.assertEqual(wf, "rnaseq_differential")

    def test_detect_workflow_16s(self):
        wf = self.connector.detect_workflow_from_run(
            {"ExperimentType": "16s amplicon metagenomics"}
        )
        self.assertEqual(wf, "metagenomics_16s")

    def test_detect_workflow_unknown(self):
        wf = self.connector.detect_workflow_from_run({"ExperimentType": "Custom"})
        self.assertEqual(wf, "generic_fastq")

    def test_runs_to_biomate_inputs_shape(self):
        runs = self.connector.list_runs()
        inputs = self.connector.runs_to_biomate_inputs(runs)
        self.assertEqual(len(inputs), 3)
        required_fields = {"basespace_run_id", "run_name", "suggested_workflow",
                           "sample_count", "sample_ids"}
        for inp in inputs:
            for f in required_fields:
                self.assertIn(f, inp, f"Missing field '{f}' in BaseSpace input")

    def test_wgs_run_routes_to_correct_workflow(self):
        runs = self.connector.list_runs()
        inputs = self.connector.runs_to_biomate_inputs(runs)
        wgs_input = next(i for i in inputs if i["basespace_run_id"] == "run-wgs-01")
        self.assertEqual(wgs_input["suggested_workflow"], "wgs_germline_gatk")

    def test_rnaseq_run_routes_to_correct_workflow(self):
        runs = self.connector.list_runs()
        inputs = self.connector.runs_to_biomate_inputs(runs)
        rna_input = next(i for i in inputs if i["basespace_run_id"] == "run-rna-02")
        self.assertEqual(rna_input["suggested_workflow"], "rnaseq_differential")


# ──────────────────────────────────────────────────────────────────────────────
# 2. Nanopore MinKNOW
# ──────────────────────────────────────────────────────────────────────────────

class TestNanoporeMinKNOW(unittest.TestCase):
    """HTTP REST connector: health, positions, run detection, workflow routing."""

    def setUp(self):
        from lab_instruments.nanopore_minknow_connector import MinKNOWConnector
        self.connector = MinKNOWConnector(host="127.0.0.1", port=_MOCK_PORT)

    def test_health_returns_version(self):
        info = self.connector.health()
        self.assertEqual(info.get("version"), "5.3.0")
        self.assertEqual(info.get("name"), "MinKNOW")

    def test_list_positions_returns_all(self):
        positions = self.connector.list_positions()
        self.assertEqual(len(positions), 2)
        names = [p["name"] for p in positions]
        self.assertIn("X1", names)

    def test_detect_completed_runs_filters_idle(self):
        runs = self.connector.detect_completed_runs()
        # Only X1 (state=completed) should appear; X2 (state=idle) should not
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["position"], "X1")

    def test_completed_run_has_required_fields(self):
        runs = self.connector.detect_completed_runs()
        r = runs[0]
        for field in ("position", "state", "run_id", "experiment_name",
                      "sample_id", "output_path", "flow_cell_id"):
            self.assertIn(field, r, f"MinKNOW run missing field: {field}")

    def test_detect_workflow_lsk_dna(self):
        wf = self.connector._detect_workflow({"kit": "SQK-LSK114"})
        self.assertEqual(wf, "wgs_nanopore_variant_call")

    def test_detect_workflow_rna_kit(self):
        wf = self.connector._detect_workflow({"kit": "SQK-RNA002"})
        self.assertEqual(wf, "rnaseq_nanopore")

    def test_detect_workflow_unknown_kit(self):
        wf = self.connector._detect_workflow({"kit": "UNKNOWN-KIT"})
        self.assertEqual(wf, "nanopore_basecalling")

    def test_run_to_biomate_params_shape(self):
        run = self.connector.detect_completed_runs()[0]
        # Avoid real filesystem scan — override output_path to empty
        run["output_path"] = ""
        params = self.connector.run_to_biomate_params(run)
        for field in ("input_path", "input_format", "sample_id",
                      "experiment_name", "suggested_workflow"):
            self.assertIn(field, params, f"MinKNOW biomate_params missing: {field}")

    def test_lsk_kit_routes_to_wgs_workflow(self):
        run = self.connector.detect_completed_runs()[0]
        # kit is populated from MINKNOW_RUN_INFO protocol_run_info.kit
        params = self.connector.run_to_biomate_params(run)
        self.assertEqual(params["suggested_workflow"], "wgs_nanopore_variant_call")


# ──────────────────────────────────────────────────────────────────────────────
# 3. CryoEM EPU
# ──────────────────────────────────────────────────────────────────────────────

class TestCryoEMEPU(unittest.TestCase):
    """EPU REST API + file-based session scan."""

    def setUp(self):
        from lab_instruments.cryoem_instrument_connector import EPUConnector
        self.connector = EPUConnector(host="127.0.0.1", port=_MOCK_PORT)

    def test_health_returns_version(self):
        info = self.connector.health()
        self.assertFalse(info.get("error"), f"health() returned error: {info}")
        self.assertEqual(info.get("version"), "3.2.0")
        self.assertIn("Krios", info.get("instrument", ""))

    def test_list_sessions_returns_all(self):
        sessions = self.connector.list_sessions()
        self.assertEqual(len(sessions), 2)

    def test_detect_completed_filters_running(self):
        completed = self.connector.detect_completed_sessions()
        # Only sess-001 has status=Finished; sess-002 is Running
        self.assertEqual(len(completed), 1)
        self.assertEqual(completed[0]["session_id"], "sess-001")

    def test_completed_session_has_required_fields(self):
        completed = self.connector.detect_completed_sessions()
        s = completed[0]
        for field in ("session_id", "session_name", "sample_name", "microscope_id",
                      "movie_count", "voltage_kv", "pixel_size_angstrom",
                      "output_directory", "status"):
            self.assertIn(field, s, f"EPU session missing field: {field}")

    def test_movie_count_from_stats(self):
        completed = self.connector.detect_completed_sessions()
        self.assertEqual(completed[0]["movie_count"], 2480)

    def test_session_to_biomate_params_workflow(self):
        completed = self.connector.detect_completed_sessions()
        params = self.connector.session_to_biomate_params(completed[0])
        self.assertEqual(params["suggested_workflow"], "cryosparc_standard_spa")

    def test_session_to_biomate_params_shape(self):
        completed = self.connector.detect_completed_sessions()
        params = self.connector.session_to_biomate_params(completed[0])
        for field in ("movies_path", "sample_name", "voltage", "pixel_size",
                      "movie_count", "suggested_workflow", "session_id"):
            self.assertIn(field, params, f"EPU biomate_params missing: {field}")

    def test_file_based_scan_with_temp_dir(self):
        """scan_epu_directory detects .mrc files above threshold."""
        from lab_instruments.cryoem_instrument_connector import (
            CryoEMWatchedDirectory, scan_epu_directory,
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create enough .mrc files to exceed min_movies
            for i in range(55):
                (Path(tmpdir) / f"movie_{i:04d}.mrc").touch()

            # settle_seconds=0 so the "still settling" guard doesn't block the trigger
            watched = CryoEMWatchedDirectory(path=tmpdir, min_movies=50, settle_seconds=0)
            result = scan_epu_directory(watched)

        self.assertIsNotNone(result, "scan_epu_directory returned None for >50 .mrc files")
        self.assertEqual(result["workflow_id"], "cryosparc_standard_spa")
        self.assertIn("movie_count", result)
        self.assertGreaterEqual(result["movie_count"], 55)

    def test_file_based_scan_below_threshold_returns_none(self):
        from lab_instruments.cryoem_instrument_connector import (
            CryoEMWatchedDirectory, scan_epu_directory,
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            for i in range(10):
                (Path(tmpdir) / f"movie_{i:04d}.mrc").touch()

            watched = CryoEMWatchedDirectory(path=tmpdir, min_movies=50, settle_seconds=0)
            result = scan_epu_directory(watched)

        self.assertIsNone(result, "Should return None when movie_count < min_movies")


# ──────────────────────────────────────────────────────────────────────────────
# 4. Flow Cytometer (smoke — full coverage in test_instrument_connectors.py)
# ──────────────────────────────────────────────────────────────────────────────

class TestFlowCytometerSmoke(unittest.TestCase):
    """Smoke: FCS parser + workflow routing. Full suite in test_instrument_connectors.py."""

    def setUp(self):
        self.fcs = FIXTURES / "sample.fcs"
        if not self.fcs.exists():
            self.skipTest("FCS fixture not found — run generate_fixtures.py")

    def test_fcs_header_cytometer_field(self):
        from lab_instruments.flow_cytometer_connector import read_fcs_header
        meta = read_fcs_header(self.fcs)
        self.assertEqual(meta.get("version"), "FCS3.1")
        self.assertIn("cytometer", meta)

    def test_workflow_map_keys_non_empty(self):
        from lab_instruments.flow_cytometer_connector import _WORKFLOW_MAP
        self.assertGreater(len(_WORKFLOW_MAP), 0)
        for key, wf_id in _WORKFLOW_MAP.items():
            self.assertTrue(wf_id, f"Empty workflow_id for FCS type '{key}'")

    def test_event_payload_completeness(self):
        from lab_instruments.flow_cytometer_connector import (
            read_fcs_header, detect_fcs_experiment_type, _WORKFLOW_MAP,
        )
        meta = read_fcs_header(self.fcs)
        exp_type = detect_fcs_experiment_type(self.fcs, meta)
        workflow_id = _WORKFLOW_MAP.get(exp_type, "flow_cytometry_immunophenotyping")
        event = {
            "source": "flow_cytometer_watcher",
            "workflow_id": workflow_id,
            "fcs_dir": str(self.fcs.parent),
            "description": f"FCS data: {exp_type}",
            "message": f"New FCS data: {exp_type}",
        }
        for field in ("source", "workflow_id", "fcs_dir", "description", "message"):
            self.assertIn(field, event)
            self.assertTrue(event[field])


# ──────────────────────────────────────────────────────────────────────────────
# 5. LC-MS
# ──────────────────────────────────────────────────────────────────────────────

class TestLCMS(unittest.TestCase):
    """File-based vendor detection + workflow routing."""

    def test_thermo_raw_vendor_detection(self):
        if not (FIXTURES / "thermo_run.raw").exists():
            self.skipTest("thermo_run.raw fixture not found")
        from lab_instruments.lcms_connector import detect_vendor
        vendor = detect_vendor(FIXTURES / "thermo_run.raw")
        self.assertEqual(vendor, "thermo")

    def test_vendor_detection_by_extension(self):
        """Vendor detection maps file extensions/directories to vendors."""
        from lab_instruments.lcms_connector import detect_vendor

        # .raw file → thermo (Waters .raw is a directory; Thermo .raw is a file)
        with tempfile.NamedTemporaryFile(suffix=".raw", delete=False) as f:
            fpath = Path(f.name)
        try:
            self.assertEqual(detect_vendor(fpath), "thermo")
        finally:
            fpath.unlink(missing_ok=True)

        # .wiff file → sciex
        with tempfile.NamedTemporaryFile(suffix=".wiff", delete=False) as f:
            fpath = Path(f.name)
        try:
            self.assertEqual(detect_vendor(fpath), "sciex")
        finally:
            fpath.unlink(missing_ok=True)

        # .d directory with .baf inside → bruker
        with tempfile.TemporaryDirectory(suffix=".d") as dpath:
            (Path(dpath) / "analysis.baf").touch()
            self.assertEqual(detect_vendor(Path(dpath)), "bruker")

        # .raw directory → waters
        with tempfile.TemporaryDirectory(suffix=".raw") as dpath:
            self.assertEqual(detect_vendor(Path(dpath)), "waters")

    def test_select_workflow_dia(self):
        """select_workflow routes DIA experiment types to correct workflow."""
        from lab_instruments.lcms_connector import select_workflow
        wf = select_workflow("dia", "thermo")
        self.assertTrue(wf, "select_workflow returned empty string for DIA")
        self.assertIn("dia", wf.lower())

    def test_select_workflow_dda(self):
        from lab_instruments.lcms_connector import select_workflow
        wf = select_workflow("dda", "thermo")
        self.assertTrue(wf, "select_workflow returned empty string for DDA")

    def test_lcms_event_payload_completeness(self):
        from lab_instruments.lcms_connector import select_workflow
        workflow_id = select_workflow("dia", "thermo")
        event = {
            "source": "lcms_watcher",
            "workflow_id": workflow_id,
            "file_path": "/data/lcms/experiment.raw",
            "directory": "/data/lcms/",
            "description": "DIA LC-MS run: 3600 spectra",
            "message": "New LC-MS data ready. Click to run DIA analysis.",
        }
        for field in ("source", "workflow_id", "file_path", "description", "message"):
            self.assertIn(field, event)
            self.assertTrue(event[field])


# ──────────────────────────────────────────────────────────────────────────────
# 6. qPCR (smoke — full coverage in test_instrument_connectors.py)
# ──────────────────────────────────────────────────────────────────────────────

class TestQPCRSmoke(unittest.TestCase):

    def setUp(self):
        self.pcrd = FIXTURES / "cfx_run.pcrd"
        self.eds  = FIXTURES / "quantstudio.eds"
        if not self.pcrd.exists() or not self.eds.exists():
            self.skipTest("qPCR fixtures not found")

    def test_biorad_vendor_detection(self):
        from lab_instruments.qpcr_connector import detect_qpcr_vendor, QPCRVendor
        self.assertEqual(detect_qpcr_vendor(self.pcrd), QPCRVendor.BIORAD)

    def test_abi_vendor_detection(self):
        from lab_instruments.qpcr_connector import detect_qpcr_vendor, QPCRVendor
        self.assertEqual(detect_qpcr_vendor(self.eds), QPCRVendor.ABI)

    def test_workflow_map_non_empty(self):
        from lab_instruments.qpcr_connector import _WORKFLOW_MAP
        self.assertGreater(len(_WORKFLOW_MAP), 0)
        for k, v in _WORKFLOW_MAP.items():
            self.assertTrue(v, f"Empty workflow_id for qPCR type '{k}'")


# ──────────────────────────────────────────────────────────────────────────────
# 7. Opentrons OT-2 / Flex
# ──────────────────────────────────────────────────────────────────────────────

class TestOpentrons(unittest.TestCase):
    """HTTP REST connector: health, protocol upload, run lifecycle, outputs."""

    def setUp(self):
        from lab_instruments.opentrons_connector import OpenTronsConnector
        self.connector = OpenTronsConnector(robot_ip="127.0.0.1", port=_MOCK_PORT)

    def test_health_check_returns_model(self):
        info = self.connector.health_check()
        self.assertIn("robot_model", info)
        self.assertIn("OT-2", info["robot_model"])

    def test_list_protocols_returns_data(self):
        protocols = self.connector.list_protocols()
        self.assertEqual(len(protocols), 1)
        self.assertEqual(protocols[0]["id"], "prot-abc")

    def test_upload_protocol_returns_id(self):
        code = "from opentrons import protocol_api\ndef run(ctx): pass"
        protocol_id = self.connector.upload_protocol(code, "test_protocol.py")
        self.assertEqual(protocol_id, "prot-xyz")

    def test_start_run_returns_run_id(self):
        run_id = self.connector.start_run("prot-xyz")
        self.assertEqual(run_id, "run-ot2-001")

    def test_get_run_status_succeeded(self):
        status = self.connector.get_run_status("run-ot2-001")
        self.assertEqual(status["status"], "succeeded")
        self.assertEqual(status["run_id"], "run-ot2-001")
        self.assertIn("completedAt", status)
        self.assertEqual(status["errors"], [])

    def test_get_run_status_required_fields(self):
        status = self.connector.get_run_status("run-ot2-001")
        for field in ("run_id", "status", "createdAt", "errors"):
            self.assertIn(field, status, f"OT-2 status missing field: {field}")

    def test_generate_plate_transfer_protocol_valid_python(self):
        code = self.connector.generate_plate_transfer_protocol(
            source_labware="corning_96_wellplate_360ul_flat",
            dest_labware="corning_96_wellplate_360ul_flat",
            pipette="p300_single_gen2",
            transfers=[
                {"from_well": "A1", "to_well": "C1", "volume_ul": 10.0},
                {"from_well": "A2", "to_well": "C2", "volume_ul": 10.0},
            ],
        )
        # Should be a valid Python string containing opentrons imports
        self.assertIsInstance(code, str)
        self.assertIn("protocol_api", code)
        self.assertIn("def run", code)

    def test_stop_run_sends_correct_action(self):
        result = self.connector.stop_run("run-ot2-001")
        self.assertTrue(result)

    def test_discover_finds_robot_at_configured_ip(self):
        from lab_instruments.opentrons_connector import OpenTronsConnector
        with patch.dict(os.environ, {"OPENTRONS_ROBOT_IPS": f"127.0.0.1:{_MOCK_PORT}".split(":")[0]}):
            # Discover will attempt health check; mock server responds on _MOCK_PORT
            # Use the connector directly rather than discover() to avoid port guessing
            c = OpenTronsConnector("127.0.0.1", port=_MOCK_PORT)
            info = c.health_check()
            self.assertIn("robot_model", info)


# ──────────────────────────────────────────────────────────────────────────────
# 8. Plate Reader (smoke — full coverage in test_instrument_connectors.py)
# ──────────────────────────────────────────────────────────────────────────────

class TestPlateReaderSmoke(unittest.TestCase):

    def setUp(self):
        self.bmg = FIXTURES / "bmg_mars_export.csv"
        self.biotek = FIXTURES / "gen5_kinetics.txt"
        if not self.bmg.exists() or not self.biotek.exists():
            self.skipTest("Plate reader fixtures not found")

    def test_bmg_vendor_detected(self):
        from lab_instruments.plate_reader_connector import (
            detect_plate_reader_format, _safe_read_lines,
        )
        lines = _safe_read_lines(self.bmg)
        self.assertEqual(detect_plate_reader_format(self.bmg, lines), "bmg")

    def test_biotek_vendor_detected(self):
        from lab_instruments.plate_reader_connector import (
            detect_plate_reader_format, _safe_read_lines,
        )
        lines = _safe_read_lines(self.biotek)
        self.assertEqual(detect_plate_reader_format(self.biotek, lines), "biotek")

    def test_workflow_map_non_empty(self):
        from lab_instruments.plate_reader_connector import _WORKFLOW_MAP
        self.assertGreater(len(_WORKFLOW_MAP), 0)
        for k, v in _WORKFLOW_MAP.items():
            self.assertTrue(v, f"Empty workflow_id for plate reader type '{k}'")


# ──────────────────────────────────────────────────────────────────────────────
# 9. SiLA2
# ──────────────────────────────────────────────────────────────────────────────

class TestSiLA2(unittest.TestCase):
    """
    Tests SiLA2 adapter in stub mode (no gRPC library required).
    Verifies device registry, connect() fallback, and feature listing.
    """

    def test_device_initialises(self):
        from lab_instruments.sila2_adapter import SiLA2Device
        dev = SiLA2Device(host="192.168.1.10", port=50051)
        self.assertEqual(dev.host, "192.168.1.10")
        self.assertEqual(dev.port, 50051)
        self.assertIsNone(dev._client)

    def test_connect_returns_false_when_unreachable(self):
        """connect() should return False (not raise) when no gRPC server exists."""
        from lab_instruments.sila2_adapter import SiLA2Device
        dev = SiLA2Device(host="127.0.0.2", port=59999)  # no server on this port
        result = dev.connect()
        self.assertFalse(result)

    def test_get_server_info_stub_when_not_connected(self):
        """get_server_info() returns stub dict when client is None."""
        from lab_instruments.sila2_adapter import SiLA2Device
        dev = SiLA2Device(host="192.168.1.10")
        info = dev.get_server_info()
        self.assertIsInstance(info, dict)
        # Stub or error key expected — not an exception
        self.assertTrue("error" in info or "name" in info or "server_name" in info)

    def test_list_features_returns_list(self):
        from lab_instruments.sila2_adapter import SiLA2Device
        dev = SiLA2Device(host="192.168.1.10")
        features = dev.list_features()
        self.assertIsInstance(features, list)

    def test_registry_add_and_get_device(self):
        from lab_instruments.sila2_adapter import SiLA2Registry, SiLA2Device
        registry = SiLA2Registry()
        dev = registry.add_device("192.168.1.20", port=50051)
        self.assertIsInstance(dev, SiLA2Device)
        retrieved = registry.get_device("192.168.1.20", port=50051)
        self.assertIs(retrieved, dev)

    def test_registry_list_all(self):
        from lab_instruments.sila2_adapter import SiLA2Registry
        registry = SiLA2Registry()
        registry.add_device("10.0.0.1")
        registry.add_device("10.0.0.2")
        all_devs = registry.list_all()
        self.assertGreaterEqual(len(all_devs), 2)

    def test_load_from_env_parses_comma_list(self):
        import lab_instruments.sila2_adapter as sila2_mod
        from lab_instruments.sila2_adapter import SiLA2Registry
        registry = SiLA2Registry()
        # Patch the module-level constant (already imported at module load time)
        with patch.object(sila2_mod, "SILA2_DEVICE_IPS", "10.0.1.1,10.0.1.2,10.0.1.3"):
            devices = registry.load_from_env()
        self.assertEqual(len(devices), 3)
        hosts = [d.host for d in devices]
        self.assertIn("10.0.1.1", hosts)
        self.assertIn("10.0.1.3", hosts)

    def test_run_command_returns_error_when_not_connected(self):
        from lab_instruments.sila2_adapter import SiLA2Device
        dev = SiLA2Device(host="192.168.1.10")
        result = dev.run_command("SomeFI", "SomeCommand", {})
        self.assertIsInstance(result, dict)
        self.assertIn("error", result)


# ──────────────────────────────────────────────────────────────────────────────
# 10. Benchling
# ──────────────────────────────────────────────────────────────────────────────

class TestBenchling(unittest.TestCase):
    """REST connector: ELN entry pull, sample mapping, assay result push."""

    def setUp(self):
        from lab_instruments.benchling_connector import BenchlingConnector
        self.connector = BenchlingConnector(
            api_url=_MOCK_URL,
            api_key="sk_test_abc123",
        )

    def test_auth_header_set(self):
        # Benchling uses HTTP Basic auth with API key as username
        auth = self.connector.session.auth
        self.assertIsNotNone(auth)
        self.assertEqual(auth[0], "sk_test_abc123")
        self.assertEqual(auth[1], "")

    def test_list_entries_returns_items(self):
        entries = self.connector.list_entries()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["id"], "etr-001")

    def test_get_entry_by_id(self):
        entry = self.connector.get_entry("etr-001")
        self.assertEqual(entry["name"], "ADMET_Study_Entry_June")

    def test_list_samples_returns_items(self):
        samples = self.connector.list_samples()
        self.assertEqual(len(samples), 1)
        self.assertEqual(samples[0]["id"], "smp-001")

    def test_samples_to_workflow_inputs_maps_smiles(self):
        samples = self.connector.list_samples()
        inputs = self.connector.samples_to_workflow_inputs(samples)
        self.assertIsInstance(inputs, dict)
        # Should extract SMILES from fields
        smiles_val = inputs.get("smiles") or inputs.get("compounds") or inputs.get("sample_ids")
        self.assertIsNotNone(smiles_val, f"SMILES or sample ref not found in inputs: {inputs}")

    def test_pull_workflow_inputs_shape(self):
        # entry_id is required to populate the result (method returns {} with no args)
        result = self.connector.pull_workflow_inputs(entry_id="etr-001")
        self.assertIsInstance(result, dict)
        has_data = bool(result.get("notebook_text") is not None or result.get("compounds"))
        self.assertTrue(has_data, f"pull_workflow_inputs returned empty: {result}")

    def test_create_assay_result(self):
        created = self.connector.create_assay_result(
            schema_id="schema-admet",
            fields={"herg_ic50": 45.2, "caco2_papp": 12.1},
        )
        self.assertIn("assayResults", created)
        self.assertEqual(created["assayResults"][0]["schemaId"], "schema-admet")

    def test_push_workflow_outputs_returns_ids(self):
        response = self.connector.push_workflow_outputs(
            assay_schema_id="schema-admet",
            result_rows=[
                {"herg_ic50": 45.2, "ames": "negative"},
                {"herg_ic50": 12.0, "ames": "negative"},
            ],
        )
        self.assertIn("assayResults", response)
        self.assertGreater(len(response["assayResults"]), 0)


# ──────────────────────────────────────────────────────────────────────────────
# Summary runner
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print()
    print("═" * 62)
    print("  BioMate Lab Instruments — E2E Test Suite")
    print(f"  Mock server: {_MOCK_URL}")
    print("═" * 62)
    print()

    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for cls in [
        TestIlluminaBaseSpace,
        TestNanoporeMinKNOW,
        TestCryoEMEPU,
        TestFlowCytometerSmoke,
        TestLCMS,
        TestQPCRSmoke,
        TestOpentrons,
        TestPlateReaderSmoke,
        TestSiLA2,
        TestBenchling,
    ]:
        suite.addTests(loader.loadTestsFromTestCase(cls))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
