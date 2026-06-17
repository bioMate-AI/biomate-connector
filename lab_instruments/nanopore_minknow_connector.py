"""
Nanopore MinKNOW Connector
===========================
Connects BioMate to an Oxford Nanopore MinKNOW instance running on a local
sequencing server. Monitors sequencing runs, detects completion, and triggers
basecalling workflows.

MinKNOW exposes two interfaces:
  - Local HTTP health/info: http://<host>:9501 (basic status)
  - gRPC manager API: minknow-api Python library (full run management)

This connector uses the HTTP interface (no gRPC dependency) for lightweight
integration. For full protocol control, the minknow-api library can be added.

Environment variables:
    MINKNOW_HOST        MinKNOW server hostname/IP (default: localhost)
    MINKNOW_HTTP_PORT   HTTP API port (default: 9501)
    BIOMATE_API_URL     BioMate API URL

Reference: https://github.com/nanoporetech/minknow_api
"""

import logging
import os
import time
from typing import Any, Dict, List, Optional

import requests

log = logging.getLogger(__name__)

MINKNOW_HOST = os.environ.get("MINKNOW_HOST", "localhost")
MINKNOW_HTTP_PORT = int(os.environ.get("MINKNOW_HTTP_PORT", "9501"))
MINKNOW_BASE_URL = f"http://{MINKNOW_HOST}:{MINKNOW_HTTP_PORT}"
BIOMATE_API_URL = os.environ.get("BIOMATE_API_URL", "http://localhost:5000")
BIOMATE_API_KEY = os.environ.get("BIOMATE_API_KEY", "")


class MinKNOWConnector:
    """
    Client for MinKNOW's local HTTP interface.

    Capabilities:
      - List flow cells / positions
      - Get current run status and output path
      - Detect run completion (state transitions to 'processing' or 'finishing')
      - Pull output file paths (POD5, FAST5, FASTQ) for BioMate workflow input
    """

    def __init__(self, host: str = MINKNOW_HOST, port: int = MINKNOW_HTTP_PORT):
        self.base_url = f"http://{host}:{port}"
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "BioMate-Connector/1.0"

    def health(self) -> Dict[str, Any]:
        """
        Check MinKNOW health. Returns server info including version.
        Raises requests.RequestException if MinKNOW is not running.
        """
        r = self.session.get(f"{self.base_url}/", timeout=5)
        r.raise_for_status()
        return r.json()

    def list_positions(self) -> List[Dict[str, Any]]:
        """
        List flow cell positions (ports) on the sequencing device.
        Each position: {name, state, flow_cell_info, current_protocol}
        """
        try:
            r = self.session.get(f"{self.base_url}/positions", timeout=5)
            r.raise_for_status()
            data = r.json()
            return data.get("positions", data) if isinstance(data, dict) else data
        except Exception:
            # MinKNOW HTTP API varies by version — fallback to empty list
            log.warning("Could not list MinKNOW positions via HTTP")
            return []

    def get_run_info(self, position_name: str) -> Optional[Dict[str, Any]]:
        """
        Get current run information for a flow cell position.
        Returns run metadata including output directory and run_id.
        """
        try:
            r = self.session.get(f"{self.base_url}/run_info/{position_name}", timeout=5)
            r.raise_for_status()
            return r.json()
        except Exception:
            return None

    def detect_completed_runs(self) -> List[Dict[str, Any]]:
        """
        Scan all positions and return runs that have recently completed
        (state = 'finishing' or 'processing') with output paths.
        """
        completed = []
        for pos in self.list_positions():
            state = pos.get("state", "")
            if state in ("finishing", "processing", "completed"):
                run_info = self.get_run_info(pos.get("name", ""))
                if run_info:
                    completed.append({
                        "position": pos.get("name"),
                        "state": state,
                        "run_id": run_info.get("run_id"),
                        "experiment_name": run_info.get("experiment_name"),
                        "sample_id": run_info.get("sample_id"),
                        "output_path": run_info.get("output_directory"),
                        "flow_cell_id": run_info.get("flow_cell_id"),
                        "kit": run_info.get("protocol_run_info", {}).get("kit", ""),
                    })
        return completed

    def get_output_files(self, output_path: str) -> Dict[str, List[str]]:
        """
        Scan the local output directory for POD5, FAST5, and FASTQ files.
        Returns dict with {pod5: [...], fast5: [...], fastq: [...]}.

        Note: MinKNOW writes to the sequencing server's local filesystem.
        If BioMate runs on a different machine, output_path must be NFS-mounted.
        """
        import glob
        result: Dict[str, List[str]] = {"pod5": [], "fast5": [], "fastq": []}
        if not output_path:
            return result

        # POD5 (newer format — preferred for Dorado basecalling)
        result["pod5"] = glob.glob(f"{output_path}/**/*.pod5", recursive=True)
        # FAST5 (legacy format)
        result["fast5"] = glob.glob(f"{output_path}/**/*.fast5", recursive=True)
        # FASTQ (post-basecalling output, if basecalling is enabled in MinKNOW)
        result["fastq"] = glob.glob(f"{output_path}/**/*.fastq.gz", recursive=True)

        return result

    def run_to_biomate_params(self, run: Dict[str, Any]) -> Dict[str, Any]:
        """
        Convert a completed MinKNOW run to BioMate workflow parameters.
        Maps to the `nanopore_basecalling` or `wgs_nanopore_variant_call` workflow.
        """
        output_path = run.get("output_path", "")
        files = self.get_output_files(output_path) if output_path else {}

        pod5_dir = output_path if files.get("pod5") else None
        fast5_dir = output_path if files.get("fast5") else None

        return {
            "input_path": pod5_dir or fast5_dir or output_path,
            "input_format": "pod5" if pod5_dir else "fast5" if fast5_dir else "unknown",
            "sample_id": run.get("sample_id", "sample"),
            "experiment_name": run.get("experiment_name"),
            "flow_cell_id": run.get("flow_cell_id"),
            "kit": run.get("kit", ""),
            "suggested_workflow": self._detect_workflow(run),
            "file_counts": {k: len(v) for k, v in files.items()},
        }

    @staticmethod
    def _detect_workflow(run: Dict[str, Any]) -> str:
        """Detect appropriate BioMate workflow from run metadata."""
        kit = run.get("kit", "").upper()
        # DNA/WGS kits
        if any(k in kit for k in ("LSK", "SQK-LSK", "RAD", "RBK", "NBD")):
            return "wgs_nanopore_variant_call"
        # RNA kits
        if any(k in kit for k in ("PCB", "DRS", "SQK-RNA")):
            return "rnaseq_nanopore"
        return "nanopore_basecalling"


