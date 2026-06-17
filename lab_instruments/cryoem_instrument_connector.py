"""
CryoEM Instrument Connector
==============================
Connects BioMate to Thermo Fisher cryo-electron microscopes (Titan Krios, Talos Arctica,
Glacios) running EPU (EM data acquisition software) and monitors active sessions.

Two integration paths:
  1. **EPU session directory watcher** (file-based, no API needed)
     - EPU writes .mrc / .tif / .eer movie frames to a configured output directory
     - Watch the EPU session folder; on acquisition completion, trigger CryoSPARC workflow
  2. **EPU REST API** (EPU 3.0+, Thermo Fisher Scientific)
     - EPU exposes a local REST API on port 8080 (on the microscope PC)
     - Can read session status, grid atlas, and acquisition parameters

Also supports:
  - JEOL (cryoARM): writes .mrc files to a network share — file-watcher path
  - FEI/Thermo Talos: same EPU software as Krios

Environment variables:
    EPU_HOST             EPU server hostname/IP (default: localhost)
    EPU_PORT             EPU REST API port (default: 8080)
    EPU_OUTPUT_DIR       Root directory where EPU writes session data
    CRYOEM_POLL_INTERVAL Poll interval in seconds (default: 60)
    BIOMATE_API_URL      BioMate API URL
    BIOMATE_API_KEY      BioMate service API key

EPU REST API reference:
    https://fei-software-center.github.io/TargetingAPI/ (TargetingAPI)
    Contact Thermo Fisher support for EPU API access
"""

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import requests

log = logging.getLogger(__name__)

EPU_HOST = os.environ.get("EPU_HOST", "localhost")
EPU_PORT = int(os.environ.get("EPU_PORT", "8080"))
EPU_OUTPUT_DIR = os.environ.get("EPU_OUTPUT_DIR", "")
CRYOEM_POLL_INTERVAL = int(os.environ.get("CRYOEM_POLL_INTERVAL", "60"))
BIOMATE_API_URL = os.environ.get("BIOMATE_API_URL", "http://localhost:5000")
BIOMATE_API_KEY = os.environ.get("BIOMATE_API_KEY", "")


# ──────────────────────────────────────────────────────────────────────────────
# EPU REST API client
# ──────────────────────────────────────────────────────────────────────────────

