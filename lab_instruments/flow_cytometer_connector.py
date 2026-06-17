"""
Flow Cytometer Instrument Connector
======================================
Bridges physical flow cytometers to BioMate's existing analysis functions in
`_lib/cell_biology.py` and `_lib/immunology.py`.

Supported instruments (all file-based or local HTTP):
  - BD FACSAria / FACSCanto / FACSCelesta (FACSDiva software): .fcs file export
  - Beckman Coulter CytoFLEX / Navios (CytExpert): .fcs export
  - Miltenyi MACSQuant: .fcs export
  - Sony SH800: .fcs export
  - Any FCS 2.0 / 3.0 / 3.1 compliant instrument

Integration with BioMate analysis:
  .fcs file detected → analyze_flow_cytometry_immunophenotyping()  (cell_biology.py)
  .fcs file detected → analyze_cfse_cell_proliferation()           (immunology.py)
  .fcs file detected → analyze_cytokine_production_in_cd4_tcells() (immunology.py)
  .fcs file detected → analyze_cell_senescence_and_apoptosis()     (cancer_biology.py)

Workflow detection:
  - Filename contains "CFSE" / "proliferat" → nanopore_basecalling (cfse_proliferation)
  - Filename contains "cytokine" / "intracell" / "ICS" → cytokine_production
  - Filename contains "sort" → cell_sort
  - Default → immunophenotyping

Environment variables:
    FLOW_CYTOMETER_WATCH_DIRS   Colon-separated FACSDiva output directories
    FLOW_CYTOMETER_POLL_INTERVAL Poll interval in seconds (default: 30)
    BIOMATE_API_URL             BioMate API URL
    BIOMATE_API_KEY             BioMate service API key
"""

import logging
import os
import re
import struct
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import requests

log = logging.getLogger(__name__)

WATCH_DIRS = os.environ.get("FLOW_CYTOMETER_WATCH_DIRS", "").split(":") \
    if os.environ.get("FLOW_CYTOMETER_WATCH_DIRS") else []
POLL_INTERVAL = int(os.environ.get("FLOW_CYTOMETER_POLL_INTERVAL", "30"))
BIOMATE_API_URL = os.environ.get("BIOMATE_API_URL", "http://localhost:5000")
BIOMATE_API_KEY = os.environ.get("BIOMATE_API_KEY", "")


# ──────────────────────────────────────────────────────────────────────────────
# FCS file metadata reader (no external library needed)
# ──────────────────────────────────────────────────────────────────────────────

def read_fcs_header(fcs_path: Path) -> Dict[str, Any]:
    """
    Read FCS file header metadata without loading the data matrix.
    FCS 3.0/3.1 format: ASCII magic + TEXT segment with $KEY/VALUE pairs.

    Returns dict with: version, cytometer, experiment_name, sample_id,
    parameter_count, event_count, date, tube_name, panel.
    """
    metadata: Dict[str, Any] = {}
    try:
        with open(fcs_path, "rb") as f:
            # FCS header: bytes 0-5 = version (e.g. "FCS3.1"), 6-9 = spaces,
            # 10-17 = TEXT start offset, 18-25 = TEXT end offset
            header = f.read(58)
            if len(header) < 26:
                return metadata

            version = header[:6].decode("ascii", errors="replace").strip()
            metadata["version"] = version

            if not version.startswith("FCS"):
                return metadata

            text_start = int(header[10:18].strip())
            text_end = int(header[18:26].strip())

            # Read TEXT segment
            f.seek(text_start)
            text_bytes = f.read(text_end - text_start + 1)
            text = text_bytes.decode("latin-1", errors="replace")

            # Parse delimited key/value pairs — delimiter is first byte
            if not text:
                return metadata

            delimiter = text[0]
            # Split on delimiter, skip first empty element
            parts = text[1:].split(delimiter)
            kv: Dict[str, str] = {}
            for i in range(0, len(parts) - 1, 2):
                k = parts[i].strip().upper()
                v = parts[i + 1].strip() if i + 1 < len(parts) else ""
                kv[k] = v

            metadata["cytometer"] = kv.get("$CYT", "")
            metadata["experiment_name"] = kv.get("EXPERIMENT NAME", kv.get("$EXPERIMENT NAME", ""))
            metadata["tube_name"] = kv.get("TUBE NAME", kv.get("$TUBE NAME", ""))
            metadata["sample_id"] = kv.get("SAMPLE ID", kv.get("$SMNO", ""))
            metadata["date"] = kv.get("$DATE", "")
            metadata["parameter_count"] = int(kv.get("$PAR", "0") or "0")
            metadata["event_count"] = int(kv.get("$TOT", "0") or "0")
            # Channel names
            channels = []
            for i in range(1, metadata["parameter_count"] + 1):
                n = kv.get(f"$P{i}N", "")
                s = kv.get(f"$P{i}S", "")  # short name / marker
                if n:
                    channels.append(s or n)
            metadata["channels"] = channels

    except Exception as exc:
        log.debug(f"FCS header read failed for {fcs_path}: {exc}")

    return metadata


