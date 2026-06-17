"""
BioMate Instrument File Watcher
=================================
Daemon that watches NFS/S3/local directories for new instrument output files
and automatically creates BioMate sessions with pre-filled workflow suggestions.

Supported triggers:
  - CryoEM: new .mrc / .tif files → suggest CryoSPARC SPA workflow
  - Nanopore: new .pod5 / .fast5 files → suggest basecalling workflow
  - Illumina: new .fastq.gz files with sample sheet → suggest RNA-seq/WGS
  - Mass spec: new .mzML / .raw files → suggest DIA-NN or MetaboAnalyst workflow

Configuration via environment variables or YAML config file.
Deploy as a systemd service or Docker container alongside the sequencing server.
"""

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

import requests

log = logging.getLogger(__name__)

BIOMATE_API_URL = os.environ.get("BIOMATE_API_URL", "http://localhost:5000")
BIOMATE_API_KEY = os.environ.get("BIOMATE_API_KEY", "")
WATCH_DIRS = os.environ.get("INSTRUMENT_WATCH_DIRS", "").split(":") if os.environ.get("INSTRUMENT_WATCH_DIRS") else []
POLL_INTERVAL = int(os.environ.get("INSTRUMENT_POLL_INTERVAL", "30"))


# ──────────────────────────────────────────────────────────────────────────────
# File type → workflow mapping
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class InstrumentTrigger:
    """Defines what file pattern triggers which BioMate workflow."""
    extensions: List[str]            # e.g. ['.mrc', '.tif', '.tiff']
    workflow_id: str                 # BioMate workflow ID
    param_key: str                   # Parameter name to fill with the path
    description: str
    min_files: int = 1               # Minimum files before triggering
    notification_message: str = ""   # Message to send to user

INSTRUMENT_TRIGGERS: List[InstrumentTrigger] = [
    InstrumentTrigger(
        extensions=[".mrc", ".tif", ".tiff", ".eer"],
        workflow_id="cryosparc_standard_spa",
        param_key="movies_path",
        description="CryoEM movie frames detected — CryoSPARC SPA workflow available",
        min_files=10,
        notification_message=(
            "New cryo-EM data detected in {directory} ({count} files). "
            "Click to run CryoSPARC Standard SPA workflow."
        ),
    ),
    InstrumentTrigger(
        extensions=[".pod5"],
        workflow_id="nanopore_basecalling",
        param_key="input_path",
        description="Oxford Nanopore POD5 files detected — basecalling workflow available",
        min_files=1,
        notification_message=(
            "Nanopore sequencing data detected ({count} POD5 files). "
            "Click to run Dorado basecalling."
        ),
    ),
    InstrumentTrigger(
        extensions=[".fast5"],
        workflow_id="nanopore_basecalling",
        param_key="input_path",
        description="Oxford Nanopore FAST5 files detected",
        min_files=1,
        notification_message="Nanopore FAST5 files detected. Click to run basecalling.",
    ),
    InstrumentTrigger(
        extensions=[".fastq.gz", ".fq.gz"],
        workflow_id="rnaseq_differential",
        param_key="reads",
        description="FASTQ files detected — RNA-seq or WGS workflow available",
        min_files=2,
        notification_message=(
            "FASTQ files detected ({count} files). "
            "Click to run RNA-seq differential expression or WGS variant calling."
        ),
    ),
    InstrumentTrigger(
        extensions=[".mzml", ".mzML"],
        workflow_id="diann_proteomics",
        param_key="mzml_dir",
        description="mzML mass spectrometry data detected — DIA-NN proteomics workflow",
        min_files=1,
        notification_message="Mass spectrometry mzML files detected. Click to run DIA-NN proteomics.",
    ),
    InstrumentTrigger(
        extensions=[".raw", ".RAW"],
        workflow_id="diann_proteomics",
        param_key="raw_dir",
        description="Thermo .RAW files detected — convert to mzML first with msconvert",
        min_files=1,
        notification_message="Thermo .RAW files detected. BioMate will convert to mzML then run proteomics.",
    ),
]

# Extension → trigger lookup
_EXT_TO_TRIGGER: Dict[str, InstrumentTrigger] = {}
for _trigger in INSTRUMENT_TRIGGERS:
    for _ext in _trigger.extensions:
        _EXT_TO_TRIGGER[_ext.lower()] = _trigger


# ──────────────────────────────────────────────────────────────────────────────
# BioMate notification API
# ──────────────────────────────────────────────────────────────────────────────

def _notify_biomate(event: Dict[str, Any]) -> bool:
    """
    POST a new-data notification to BioMate.
    BioMate creates a session suggestion and notifies the lab's users.
    """
    headers = {"Authorization": f"Bearer {BIOMATE_API_KEY}"} if BIOMATE_API_KEY else {}
    try:
        r = requests.post(
            f"{BIOMATE_API_URL}/api/instruments/new-data",
            json=event,
            headers=headers,
            timeout=10,
        )
        r.raise_for_status()
        log.info(f"Notified BioMate: {event['workflow_id']} from {event['directory']}")
        return True
    except Exception as exc:
        log.error(f"BioMate notification failed: {exc}")
        return False