class EPUConnector:
    """
    Client for EPU's local REST API (EPU 3.0+).

    EPU manages automated data acquisition on Titan Krios, Talos Arctica,
    and Glacios microscopes. The REST API provides session status and metadata.

    Note: The EPU API is only accessible from the microscope's local network.
    In most facilities, BioMate runs on an analysis server that accesses EPU
    via the instrument NFS share — use EPUSessionWatcher for file-based monitoring.
    """

    def __init__(self, host: str = EPU_HOST, port: int = EPU_PORT):
        self.base_url = f"http://{host}:{port}/api/v1"
        self.session = requests.Session()
        self.session.headers["Accept"] = "application/json"
        self.session.headers["User-Agent"] = "BioMate-CryoEM/1.0"

    def health(self) -> Dict[str, Any]:
        """Check EPU API health. Returns version and instrument type."""
        try:
            r = self.session.get(f"{self.base_url}/version", timeout=5)
            r.raise_for_status()
            return r.json()
        except requests.RequestException as exc:
            return {"error": str(exc), "reachable": False}

    def list_sessions(self) -> List[Dict[str, Any]]:
        """
        List EPU sessions. Each session represents a cryo-EM experiment.
        Returns: [{id, name, status, created, microscopeId, sampleName, ...}]
        """
        try:
            r = self.session.get(f"{self.base_url}/sessions", timeout=10)
            r.raise_for_status()
            return r.json() if isinstance(r.json(), list) else r.json().get("sessions", [])
        except Exception as exc:
            log.warning(f"EPU session list failed: {exc}")
            return []

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Get detailed session info including acquisition statistics."""
        try:
            r = self.session.get(f"{self.base_url}/sessions/{session_id}", timeout=10)
            r.raise_for_status()
            return r.json()
        except Exception:
            return None

    def get_session_stats(self, session_id: str) -> Optional[Dict[str, Any]]:
        """
        Get acquisition statistics: movies collected, CTF quality, ice thickness,
        estimated completion time.
        """
        try:
            r = self.session.get(f"{self.base_url}/sessions/{session_id}/stats", timeout=10)
            r.raise_for_status()
            return r.json()
        except Exception:
            return None

    def detect_completed_sessions(self) -> List[Dict[str, Any]]:
        """
        Return sessions that have recently completed (status = 'Finished').
        Includes estimated output path from session metadata.
        """
        completed = []
        for session in self.list_sessions():
            status = session.get("status", "").lower()
            if status in ("finished", "complete", "completed"):
                stats = self.get_session_stats(session.get("id", ""))
                completed.append({
                    "session_id": session.get("id"),
                    "session_name": session.get("name"),
                    "sample_name": session.get("sampleName", ""),
                    "microscope_id": session.get("microscopeId", ""),
                    "movie_count": (stats or {}).get("totalMovies", 0),
                    "voltage_kv": session.get("accelerationVoltage", 300),
                    "pixel_size_angstrom": session.get("pixelSize", 1.0),
                    "output_directory": session.get("outputDirectory", EPU_OUTPUT_DIR),
                    "status": status,
                    "completed_at": session.get("endTime"),
                })
        return completed

    def session_to_biomate_params(self, session: Dict[str, Any]) -> Dict[str, Any]:
        """
        Convert a completed EPU session to BioMate workflow parameters
        for the cryosparc_standard_spa workflow.
        """
        output_dir = session.get("output_directory", "")
        return {
            "movies_path": output_dir,
            "sample_name": session.get("sample_name", "sample"),
            "voltage": session.get("voltage_kv", 300),
            "pixel_size": session.get("pixel_size_angstrom", 1.0),
            "movie_count": session.get("movie_count", 0),
            "suggested_workflow": "cryosparc_standard_spa",
            "session_id": session.get("session_id"),
            "session_name": session.get("session_name"),
            "microscope": session.get("microscope_id", "unknown"),
        }


# ──────────────────────────────────────────────────────────────────────────────
# File-based session watcher (no EPU API required)
# ──────────────────────────────────────────────────────────────────────────────

# CryoEM movie extensions
_CRYO_EXTENSIONS = {".mrc", ".tif", ".tiff", ".eer", ".mrcs"}

# Minimum movie count before triggering (partial acquisitions are common)
DEFAULT_MIN_MOVIES = 50


@dataclass
class CryoEMWatchedDirectory:
    """State for a watched EPU session output directory."""
    path: str
    min_movies: int = DEFAULT_MIN_MOVIES
    settle_seconds: int = 300  # 5 min; EPU can produce bursts then pause
    seen_files: Set[str] = field(default_factory=set)
    movie_count: int = 0
    last_new_file_time: float = 0.0
    triggered: bool = False  # Only trigger once per session


def _parse_epu_metadata(session_dir: Path) -> Dict[str, Any]:
    """
    Parse EPU session metadata from the standard EPU directory structure.

    EPU session directories contain:
      - <session_name>/Metadata/<grid>.dm  (EPU-generated metadata)
      - <session_name>/Images-Disc1/GridSquare_*/Data/*.mrc  (movie frames)

    Try to extract: pixel_size, voltage, magnification, defocus_range.
    Returns empty dict if metadata not readable.
    """
    metadata: Dict[str, Any] = {}
    try:
        # Find EPU XML metadata files
        for xml_file in session_dir.rglob("*.xml"):
            if xml_file.stat().st_size < 50_000:  # Skip large data files
                try:
                    import xml.etree.ElementTree as ET
                    tree = ET.parse(xml_file)
                    root = tree.getroot()
                    # EPU metadata uses namespaces; extract key values
                    for elem in root.iter():
                        tag = elem.tag.split("}")[-1]  # strip namespace
                        if tag in ("NominalMagnification", "AccelerationVoltage",
                                   "PixelSize", "NominalDefocus"):
                            try:
                                metadata[tag] = float(elem.text or "0")
                            except (ValueError, TypeError):
                                pass
                    if metadata:
                        break
                except Exception:
                    continue
    except Exception as exc:
        log.debug(f"EPU metadata parse failed: {exc}")
    return metadata


def scan_epu_directory(watched: CryoEMWatchedDirectory) -> Optional[Dict[str, Any]]:
    """
    Scan an EPU session directory for new movie frames.
    Triggers a workflow when:
      - ≥ min_movies new frames found
      - No new frames for settle_seconds (acquisition complete)
    """
    if watched.triggered:
        return None

    dir_path = Path(watched.path)
    if not dir_path.exists():
        return None

    # Scan for new movie files
    for file_path in dir_path.rglob("*"):
        if not file_path.is_file():
            continue
        if file_path.suffix.lower() not in _CRYO_EXTENSIONS:
            continue
        key = str(file_path)
        if key in watched.seen_files:
            continue

        watched.seen_files.add(key)
        watched.movie_count += 1
        watched.last_new_file_time = time.time()

    # Check trigger conditions
    now = time.time()
    if watched.movie_count < watched.min_movies:
        return None
    if watched.last_new_file_time == 0:
        return None
    if (now - watched.last_new_file_time) < watched.settle_seconds:
        log.debug(f"CryoEM settling: {watched.movie_count} movies, last {now - watched.last_new_file_time:.0f}s ago")
        return None

    # Fire!
    watched.triggered = True
    metadata = _parse_epu_metadata(dir_path)
    voltage = metadata.get("AccelerationVoltage", 300)
    pixel_size = metadata.get("PixelSize", 1.0)
    if pixel_size and pixel_size > 10:  # EPU stores in meters or Angstroms depending on version
        pixel_size = pixel_size * 1e10  # m → Angstroms

    event = {
        "source": "epu_session_watcher",
        "workflow_id": "cryosparc_standard_spa",
        "directory": watched.path,
        "movies_path": watched.path,
        "movie_count": watched.movie_count,
        "voltage_kv": int(voltage) if voltage else 300,
        "pixel_size_angstrom": round(pixel_size, 3) if pixel_size else 1.0,
        "param_key": "movies_path",
        "description": (
            f"CryoEM session complete: {watched.movie_count} movies in {dir_path.name}. "
            "CryoSPARC Standard SPA workflow available."
        ),
        "message": (
            f"CryoEM acquisition complete ({watched.movie_count} movies). "
            "Click to run CryoSPARC SPA workflow."
        ),
    }
    return event


# ──────────────────────────────────────────────────────────────────────────────
# BioMate notification
# ──────────────────────────────────────────────────────────────────────────────

def _notify_biomate(event: Dict[str, Any]) -> bool:
    """POST cryo-EM session event to BioMate instruments endpoint."""
    headers = {"Authorization": f"Bearer {BIOMATE_API_KEY}"} if BIOMATE_API_KEY else {}
    try:
        r = requests.post(
            f"{BIOMATE_API_URL}/api/instruments/new-data",
            json=event,
            headers=headers,
            timeout=10,
        )
        r.raise_for_status()
        log.info(f"BioMate notified: cryo-EM {event['movie_count']} movies from {event['directory']}")
        return True
    except Exception as exc:
        log.error(f"BioMate notification failed: {exc}")
        return False


# ──────────────────────────────────────────────────────────────────────────────
# EPU session watcher daemon
# ──────────────────────────────────────────────────────────────────────────────

class EPUSessionWatcher:
    """
    Background daemon that watches EPU output directories for completed sessions.
    Works without EPU API access — monitors the NFS share where EPU writes data.

    Typical EPU output structure:
        /mnt/krios1/<date>_<samplename>/
            Images-Disc1/
                GridSquare_*/
                    Data/
                        FoilHole_*_fractions.mrc   ← movie frames
            Metadata/
                *.xml  ← session parameters
    """

    def __init__(
        self,
        directories: Optional[List[str]] = None,
        on_session_complete=None,
        poll_interval: int = CRYOEM_POLL_INTERVAL,
        min_movies: int = DEFAULT_MIN_MOVIES,
        settle_seconds: int = 300,
    ):
        base_dirs = directories or ([EPU_OUTPUT_DIR] if EPU_OUTPUT_DIR else [])
        self.directories: List[CryoEMWatchedDirectory] = [
            CryoEMWatchedDirectory(
                path=d.strip(),
                min_movies=min_movies,
                settle_seconds=settle_seconds,
            )
            for d in base_dirs if d.strip()
        ]
        self.on_session_complete = on_session_complete or _notify_biomate
        self.poll_interval = poll_interval
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def add_session_directory(
        self,
        path: str,
        min_movies: int = DEFAULT_MIN_MOVIES,
        settle_seconds: int = 300,
    ) -> None:
        """
        Add a specific EPU session directory to watch.
        Call this when a new session starts (e.g., triggered by EPU API hook).
        """
        watched = CryoEMWatchedDirectory(
            path=path,
            min_movies=min_movies,
            settle_seconds=settle_seconds,
        )
        self.directories.append(watched)
        log.info(f"CryoEM watching session: {path}")

    def start(self) -> None:
        """Start background polling thread."""
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        log.info(
            f"EPUSessionWatcher started: {[d.path for d in self.directories]}, "
            f"poll={self.poll_interval}s, min_movies={self.directories[0].min_movies if self.directories else 'N/A'}"
        )

    def stop(self) -> None:
        """Stop the background polling thread."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)

    def _loop(self) -> None:
        while self._running:
            for watched in self.directories:
                try:
                    event = scan_epu_directory(watched)
                    if event:
                        self.on_session_complete(event)
                except Exception as exc:
                    log.error(f"CryoEM scan error for {watched.path}: {exc}")
            time.sleep(self.poll_interval)


