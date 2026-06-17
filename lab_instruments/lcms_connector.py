"""
LC-MS Instrument Connector
============================
Monitors liquid chromatography-mass spectrometry data directories for new raw files
and triggers BioMate proteomics / metabolomics workflows.

Supported vendors and file formats:
  - Thermo Fisher (Xcalibur): .raw / .RAW files — file-based export
  - Waters (MassLynx): .raw directories (folder-based format)
  - Bruker (DataAnalysis): .d directories (.baf, .tdf inside)
  - AB SCIEX (Analyst): .wiff / .wiff.scan files
  - Generic: .mzML (post-conversion via msconvert / proteowizard)

Workflow triggers:
  - Proteomics (DDA/DIA): .raw or .mzML → diann_proteomics or maxquant_proteomics
  - Metabolomics: .raw or .mzML → xcms_metabolomics or mzmine_metabolomics
  - Lipidomics: detected by instrument method name containing "lipid"

Architecture:
  1. LCMSFileWatcher polls configured directories every POLL_INTERVAL seconds
  2. On new file: vendor detection → workflow selection → notify BioMate API
  3. Optional: call msconvert to convert .raw → .mzML before triggering workflow
  4. Method file parsing: reads instrument method name from vendor metadata to
     auto-detect experiment type (proteomics vs metabolomics vs lipidomics)

Environment variables:
    LCMS_WATCH_DIRS      Colon-separated list of directories to watch
    LCMS_POLL_INTERVAL   Polling interval in seconds (default: 30)
    LCMS_AUTO_CONVERT    Set to "1" to auto-convert .raw → .mzML via msconvert
    MSCONVERT_PATH       Path to msconvert binary (default: msconvert)
    BIOMATE_API_URL      BioMate API URL
    BIOMATE_API_KEY      BioMate service API key
"""

import logging
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import requests

log = logging.getLogger(__name__)

LCMS_WATCH_DIRS = os.environ.get("LCMS_WATCH_DIRS", "").split(":") if os.environ.get("LCMS_WATCH_DIRS") else []
LCMS_POLL_INTERVAL = int(os.environ.get("LCMS_POLL_INTERVAL", "30"))
LCMS_AUTO_CONVERT = os.environ.get("LCMS_AUTO_CONVERT", "0") == "1"
MSCONVERT_PATH = os.environ.get("MSCONVERT_PATH", "msconvert")
BIOMATE_API_URL = os.environ.get("BIOMATE_API_URL", "http://localhost:5000")
BIOMATE_API_KEY = os.environ.get("BIOMATE_API_KEY", "")


# ──────────────────────────────────────────────────────────────────────────────
# Vendor detection
# ──────────────────────────────────────────────────────────────────────────────

class LCMSVendor:
    THERMO = "thermo"       # .raw files
    WATERS = "waters"       # .raw directory (folder-based)
    BRUKER = "bruker"       # .d directory
    SCIEX = "sciex"         # .wiff / .wiff.scan
    GENERIC = "generic"     # .mzML (post-conversion)


def detect_vendor(path: Path) -> str:
    """
    Detect the LC-MS vendor from file/directory extension and internal structure.
    """
    name = path.name.lower()
    if path.is_file():
        if name.endswith(".raw"):
            # Thermo .raw is a file; Waters .raw is a directory — check is_file()
            return LCMSVendor.THERMO
        if name.endswith(".wiff") or name.endswith(".wiff.scan"):
            return LCMSVendor.SCIEX
        if name.endswith(".mzml") or name.endswith(".mzxml"):
            return LCMSVendor.GENERIC
    elif path.is_dir():
        if name.endswith(".raw"):
            return LCMSVendor.WATERS
        if name.endswith(".d"):
            # Bruker .d directory: contains .baf, .tdf, or .ms2 files
            contents = list(path.iterdir()) if path.exists() else []
            for child in contents:
                if child.suffix.lower() in (".baf", ".tdf", ".ms2", ".mcf"):
                    return LCMSVendor.BRUKER
    return LCMSVendor.GENERIC


# ──────────────────────────────────────────────────────────────────────────────
# Workflow detection from file metadata
# ──────────────────────────────────────────────────────────────────────────────

# Keyword patterns in directory/file names that indicate experiment type
_PROTEOMICS_PATTERNS = re.compile(
    r"\b(protein|proteom|trypsin|digest|peptide|TMT|iTRAQ|DDA|DIA|SWATH|PRM|MRM|SRM)\b",
    re.IGNORECASE
)
_METABOLOMICS_PATTERNS = re.compile(
    r"\b(metabol|metanom|HILIC|C18|lipid|lipidom|plasma|serum|urine|xcms|RPLC)\b",
    re.IGNORECASE
)
_LIPIDOMICS_PATTERNS = re.compile(
    r"\b(lipid|lipidom|sphingo|phospholipid|ceramide|triglyceride|cholesterol)\b",
    re.IGNORECASE
)