# ──────────────────────────────────────────────────────────────────────────────
# File watcher for automated run detection
# ──────────────────────────────────────────────────────────────────────────────

class MinKNOWWatcher:
    """
    Poll MinKNOW at regular intervals. When a run completes, call callback(run_params).
    Designed to run as a background daemon thread.
    """

    def __init__(
        self,
        connector: MinKNOWConnector,
        on_run_complete: Any,  # Callable[[dict], None]
        poll_interval: int = 30,
    ):
        self.connector = connector
        self.on_run_complete = on_run_complete
        self.poll_interval = poll_interval
        self._seen_runs: set = set()
        self._running = False
        self._thread: Optional[Any] = None

    def start(self) -> None:
        """Start the background polling thread."""
        import threading
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        log.info(f"MinKNOW watcher started (host={self.connector.base_url}, interval={self.poll_interval}s)")

    def stop(self) -> None:
        """Stop the background polling thread."""
        self._running = False

    def _loop(self) -> None:
        while self._running:
            try:
                runs = self.connector.detect_completed_runs()
                for run in runs:
                    run_id = run.get("run_id")
                    if run_id and run_id not in self._seen_runs:
                        self._seen_runs.add(run_id)
                        params = self.connector.run_to_biomate_params(run)
                        log.info(f"MinKNOW run completed: {run_id} → {params['suggested_workflow']}")
                        try:
                            self.on_run_complete(params)
                        except Exception as cb_exc:
                            log.error(f"Run completion callback failed: {cb_exc}")
            except Exception as poll_exc:
                log.warning(f"MinKNOW poll error: {poll_exc}")
            time.sleep(self.poll_interval)