# ──────────────────────────────────────────────────────────────────────────────
# EPU API watcher (uses REST API when available)
# ──────────────────────────────────────────────────────────────────────────────

class EPUAPIWatcher:
    """
    Uses the EPU REST API (EPU 3.0+) to detect completed sessions.
    Falls back gracefully if EPU API is not reachable.
    """

    def __init__(
        self,
        connector: EPUConnector,
        on_session_complete=None,
        poll_interval: int = CRYOEM_POLL_INTERVAL,
    ):
        self.connector = connector
        self.on_session_complete = on_session_complete or _notify_biomate
        self.poll_interval = poll_interval
        self._seen_sessions: Set[str] = set()
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        health = self.connector.health()
        if health.get("reachable") is False or "error" in health:
            log.warning(
                f"EPU API not reachable at {self.connector.base_url}: {health.get('error')}. "
                "Falling back to file-based watching."
            )
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        log.info(f"EPUAPIWatcher started (EPU at {self.connector.base_url})")

    def stop(self) -> None:
        self._running = False

    def _loop(self) -> None:
        while self._running:
            try:
                sessions = self.connector.detect_completed_sessions()
                for session in sessions:
                    sid = session.get("session_id")
                    if sid and sid not in self._seen_sessions:
                        self._seen_sessions.add(sid)
                        params = self.connector.session_to_biomate_params(session)
                        event = {
                            "source": "epu_api",
                            "workflow_id": params["suggested_workflow"],
                            "directory": params["movies_path"],
                            "movies_path": params["movies_path"],
                            "movie_count": params["movie_count"],
                            "voltage_kv": params["voltage"],
                            "pixel_size_angstrom": params["pixel_size"],
                            "session_id": params["session_id"],
                            "session_name": params["session_name"],
                            "param_key": "movies_path",
                            "description": f"EPU session {params['session_name']} complete — CryoSPARC SPA available",
                            "message": (
                                f"CryoEM session '{params['session_name']}' finished "
                                f"({params['movie_count']} movies). Click to run CryoSPARC."
                            ),
                        }
                        log.info(f"EPU session complete: {params['session_name']}")
                        try:
                            self.on_session_complete(event)
                        except Exception as cb_exc:
                            log.error(f"Session complete callback failed: {cb_exc}")
            except Exception as exc:
                log.warning(f"EPU API poll error: {exc}")
            time.sleep(self.poll_interval)


