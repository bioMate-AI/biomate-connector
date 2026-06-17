"""
qPCR Instrument Connector
============================
Bridges quantitative PCR instruments to BioMate's genomics and molecular biology
analysis workflows.

Supported instruments (file-based export):
  - Bio-Rad CFX Manager: .pcrd (SQLite) and .csv export → RT-qPCR, ChIP-qPCR, CRISPR screen
  - Applied Biosystems QuantStudio / StepOnePlus: .eds (ZIP+XML) and .txt export
  - Roche LightCycler 96 / 480: .ixo (ZIP+XML) and .xls export
  - QIAGEN Rotor-Gene Q: .rex (XML)
  - BioMolecular Systems MIC: .micpcr (JSON/ZIP)

Integration with BioMate analysis:
  - RT-qPCR Cq data → rnaseq_differential (as pre-computed Cq matrix)
  - qPCR standard curve → quantification workflow
  - CRISPR screen qPCR → crispr_screen_mageck
  - ChIP-qPCR enrichment → chipseq_analysis

Key analysis: ddCt normalization, efficiency correction, melt curve analysis.

Environment variables:
    QPCR_WATCH_DIRS       Colon-separated CFX/QuantStudio export directories
    QPCR_POLL_INTERVAL    Poll interval in seconds (default: 30)
    BIOMATE_API_URL       BioMate API URL
    BIOMATE_API_KEY       BioMate service API key
"""

import json
import logging
import os
import re
import sqlite3
import threading
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import requests

log = logging.getLogger(__name__)

WATCH_DIRS = os.environ.get("QPCR_WATCH_DIRS", "").split(":") \
    if os.environ.get("QPCR_WATCH_DIRS") else []
POLL_INTERVAL = int(os.environ.get("QPCR_POLL_INTERVAL", "30"))
BIOMATE_API_URL = os.environ.get("BIOMATE_API_URL", "http://localhost:5000")
BIOMATE_API_KEY = os.environ.get("BIOMATE_API_KEY", "")

# File extensions from qPCR software
_QPCR_EXTENSIONS = {
    ".pcrd",    # Bio-Rad CFX Manager (SQLite database)
    ".csv",     # Generic Cq export
    ".eds",     # ABI QuantStudio (ZIP archive with results_amplification_data.txt)
    ".ixo",     # Roche LightCycler (ZIP archive)
    ".rex",     # Rotor-Gene Q (XML)
    ".micpcr",  # BioMolecular Systems MIC (JSON)
    ".txt",     # Generic tab-delimited qPCR export
}


# ──────────────────────────────────────────────────────────────────────────────
# Instrument-specific readers
# ──────────────────────────────────────────────────────────────────────────────

class QPCRVendor:
    BIORAD = "biorad_cfx"
    ABI = "applied_biosystems"
    ROCHE = "roche_lightcycler"
    ROTOR_GENE = "rotor_gene"
    MIC = "bms_mic"
    GENERIC = "generic"


def detect_qpcr_vendor(path: Path) -> str:
    ext = path.suffix.lower()
    if ext == ".pcrd":
        return QPCRVendor.BIORAD
    if ext == ".eds":
        return QPCRVendor.ABI
    if ext == ".ixo":
        return QPCRVendor.ROCHE
    if ext == ".rex":
        return QPCRVendor.ROTOR_GENE
    if ext == ".micpcr":
        return QPCRVendor.MIC
    return QPCRVendor.GENERIC


def read_biorad_pcrd(path: Path) -> Dict[str, Any]:
    """
    Read metadata from a Bio-Rad CFX .pcrd file (SQLite database).
    Extracts: run name, assay type (RT-qPCR / DNA quantification), plate setup.
    """
    metadata: Dict[str, Any] = {"vendor": QPCRVendor.BIORAD}
    try:
        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row

        # Run info table
        cur = conn.cursor()
        try:
            cur.execute("SELECT * FROM RunInfo LIMIT 1")
            row = cur.fetchone()
            if row:
                info = dict(row)
                metadata["run_name"] = info.get("RunName", "")
                metadata["experiment_type"] = info.get("ExperimentType", "")
                metadata["date"] = info.get("CreatedDate", "")
                metadata["user"] = info.get("UserName", "")
        except sqlite3.OperationalError:
            pass

        # Sample count
        try:
            cur.execute("SELECT COUNT(DISTINCT SampleName) as n FROM Well")
            row = cur.fetchone()
            metadata["sample_count"] = row["n"] if row else 0
        except sqlite3.OperationalError:
            pass

        # Target/gene names
        try:
            cur.execute("SELECT DISTINCT TargetName FROM Well")
            metadata["targets"] = [r["TargetName"] for r in cur.fetchall() if r["TargetName"]]
        except sqlite3.OperationalError:
            pass

        conn.close()
    except Exception as exc:
        log.debug(f"CFX .pcrd read failed: {exc}")
    return metadata