# ──────────────────────────────────────────────────────────────────────────────
# Experiment type detection
# ──────────────────────────────────────────────────────────────────────────────

_CFSE_RE = re.compile(r"\b(CFSE|CFDA|CellTrace|proliferat)\b", re.IGNORECASE)
_CYTOKINE_RE = re.compile(r"\b(cytokine|ICS|intracell|TNF|IFN|IL-?\d|interleukin)\b", re.IGNORECASE)
_APOPTOSIS_RE = re.compile(r"\b(apoptosis|annexin|PI|propidium|senescence|cell.death)\b", re.IGNORECASE)
_SORT_RE = re.compile(r"\b(sort|sorted|sorting|index)\b", re.IGNORECASE)
_IMMUNOPHEN_RE = re.compile(r"\b(immuno|phenotyp|CD\d+|T.cell|B.cell|NK|monocyte|DC)\b", re.IGNORECASE)


def detect_fcs_experiment_type(fcs_path: Path, metadata: Dict[str, Any]) -> str:
    """
    Detect the experiment type from filename, tube name, and channel list.
    Maps to BioMate analysis functions in _lib/cell_biology.py and _lib/immunology.py.
    """
    search_text = " ".join([
        fcs_path.stem,
        fcs_path.parent.name,
        metadata.get("experiment_name", ""),
        metadata.get("tube_name", ""),
        " ".join(metadata.get("channels", [])),
    ])

    if _CFSE_RE.search(search_text):
        return "cfse_proliferation"
    if _CYTOKINE_RE.search(search_text):
        return "cytokine_production"
    if _APOPTOSIS_RE.search(search_text):
        return "apoptosis_senescence"
    if _SORT_RE.search(search_text):
        return "cell_sort"
    return "immunophenotyping"


_WORKFLOW_MAP = {
    "cfse_proliferation": "flow_cytometry_proliferation",
    "cytokine_production": "flow_cytometry_cytokines",
    "apoptosis_senescence": "flow_cytometry_apoptosis",
    "cell_sort": "flow_cytometry_sort_analysis",
    "immunophenotyping": "flow_cytometry_immunophenotyping",
}

_ANALYSIS_FUNCTION_MAP = {
    "cfse_proliferation": "immunology.analyze_cfse_cell_proliferation",
    "cytokine_production": "immunology.analyze_cytokine_production_in_cd4_tcells",
    "apoptosis_senescence": "cancer_biology.analyze_cell_senescence_and_apoptosis",
    "cell_sort": "cell_biology.perform_facs_cell_sorting",
    "immunophenotyping": "cell_biology.analyze_flow_cytometry_immunophenotyping",
}


# ──────────────────────────────────────────────────────────────────────────────
# BioMate notification
# ──────────────────────────────────────────────────────────────────────────────

def _notify_biomate(event: Dict[str, Any]) -> bool:
    headers = {"Authorization": f"Bearer {BIOMATE_API_KEY}"} if BIOMATE_API_KEY else {}
    try:
        r = requests.post(
            f"{BIOMATE_API_URL}/api/instruments/new-data",
            json=event,
            headers=headers,
            timeout=10,
        )
        r.raise_for_status()
        log.info(f"BioMate notified: flow cytometry {event['experiment_type']} — {event['file_count']} FCS files")
        return True
    except Exception as exc:
        log.error(f"BioMate notification failed: {exc}")
        return False


# ──────────────────────────────────────────────────────────────────────────────
# File watcher
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class FCSSample:
    """Accumulates FCS files for a single sample/experiment directory."""
    experiment_dir: str
    files: List[str] = field(default_factory=list)
    last_new_file_time: float = 0.0
    triggered: bool = False