# ──────────────────────────────────────────────────────────────────────────────
# CLI entry point
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="BioMate CryoEM EPU Session Watcher")
    parser.add_argument("dirs", nargs="*", help="EPU session output directories to watch")
    parser.add_argument("--epu-host", default=EPU_HOST, help="EPU REST API host")
    parser.add_argument("--epu-port", type=int, default=EPU_PORT, help="EPU REST API port")
    parser.add_argument("--interval", type=int, default=60, help="Poll interval seconds")
    parser.add_argument("--min-movies", type=int, default=50, help="Min movies before triggering")
    parser.add_argument("--settle", type=int, default=300, help="Settle time after last movie (seconds)")
    parser.add_argument("--api", action="store_true", help="Use EPU REST API instead of file watching")
    args = parser.parse_args()

    if args.api:
        connector = EPUConnector(host=args.epu_host, port=args.epu_port)
        watcher: Any = EPUAPIWatcher(connector, poll_interval=args.interval)
    else:
        dirs = args.dirs or ([EPU_OUTPUT_DIR] if EPU_OUTPUT_DIR else [])
        if not dirs:
            parser.error("Specify directories to watch, or set EPU_OUTPUT_DIR env var")
        watcher = EPUSessionWatcher(
            directories=dirs,
            poll_interval=args.interval,
            min_movies=args.min_movies,
            settle_seconds=args.settle,
        )

    watcher.start()
    log.info("CryoEM watcher running. Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        watcher.stop()