# ──────────────────────────────────────────────────────────────────────────────
# Directory scanner
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class WatchedDirectory:
    """State for a single watched directory."""
    path: str
    seen_files: Set[str] = field(default_factory=set)
    pending: Dict[str, List[str]] = field(default_factory=dict)  # workflow_id → [files]
    last_new_files: Dict[str, float] = field(default_factory=dict)  # workflow_id → timestamp
    settle_seconds: int = 60  # wait N seconds after last new file before triggering


def scan_directory(watched: WatchedDirectory) -> Optional[Dict[str, Any]]:
    """
    Scan a directory for new instrument files.
    Returns an event dict if a trigger threshold is met, else None.
    """
    dir_path = Path(watched.path)
    if not dir_path.exists():
        return None

    trigger_candidates: Dict[str, List[str]] = {}  # workflow_id → new file list

    for file_path in dir_path.rglob("*"):
        if not file_path.is_file():
            continue
        ext = "".join(file_path.suffixes[-2:]).lower()  # handles .fastq.gz
        if ext not in _EXT_TO_TRIGGER:
            ext = file_path.suffix.lower()
        if ext not in _EXT_TO_TRIGGER:
            continue
        key = str(file_path)
        if key in watched.seen_files:
            continue

        watched.seen_files.add(key)
        trigger = _EXT_TO_TRIGGER[ext]
        trigger_candidates.setdefault(trigger.workflow_id, []).append(key)
        watched.last_new_files[trigger.workflow_id] = time.time()

    # Check settle time: only fire after no new files for settle_seconds
    now = time.time()
    for workflow_id, new_files in trigger_candidates.items():
        watched.pending.setdefault(workflow_id, []).extend(new_files)

    for workflow_id, files in list(watched.pending.items()):
        last_new = watched.last_new_files.get(workflow_id, 0)
        trigger = next((t for t in INSTRUMENT_TRIGGERS if t.workflow_id == workflow_id), None)
        if not trigger:
            continue
        if len(files) < trigger.min_files:
            continue
        if now - last_new < watched.settle_seconds:
            continue  # Still receiving files — wait

        # Fire!
        event = {
            "workflow_id": workflow_id,
            "directory": watched.path,
            "file_count": len(files),
            "sample_files": files[:5],  # first 5 as preview
            "param_key": trigger.param_key,
            "description": trigger.description,
            "message": trigger.notification_message.format(
                directory=watched.path, count=len(files)
            ),
        }
        del watched.pending[workflow_id]
        return event

    return None


# ──────────────────────────────────────────────────────────────────────────────
# Watcher daemon
# ──────────────────────────────────────────────────────────────────────────────

class InstrumentWatcher:
    """
    Background daemon that polls watched directories for new instrument data.
    When a trigger threshold is met, calls on_new_data callback (or notifies BioMate API).
    """

    def __init__(
        self,
        directories: Optional[List[str]] = None,
        on_new_data: Optional[Callable[[Dict[str, Any]], None]] = None,
        poll_interval: int = POLL_INTERVAL,
        settle_seconds: int = 60,
    ):
        self.directories = [
            WatchedDirectory(path=d, settle_seconds=settle_seconds)
            for d in (directories or WATCH_DIRS)
            if d.strip()
        ]
        self.on_new_data = on_new_data or _notify_biomate
        self.poll_interval = poll_interval
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def add_directory(self, path: str, settle_seconds: int = 60) -> None:
        """Add a directory to watch at runtime."""
        self.directories.append(WatchedDirectory(path=path, settle_seconds=settle_seconds))
        log.info(f"Watching: {path}")

    def start(self) -> None:
        """Start background polling thread."""
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        dirs = [d.path for d in self.directories]
        log.warning(f"InstrumentWatcher started: {dirs}, poll={self.poll_interval}s")

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)

    def _loop(self) -> None:
        while self._running:
            for watched in self.directories:
                try:
                    event = scan_directory(watched)
                    if event:
                        self.on_new_data(event)
                except Exception as exc:
                    log.error(f"Scan error for {watched.path}: {exc}")
            time.sleep(self.poll_interval)


# ──────────────────────────────────────────────────────────────────────────────
# CLI entry point
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="BioMate Instrument File Watcher")
    parser.add_argument("dirs", nargs="+", help="Directories to watch")
    parser.add_argument("--interval", type=int, default=30, help="Poll interval seconds")
    parser.add_argument("--settle", type=int, default=60, help="Settle time after last new file")
    args = parser.parse_args()

    watcher = InstrumentWatcher(
        directories=args.dirs,
        poll_interval=args.interval,
        settle_seconds=args.settle,
    )
    watcher.start()
    log.info("Watching. Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        watcher.stop()