@dataclass
class FlowCytometerWatchedDir:
    path: str
    seen_files: Set[str] = field(default_factory=set)
    samples: Dict[str, FCSSample] = field(default_factory=dict)  # parent dir → sample
    settle_seconds: int = 60


def scan_for_fcs_files(watched: FlowCytometerWatchedDir) -> Optional[Dict[str, Any]]:
    """
    Scan for new .fcs files. Groups by parent directory (= one experiment).
    Fires when a group settles (no new files for settle_seconds).
    """
    dir_path = Path(watched.path)
    if not dir_path.exists():
        return None

    for fcs_file in dir_path.rglob("*.fcs"):
        key = str(fcs_file)
        if key in watched.seen_files:
            continue
        watched.seen_files.add(key)

        # Group by immediate parent = one tube rack / experiment
        sample_key = str(fcs_file.parent)
        if sample_key not in watched.samples:
            watched.samples[sample_key] = FCSSample(experiment_dir=sample_key)
        sample = watched.samples[sample_key]
        sample.files.append(key)
        sample.last_new_file_time = time.time()

    # Check each sample group for settle
    now = time.time()
    for sample_key, sample in list(watched.samples.items()):
        if sample.triggered or not sample.files:
            continue
        if (now - sample.last_new_file_time) < watched.settle_seconds:
            continue

        sample.triggered = True

        # Read metadata from first FCS file
        first_fcs = Path(sample.files[0])
        metadata = read_fcs_header(first_fcs)
        exp_type = detect_fcs_experiment_type(first_fcs, metadata)
        workflow_id = _WORKFLOW_MAP.get(exp_type, "flow_cytometry_immunophenotyping")
        analysis_fn = _ANALYSIS_FUNCTION_MAP.get(exp_type, "")

        event = {
            "source": "flow_cytometer_watcher",
            "workflow_id": workflow_id,
            "experiment_type": exp_type,
            "directory": sample_key,
            "fcs_dir": sample_key,
            "file_count": len(sample.files),
            "sample_files": sample.files[:5],
            "param_key": "fcs_dir",
            "cytometer": metadata.get("cytometer", "unknown"),
            "event_count_per_tube": metadata.get("event_count", 0),
            "channels": metadata.get("channels", []),
            "analysis_function": analysis_fn,
            "description": (
                f"Flow cytometry: {len(sample.files)} FCS files ({exp_type.replace('_', ' ')}). "
                f"Cytometer: {metadata.get('cytometer', 'unknown')}."
            ),
            "message": (
                f"New flow cytometry data: {len(sample.files)} FCS files "
                f"({exp_type.replace('_', ' ')}). Click to run analysis."
            ),
        }
        return event

    return None


class FlowCytometerWatcher:
    """
    Background daemon watching FACSDiva / CytExpert output directories for new .fcs files.
    On completion, fires callback with experiment metadata and triggers BioMate workflow.
    """

    def __init__(
        self,
        directories: Optional[List[str]] = None,
        on_new_data=None,
        poll_interval: int = POLL_INTERVAL,
        settle_seconds: int = 60,
    ):
        self.directories = [
            FlowCytometerWatchedDir(path=d, settle_seconds=settle_seconds)
            for d in (directories or WATCH_DIRS) if d.strip()
        ]
        self.on_new_data = on_new_data or _notify_biomate
        self.poll_interval = poll_interval
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def add_directory(self, path: str, settle_seconds: int = 60) -> None:
        self.directories.append(FlowCytometerWatchedDir(path=path, settle_seconds=settle_seconds))
        log.info(f"Flow cytometer watching: {path}")

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        log.info(f"FlowCytometerWatcher started: {[d.path for d in self.directories]}")

    def stop(self) -> None:
        self._running = False

    def _loop(self) -> None:
        while self._running:
            for watched in self.directories:
                try:
                    event = scan_for_fcs_files(watched)
                    if event:
                        self.on_new_data(event)
                except Exception as exc:
                    log.error(f"Flow cytometer scan error for {watched.path}: {exc}")
            time.sleep(self.poll_interval)


if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="BioMate Flow Cytometer FCS File Watcher")
    parser.add_argument("dirs", nargs="+", help="FACSDiva/CytExpert output directories")
    parser.add_argument("--interval", type=int, default=30)
    parser.add_argument("--settle", type=int, default=60)
    args = parser.parse_args()
    watcher = FlowCytometerWatcher(directories=args.dirs, poll_interval=args.interval, settle_seconds=args.settle)
    watcher.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        watcher.stop()
