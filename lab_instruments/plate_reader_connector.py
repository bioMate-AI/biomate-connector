"""
Plate Reader Instrument Connector
====================================
Bridges physical plate readers to BioMate's biochemistry analysis functions in
`_lib/biochemistry.py` (enzyme kinetics, dose-response, ITC, CD spectra).

Supported instruments (all file-based export):
  - BMG LABTECH FLUOstar Omega / CLARIOstar / PHERAstar (MARS data reader → CSV)
  - BioTek / Agilent Synergy (Gen5 software → CSV / TXT)
  - Molecular Devices SpectraMax (SoftMax Pro → .txt or .xml)
  - Tecan Spark (SparkControl → .xlsx / .CSV)
  - Enspire (PerkinElmer) → .csv
  - BioRad iMark / xMark → .csv

Experiment type detection:
  - Absorbance kinetics (450 nm, OD450) → analyze_enzyme_kinetics_assay()
  - Fluorescence over time → analyze_enzyme_kinetics_assay() (fluorometric variant)
  - Dose-response (8–12 dilution rows) → dose_response curve fitting
  - Endpoint absorbance (single time point, OD620) → ELISA quantification
  - Cell viability (MTS/MTT/CTG) → IC50 / growth inhibition

Integration with BioMate _lib/biochemistry.py:
  - analyze_enzyme_kinetics_assay(time_points, absorbance_values, ...)
  - analyze_protease_kinetics(substrate_conc, v0_values, ...)
  - Dose-response: michaelis_menten() / dose_response() from biochemistry

Environment variables:
    PLATE_READER_WATCH_DIRS     Colon-separated Gen5/MARS export directories
    PLATE_READER_POLL_INTERVAL  Poll interval in seconds (default: 30)
    BIOMATE_API_URL             BioMate API URL
    BIOMATE_API_KEY             BioMate service API key
"""

import csv
import io
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import requests

log = logging.getLogger(__name__)

WATCH_DIRS = os.environ.get("PLATE_READER_WATCH_DIRS", "").split(":") \
    if os.environ.get("PLATE_READER_WATCH_DIRS") else []
POLL_INTERVAL = int(os.environ.get("PLATE_READER_POLL_INTERVAL", "30"))
BIOMATE_API_URL = os.environ.get("BIOMATE_API_URL", "http://localhost:5000")
BIOMATE_API_KEY = os.environ.get("BIOMATE_API_KEY", "")

# File extensions produced by common plate reader software
_PLATE_READER_EXTENSIONS = {".csv", ".txt", ".xlsx", ".xls", ".asc", ".pda"}


# ──────────────────────────────────────────────────────────────────────────────
# Plate reader file format detection and metadata extraction
# ──────────────────────────────────────────────────────────────────────────────

class PlateReaderVendor:
    BMG = "bmg"          # MARS data reader: CSV with metadata header
    BIOTEK = "biotek"    # Gen5: tab-delimited TXT with "Results" section
    MOLECULAR_DEVICES = "molecular_devices"  # SoftMax Pro: section-delimited
    TECAN = "tecan"      # SparkControl: XLSX or CSV
    GENERIC = "generic"  # Any CSV plate data


def detect_plate_reader_format(path: Path, first_lines: List[str]) -> str:
    """Detect plate reader vendor from file header signatures."""
    header = "\n".join(first_lines[:10]).lower()
    if "mars" in header or "bmg" in header or "fluostar" in header or "clariostar" in header:
        return PlateReaderVendor.BMG
    if "gen5" in header or "biotek" in header or "synergy" in header:
        return PlateReaderVendor.BIOTEK
    if "softmax" in header or "molecular devices" in header or "spectramax" in header:
        return PlateReaderVendor.MOLECULAR_DEVICES
    if "spark" in header or "tecan" in header:
        return PlateReaderVendor.TECAN
    return PlateReaderVendor.GENERIC