def read_quantstudio_eds(path: Path) -> Dict[str, Any]:
    """
    Read metadata from an ABI QuantStudio .eds file (ZIP archive).
    Extracts experiment name, targets, and plate setup from the XML manifest.
    """
    metadata: Dict[str, Any] = {"vendor": QPCRVendor.ABI}
    try:
        with zipfile.ZipFile(str(path), "r") as zf:
            names = zf.namelist()
            # Look for manifest or results XML
            xml_files = [n for n in names if n.endswith(".xml") or n.endswith(".txt")]
            for xml_name in xml_files[:3]:
                try:
                    content = zf.read(xml_name).decode("utf-8", errors="replace")
                    if "Experiment" in content:
                        # Extract experiment name
                        m = re.search(r"<Experiment[^>]*>(.*?)</Experiment>", content, re.IGNORECASE)
                        if m:
                            metadata["run_name"] = m.group(1)[:100]
                        # Extract targets
                        targets = re.findall(r"<Target[^>]*>([^<]+)</Target>", content)
                        if targets:
                            metadata["targets"] = list(set(targets))
                        metadata["file_structure"] = "eds_archive"
                        break
                except Exception:
                    continue
    except Exception as exc:
        log.debug(f"QuantStudio .eds read failed: {exc}")
    return metadata


def read_qpcr_metadata(path: Path) -> Dict[str, Any]:
    """Dispatch to vendor-specific reader."""
    vendor = detect_qpcr_vendor(path)
    if vendor == QPCRVendor.BIORAD:
        return read_biorad_pcrd(path)
    if vendor == QPCRVendor.ABI:
        return read_quantstudio_eds(path)
    # Generic: check filename
    return {"vendor": vendor, "run_name": path.stem}


# ──────────────────────────────────────────────────────────────────────────────
# Experiment type detection
# ──────────────────────────────────────────────────────────────────────────────

_RTPCR_RE = re.compile(r"\b(RT.qPCR|RT-PCR|mRNA|cDNA|transcr|expression|gene.expr)\b", re.IGNORECASE)
_CRISPR_RE = re.compile(r"\b(CRISPR|screen|sgRNA|guide|indel)\b", re.IGNORECASE)
_CHIP_RE = re.compile(r"\b(ChIP|chromatin|immuno.precip|H3K|histone)\b", re.IGNORECASE)
_QUANT_RE = re.compile(r"\b(quantif|standard.curve|absolute|copy.num|ddPCR)\b", re.IGNORECASE)
_GENO_RE = re.compile(r"\b(genotyp|SNP|allele|discriminat|HRM)\b", re.IGNORECASE)


def detect_qpcr_experiment_type(path: Path, metadata: Dict[str, Any]) -> str:
    search = " ".join([
        path.stem, path.parent.name,
        metadata.get("run_name", ""),
        metadata.get("experiment_type", ""),
        " ".join(metadata.get("targets", [])),
    ])
    if _CRISPR_RE.search(search):
        return "crispr_screen_qpcr"
    if _CHIP_RE.search(search):
        return "chip_qpcr"
    if _GENO_RE.search(search):
        return "genotyping_qpcr"
    if _QUANT_RE.search(search):
        return "absolute_quantification"
    if _RTPCR_RE.search(search):
        return "rt_qpcr_expression"
    return "rt_qpcr_expression"  # default


_WORKFLOW_MAP = {
    "rt_qpcr_expression": "qpcr_differential_expression",
    "crispr_screen_qpcr": "crispr_screen_mageck",
    "chip_qpcr": "chipseq_analysis",
    "absolute_quantification": "qpcr_quantification",
    "genotyping_qpcr": "qpcr_genotyping",
}