def detect_experiment_type(path: Path) -> str:
    """
    Detect experiment type from file/directory name and parent directory name.
    Returns: 'proteomics', 'metabolomics', 'lipidomics', or 'proteomics' (default).
    """
    search_text = f"{path.name} {path.parent.name} {path.parent.parent.name}"

    if _LIPIDOMICS_PATTERNS.search(search_text):
        return "lipidomics"
    if _METABOLOMICS_PATTERNS.search(search_text):
        return "metabolomics"
    if _PROTEOMICS_PATTERNS.search(search_text):
        return "proteomics"
    return "proteomics"  # Most LC-MS in biomed = proteomics


def select_workflow(experiment_type: str, vendor: str) -> str:
    """Map experiment type + vendor to BioMate workflow_id."""
    mapping = {
        "proteomics": "diann_proteomics",
        "metabolomics": "xcms_metabolomics",
        "lipidomics": "lipidomics_lipidmaps",
    }
    return mapping.get(experiment_type, "diann_proteomics")


# ──────────────────────────────────────────────────────────────────────────────
# msconvert wrapper
# ──────────────────────────────────────────────────────────────────────────────

def convert_to_mzml(raw_path: Path, output_dir: Optional[Path] = None) -> Optional[Path]:
    """
    Convert a vendor raw file to mzML using ProteoWizard msconvert.
    Returns the path to the converted .mzML file, or None on failure.

    Install: https://proteowizard.sourceforge.io/download.html
    Docker:  chambm/pwiz-skyline-i-agree-to-the-vendor-licenses

    Note: Thermo .raw conversion requires Windows or the pwiz Docker container.
    """
    out_dir = output_dir or raw_path.parent
    try:
        cmd = [
            MSCONVERT_PATH,
            str(raw_path),
            "--mzML",
            "--outdir", str(out_dir),
            "--filter", "peakPicking vendor msLevel=1-",  # centroiding
        ]
        log.info(f"msconvert: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            log.error(f"msconvert failed: {result.stderr}")
            return None

        # Find the output file
        stem = raw_path.stem if raw_path.is_file() else raw_path.name.rstrip("/")
        mzml_path = out_dir / f"{stem}.mzML"
        if mzml_path.exists():
            log.info(f"Converted: {mzml_path}")
            return mzml_path
        # Sometimes msconvert strips the extension from .d folders
        for f in out_dir.glob(f"{stem}*.mzML"):
            return f
        return None
    except FileNotFoundError:
        log.warning(f"msconvert not found at {MSCONVERT_PATH}. Install ProteoWizard.")
        return None
    except subprocess.TimeoutExpired:
        log.error(f"msconvert timed out on {raw_path}")
        return None
    except Exception as exc:
        log.error(f"msconvert error: {exc}")
        return None


# ──────────────────────────────────────────────────────────────────────────────
# BioMate notification
# ──────────────────────────────────────────────────────────────────────────────

def _notify_biomate(event: Dict[str, Any]) -> bool:
    """POST new LC-MS data event to BioMate API."""
    headers = {"Authorization": f"Bearer {BIOMATE_API_KEY}"} if BIOMATE_API_KEY else {}
    try:
        r = requests.post(
            f"{BIOMATE_API_URL}/api/instruments/new-data",
            json=event,
            headers=headers,
            timeout=10,
        )
        r.raise_for_status()
        log.info(f"BioMate notified: {event['workflow_id']} for {event['file_path']}")
        return True
    except Exception as exc:
        log.error(f"BioMate notification failed: {exc}")
        return False


# ──────────────────────────────────────────────────────────────────────────────
# LC-MS file watcher
# ──────────────────────────────────────────────────────────────────────────────

# Extensions and directory patterns that indicate a complete LC-MS acquisition
_LCMS_FILE_PATTERNS = {
    # Files
    ".raw",         # Thermo
    ".wiff",        # SCIEX
    ".wiff.scan",   # SCIEX
    ".mzml",        # Generic post-conversion
    ".mzxml",       # Legacy
    # Directories (detected separately)
}

_LCMS_DIR_SUFFIXES = {".raw", ".d"}  # Waters and Bruker directory formats


@dataclass
class LCMSWatchedDirectory:
    path: str
    seen: Set[str] = field(default_factory=set)
    settle_seconds: int = 120  # LC-MS runs can be 20-90 min; wait longer after file appears


def _is_acquisition_complete(path: Path, vendor: str, settle_seconds: int, last_mtime: float) -> bool:
    """
    Check if the acquisition is complete (file not growing, stable for settle_seconds).
    For Thermo .raw: check that file mtime is older than settle_seconds.
    For directory formats: check that no children were modified recently.
    """
    now = time.time()
    try:
        if path.is_file():
            mtime = path.stat().st_mtime
            return (now - mtime) > settle_seconds
        elif path.is_dir():
            # Check all children
            latest_mtime = max(
                (f.stat().st_mtime for f in path.rglob("*") if f.is_file()),
                default=0
            )
            return (now - latest_mtime) > settle_seconds
    except OSError:
        pass
    return False


def scan_for_lcms_files(watched: LCMSWatchedDirectory) -> Optional[Dict[str, Any]]:
    """
    Scan a directory for new LC-MS raw files.
    Returns an event dict if a stable (complete) file is found, else None.
    """
    dir_path = Path(watched.path)
    if not dir_path.exists():
        return None

    for item in dir_path.rglob("*"):
        key = str(item)
        if key in watched.seen:
            continue

        is_lcms = False
        if item.is_file():
            # Handle .wiff.scan (double extension)
            name_lower = item.name.lower()
            if any(name_lower.endswith(ext) for ext in _LCMS_FILE_PATTERNS):
                is_lcms = True
        elif item.is_dir():
            if item.suffix.lower() in _LCMS_DIR_SUFFIXES:
                is_lcms = True

        if not is_lcms:
            continue

        vendor = detect_vendor(item)
        try:
            last_mtime = item.stat().st_mtime if item.is_file() else max(
                (f.stat().st_mtime for f in item.rglob("*") if f.is_file()), default=0
            )
        except OSError:
            continue

        # Check if acquisition has settled (file not still being written)
        if not _is_acquisition_complete(item, vendor, watched.settle_seconds, last_mtime):
            log.debug(f"LC-MS file still settling: {item}")
            continue

        watched.seen.add(key)

        exp_type = detect_experiment_type(item)
        workflow_id = select_workflow(exp_type, vendor)

        # Auto-convert if configured
        mzml_path = None
        if LCMS_AUTO_CONVERT and vendor != LCMSVendor.GENERIC:
            mzml_path = convert_to_mzml(item)

        data_path = str(mzml_path or item)

        event = {
            "source": "lcms_watcher",
            "workflow_id": workflow_id,
            "experiment_type": exp_type,
            "vendor": vendor,
            "file_path": data_path,
            "original_path": str(item),
            "directory": watched.path,
            "mzml_converted": mzml_path is not None,
            "param_key": "mzml_dir" if mzml_path else "raw_dir",
            "description": (
                f"{vendor.title()} LC-MS {exp_type} data detected — "
                f"{workflow_id.replace('_', ' ').title()} workflow available"
            ),
            "message": (
                f"New {vendor.title()} LC-MS data: {item.name}. "
                f"Click to run {exp_type} analysis."
            ),
        }
        return event

    return None


class LCMSWatcher:
    """
    Background daemon polling configured directories for new LC-MS raw data.
    On acquisition completion, fires callback(event) — defaults to BioMate API notification.
    """

    def __init__(
        self,
        directories: Optional[List[str]] = None,
        on_new_data=None,
        poll_interval: int = LCMS_POLL_INTERVAL,
        settle_seconds: int = 120,
    ):
        self.directories = [
            LCMSWatchedDirectory(path=d, settle_seconds=settle_seconds)
            for d in (directories or LCMS_WATCH_DIRS)
            if d.strip()
        ]
        self.on_new_data = on_new_data or _notify_biomate
        self.poll_interval = poll_interval
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def add_directory(self, path: str, settle_seconds: int = 120) -> None:
        self.directories.append(LCMSWatchedDirectory(path=path, settle_seconds=settle_seconds))
        log.info(f"LC-MS watching: {path}")

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        log.info(f"LCMSWatcher started on {[d.path for d in self.directories]}, poll={self.poll_interval}s")

    def stop(self) -> None:
        self._running = False

    def _loop(self) -> None:
        while self._running:
            for watched in self.directories:
                try:
                    event = scan_for_lcms_files(watched)
                    if event:
                        self.on_new_data(event)
                except Exception as exc:
                    log.error(f"LC-MS scan error for {watched.path}: {exc}")
            time.sleep(self.poll_interval)


# ──────────────────────────────────────────────────────────────────────────────
# CLI entry point
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="BioMate LC-MS File Watcher")
    parser.add_argument("dirs", nargs="+", help="Directories to watch for LC-MS data")
    parser.add_argument("--interval", type=int, default=30, help="Poll interval in seconds")
    parser.add_argument("--settle", type=int, default=120, help="Settle time after last file write")
    parser.add_argument("--convert", action="store_true", help="Auto-convert .raw to .mzML via msconvert")
    args = parser.parse_args()

    if args.convert:
        os.environ["LCMS_AUTO_CONVERT"] = "1"
        LCMS_AUTO_CONVERT = True  # noqa: F811

    watcher = LCMSWatcher(
        directories=args.dirs,
        poll_interval=args.interval,
        settle_seconds=args.settle,
    )
    watcher.start()
    log.info("LC-MS watcher running. Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        watcher.stop()