def _safe_read_lines(path: Path, n: int = 30) -> List[str]:
    """Read first n lines of a text file safely."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return [f.readline() for _ in range(n)]
    except Exception:
        return []


def extract_plate_metadata(path: Path, vendor: str) -> Dict[str, Any]:
    """
    Extract metadata from plate reader file header.
    Returns: {assay_name, wavelength, read_type, time_points, plate_format, instrument}
    """
    metadata: Dict[str, Any] = {"vendor": vendor, "file": path.name}
    lines = _safe_read_lines(path)
    full_header = "\n".join(lines)

    # Wavelength detection
    wl_match = re.search(r"(\d{3})\s*nm", full_header, re.IGNORECASE)
    if wl_match:
        metadata["wavelength_nm"] = int(wl_match.group(1))

    # Read type
    if re.search(r"\bkinetic\b|\btime.course\b|\btime.point", full_header, re.IGNORECASE):
        metadata["read_type"] = "kinetic"
    elif re.search(r"\bfluoresc|\bemission|\bexcitation", full_header, re.IGNORECASE):
        metadata["read_type"] = "fluorescence"
    elif re.search(r"\bluminesc", full_header, re.IGNORECASE):
        metadata["read_type"] = "luminescence"
    else:
        metadata["read_type"] = "absorbance"

    # Plate format
    if "384" in full_header:
        metadata["plate_format"] = 384
    elif "96" in full_header:
        metadata["plate_format"] = 96
    elif "48" in full_header:
        metadata["plate_format"] = 48
    else:
        metadata["plate_format"] = 96  # default

    # Assay name from filename
    metadata["assay_name"] = path.stem.replace("_", " ").replace("-", " ")

    return metadata


# ──────────────────────────────────────────────────────────────────────────────
# Experiment type detection → BioMate workflow
# ──────────────────────────────────────────────────────────────────────────────

_DOSE_RESPONSE_RE = re.compile(r"\b(IC50|EC50|dose.response|dilut|serial|GI50|LD50)\b", re.IGNORECASE)
_KINETICS_RE = re.compile(r"\b(kinetic|Km|Vmax|velocity|rate|progress.curve|time.course)\b", re.IGNORECASE)
_ELISA_RE = re.compile(r"\bELISA\b|\bimmunoassay\b|\bstandard.curve\b", re.IGNORECASE)
_VIABILITY_RE = re.compile(r"\b(MTS|MTT|CTG|CellTiter|resazurin|Alamar|viab)\b", re.IGNORECASE)
_PROTEASE_RE = re.compile(r"\b(protease|trypsin|thrombin|cleav|substrate)\b", re.IGNORECASE)


def detect_assay_type(path: Path, metadata: Dict[str, Any]) -> str:
    search = path.stem + " " + path.parent.name + " " + metadata.get("assay_name", "")
    if _VIABILITY_RE.search(search):
        return "cell_viability_ic50"
    if _DOSE_RESPONSE_RE.search(search):
        return "dose_response"
    if _PROTEASE_RE.search(search):
        return "protease_kinetics"
    if _KINETICS_RE.search(search) or metadata.get("read_type") == "kinetic":
        return "enzyme_kinetics"
    if _ELISA_RE.search(search):
        return "elisa_quantification"
    return "absorbance_endpoint"


_WORKFLOW_MAP = {
    "enzyme_kinetics": "plate_reader_enzyme_kinetics",
    "protease_kinetics": "plate_reader_protease_kinetics",
    "dose_response": "plate_reader_dose_response",
    "cell_viability_ic50": "plate_reader_cell_viability",
    "elisa_quantification": "plate_reader_elisa",
    "absorbance_endpoint": "plate_reader_absorbance",
}

_ANALYSIS_FUNCTION_MAP = {
    "enzyme_kinetics": "biochemistry.analyze_enzyme_kinetics_assay",
    "protease_kinetics": "biochemistry.analyze_protease_kinetics",
    "dose_response": "biochemistry.dose_response",
    "cell_viability_ic50": "biochemistry.dose_response",
    "elisa_quantification": "biochemistry.analyze_enzyme_kinetics_assay",
    "absorbance_endpoint": "biochemistry.analyze_enzyme_kinetics_assay",
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
        log.info(f"BioMate notified: plate reader {event['assay_type']} from {event['file_path']}")
        return True
    except Exception as exc:
        log.error(f"BioMate notification failed: {exc}")
        return False


# ──────────────────────────────────────────────────────────────────────────────
# File watcher
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class PlateReaderWatchedDir:
    path: str
    seen_files: Set[str] = field(default_factory=set)
    settle_seconds: int = 30


def scan_for_plate_reader_files(watched: PlateReaderWatchedDir) -> Optional[Dict[str, Any]]:
    dir_path = Path(watched.path)
    if not dir_path.exists():
        return None

    for file_path in dir_path.rglob("*"):
        if not file_path.is_file():
            continue
        if file_path.suffix.lower() not in _PLATE_READER_EXTENSIONS:
            continue
        key = str(file_path)
        if key in watched.seen_files:
            continue

        # Check file is stable (not actively being written)
        try:
            mtime = file_path.stat().st_mtime
        except OSError:
            continue
        if (time.time() - mtime) < watched.settle_seconds:
            continue

        watched.seen_files.add(key)

        first_lines = _safe_read_lines(file_path)
        vendor = detect_plate_reader_format(file_path, first_lines)
        metadata = extract_plate_metadata(file_path, vendor)
        assay_type = detect_assay_type(file_path, metadata)
        workflow_id = _WORKFLOW_MAP.get(assay_type, "plate_reader_absorbance")
        analysis_fn = _ANALYSIS_FUNCTION_MAP.get(assay_type, "")

        event = {
            "source": "plate_reader_watcher",
            "workflow_id": workflow_id,
            "assay_type": assay_type,
            "file_path": key,
            "directory": watched.path,
            "param_key": "data_file",
            "vendor": vendor,
            "wavelength_nm": metadata.get("wavelength_nm"),
            "read_type": metadata.get("read_type"),
            "plate_format": metadata.get("plate_format"),
            "assay_name": metadata.get("assay_name"),
            "analysis_function": analysis_fn,
            "description": (
                f"Plate reader data: {assay_type.replace('_', ' ')} "
                f"({vendor}, {metadata.get('plate_format', 96)}-well, "
                f"{metadata.get('read_type', 'absorbance')}). "
                f"BioMate analysis via {analysis_fn}."
            ),
            "message": (
                f"New plate reader data: {file_path.name} "
                f"({assay_type.replace('_', ' ')}). Click to run analysis."
            ),
        }
        return event

    return None


class PlateReaderWatcher:
    """
    Background daemon watching Gen5/MARS/SoftMaxPro output directories.
    Detects new assay files and triggers BioMate biochemistry analysis workflows.

    Bridges to existing analysis functions:
      - biochemistry.analyze_enzyme_kinetics_assay()
      - biochemistry.analyze_protease_kinetics()
      - biochemistry.dose_response()
    """

    def __init__(
        self,
        directories: Optional[List[str]] = None,
        on_new_data=None,
        poll_interval: int = POLL_INTERVAL,
        settle_seconds: int = 30,
    ):
        self.directories = [
            PlateReaderWatchedDir(path=d, settle_seconds=settle_seconds)
            for d in (directories or WATCH_DIRS) if d.strip()
        ]
        self.on_new_data = on_new_data or _notify_biomate
        self.poll_interval = poll_interval
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def add_directory(self, path: str, settle_seconds: int = 30) -> None:
        self.directories.append(PlateReaderWatchedDir(path=path, settle_seconds=settle_seconds))
        log.info(f"Plate reader watching: {path}")

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        log.info(f"PlateReaderWatcher started: {[d.path for d in self.directories]}")

    def stop(self) -> None:
        self._running = False

    def _loop(self) -> None:
        while self._running:
            for watched in self.directories:
                try:
                    event = scan_for_plate_reader_files(watched)
                    if event:
                        self.on_new_data(event)
                except Exception as exc:
                    log.error(f"Plate reader scan error for {watched.path}: {exc}")
            time.sleep(self.poll_interval)


if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="BioMate Plate Reader File Watcher")
    parser.add_argument("dirs", nargs="+", help="Gen5/MARS/SoftMaxPro export directories")
    parser.add_argument("--interval", type=int, default=30)
    parser.add_argument("--settle", type=int, default=30)
    args = parser.parse_args()
    watcher = PlateReaderWatcher(directories=args.dirs, poll_interval=args.interval, settle_seconds=args.settle)
    watcher.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        watcher.stop()