_ANALYSIS_FUNCTION_MAP = {
    "rt_qpcr_expression": "molecular_biology.pcr_simple (Cq matrix → ddCt normalization)",
    "crispr_screen_qpcr": "genetics.perform_pcr_and_gel_electrophoresis",
    "absolute_quantification": "biochemistry.analyze_enzyme_kinetics_assay (standard curve fit)",
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
        log.info(f"BioMate notified: qPCR {event['experiment_type']} — {event['file_path']}")
        return True
    except Exception as exc:
        log.error(f"BioMate notification failed: {exc}")
        return False


# ──────────────────────────────────────────────────────────────────────────────
# File watcher
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class QPCRWatchedDir:
    path: str
    seen_files: Set[str] = field(default_factory=set)
    settle_seconds: int = 30


def scan_for_qpcr_files(watched: QPCRWatchedDir) -> Optional[Dict[str, Any]]:
    dir_path = Path(watched.path)
    if not dir_path.exists():
        return None

    for file_path in dir_path.rglob("*"):
        if not file_path.is_file():
            continue

        ext = file_path.suffix.lower()
        # Distinguish qPCR .txt/.csv from generic text files by parent dir or name
        if ext in (".txt", ".csv"):
            name_lower = file_path.name.lower()
            if not any(k in name_lower for k in ("pcr", "qpcr", "cq", "ct ", "cycle", "amp", "melt")):
                if not any(k in file_path.parent.name.lower() for k in ("pcr", "qpcr", "cfx", "quantstudio")):
                    continue

        if ext not in _QPCR_EXTENSIONS:
            continue

        key = str(file_path)
        if key in watched.seen_files:
            continue

        try:
            mtime = file_path.stat().st_mtime
        except OSError:
            continue
        if (time.time() - mtime) < watched.settle_seconds:
            continue

        watched.seen_files.add(key)

        vendor = detect_qpcr_vendor(file_path)
        metadata = read_qpcr_metadata(file_path)
        exp_type = detect_qpcr_experiment_type(file_path, metadata)
        workflow_id = _WORKFLOW_MAP.get(exp_type, "qpcr_differential_expression")
        analysis_fn = _ANALYSIS_FUNCTION_MAP.get(exp_type, "")

        event = {
            "source": "qpcr_watcher",
            "workflow_id": workflow_id,
            "experiment_type": exp_type,
            "file_path": key,
            "directory": watched.path,
            "param_key": "qpcr_data_file",
            "vendor": vendor,
            "run_name": metadata.get("run_name", file_path.stem),
            "targets": metadata.get("targets", []),
            "sample_count": metadata.get("sample_count", 0),
            "analysis_function": analysis_fn,
            "description": (
                f"qPCR run complete: {metadata.get('run_name', file_path.stem)} "
                f"({exp_type.replace('_', ' ')}, {vendor}). "
                f"Targets: {', '.join(metadata.get('targets', [])[:5])}."
            ),
            "message": (
                f"New qPCR data: {file_path.name} ({exp_type.replace('_', ' ')}). "
                "Click to run analysis."
            ),
        }
        return event

    return None


class QPCRWatcher:
    """
    Background daemon watching CFX Manager / QuantStudio export directories.
    Detects completed qPCR runs and triggers BioMate differential expression,
    CRISPR screen, or quantification workflows.

    Bridges to existing analysis:
      - molecular_biology.pcr_simple()
      - genetics.perform_pcr_and_gel_electrophoresis()
      - biochemistry.analyze_enzyme_kinetics_assay() (standard curve)
    """

    def __init__(
        self,
        directories: Optional[List[str]] = None,
        on_new_data=None,
        poll_interval: int = POLL_INTERVAL,
        settle_seconds: int = 30,
    ):
        self.directories = [
            QPCRWatchedDir(path=d, settle_seconds=settle_seconds)
            for d in (directories or WATCH_DIRS) if d.strip()
        ]
        self.on_new_data = on_new_data or _notify_biomate
        self.poll_interval = poll_interval
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def add_directory(self, path: str, settle_seconds: int = 30) -> None:
        self.directories.append(QPCRWatchedDir(path=path, settle_seconds=settle_seconds))
        log.info(f"qPCR watching: {path}")

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        log.info(f"QPCRWatcher started: {[d.path for d in self.directories]}")

    def stop(self) -> None:
        self._running = False

    def _loop(self) -> None:
        while self._running:
            for watched in self.directories:
                try:
                    event = scan_for_qpcr_files(watched)
                    if event:
                        self.on_new_data(event)
                except Exception as exc:
                    log.error(f"qPCR scan error for {watched.path}: {exc}")
            time.sleep(self.poll_interval)


if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="BioMate qPCR File Watcher")
    parser.add_argument("dirs", nargs="+", help="CFX Manager / QuantStudio export directories")
    parser.add_argument("--interval", type=int, default=30)
    parser.add_argument("--settle", type=int, default=30)
    args = parser.parse_args()
    watcher = QPCRWatcher(directories=args.dirs, poll_interval=args.interval, settle_seconds=args.settle)
    watcher.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        watcher.stop()
