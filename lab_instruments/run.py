#!/usr/bin/env python3
"""
BioMate Lab Connectors — Unified Launcher
==========================================
Reads config.yaml and starts daemon threads for each enabled instrument.

Usage:
    python3 run.py --config config.yaml
    python3 run.py --config config.yaml --dry-run     # validate config only
    python3 run.py --list                              # list available connectors
"""
from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import time
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

# ── Resolve the integrations library path ────────────────────────────────────
_HERE = Path(__file__).resolve().parent
_LIB  = _HERE.parent / "lib" / "integrations"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML not installed. Run: pip install -r requirements.txt", file=sys.stderr)
    sys.exit(1)

log = logging.getLogger("biomate.connectors")

# ──────────────────────────────────────────────────────────────────────────────
# Runner helpers
# ──────────────────────────────────────────────────────────────────────────────

class ConnectorThread(threading.Thread):
    """
    Wraps a watcher with automatic restart-on-crash (up to max_restarts).
    """
    def __init__(self, name: str, factory, max_restarts: int = 5, restart_delay: int = 30):
        super().__init__(name=name, daemon=True)
        self.factory = factory
        self.max_restarts = max_restarts
        self.restart_delay = restart_delay
        self._watcher = None
        self._stop_event = threading.Event()

    def run(self):
        attempts = 0
        while not self._stop_event.is_set() and attempts <= self.max_restarts:
            try:
                log.info(f"[{self.name}] Starting (attempt {attempts + 1})")
                self._watcher = self.factory()
                self._watcher.start()
                # Block until stop is requested
                while not self._stop_event.is_set():
                    time.sleep(1)
                self._watcher.stop()
                return
            except Exception as exc:
                attempts += 1
                log.error(f"[{self.name}] Crashed: {exc}")
                if attempts <= self.max_restarts:
                    log.info(f"[{self.name}] Restarting in {self.restart_delay}s ...")
                    self._stop_event.wait(self.restart_delay)
        log.error(f"[{self.name}] Giving up after {attempts} attempts")

    def stop(self):
        self._stop_event.set()
        if self._watcher:
            try:
                self._watcher.stop()
            except Exception:
                pass


# ──────────────────────────────────────────────────────────────────────────────
# Connector factories — one per instrument type
# ──────────────────────────────────────────────────────────────────────────────

def _build_notify_fn(biomate_api_url: str, biomate_api_key: str):
    """Return a callback that POSTs instrument events to BioMate."""
    import requests

    def notify(event: Dict[str, Any]) -> bool:
        try:
            headers = {"Content-Type": "application/json"}
            if biomate_api_key:
                headers["Authorization"] = f"Bearer {biomate_api_key}"
            r = requests.post(
                f"{biomate_api_url}/api/instrument-events",
                json=event,
                headers=headers,
                timeout=10,
            )
            if r.status_code in (200, 201):
                log.info(f"Event emitted: {event.get('eventType')} from {event.get('instrumentType')}")
                return True
            log.warning(f"Event rejected: HTTP {r.status_code} — {r.text[:200]}")
            return False
        except Exception as exc:
            log.error(f"Failed to notify BioMate: {exc}")
            return False

    return notify


def build_minknow(cfg: Dict, notify_fn) -> Any:
    from nanopore_minknow_connector import MinKNOWConnector, MinKNOWWatcher
    connector = MinKNOWConnector(
        host=cfg.get("host", "localhost"),
        port=int(cfg.get("port", 9501)),
    )
    return MinKNOWWatcher(
        connector=connector,
        on_run_complete=notify_fn,
        poll_interval=int(cfg.get("poll_interval", 30)),
    )


def build_epu(cfg: Dict, notify_fn) -> Any:
    from cryoem_instrument_connector import EPUSessionWatcher
    dirs = cfg.get("output_dirs", [])
    if not dirs:
        raise ValueError("epu: output_dirs must be a non-empty list")
    return EPUSessionWatcher(
        directories=[str(d) for d in dirs],
        on_session_complete=notify_fn,
        poll_interval=int(cfg.get("poll_interval", 60)),
        min_movies=int(cfg.get("min_movies", 50)),
        settle_seconds=int(cfg.get("settle_seconds", 300)),
    )


def build_epu_api(cfg: Dict, notify_fn) -> Any:
    from cryoem_instrument_connector import EPUConnector, EPUAPIWatcher
    connector = EPUConnector(
        host=cfg.get("host", "localhost"),
        port=int(cfg.get("port", 8080)),
    )
    return EPUAPIWatcher(connector=connector, poll_interval=int(cfg.get("poll_interval", 60)))


def build_lcms(cfg: Dict, notify_fn) -> Any:
    from lcms_connector import LCMSWatcher
    dirs = cfg.get("output_dirs", [])
    if not dirs:
        raise ValueError("lcms: output_dirs must be a non-empty list")
    return LCMSWatcher(
        directories=[str(d) for d in dirs],
        on_new_data=notify_fn,
        poll_interval=int(cfg.get("poll_interval", 30)),
        settle_seconds=int(cfg.get("settle_seconds", 120)),
    )


def build_flow_cytometer(cfg: Dict, notify_fn) -> Any:
    from flow_cytometer_connector import FlowCytometerWatcher
    dirs = cfg.get("output_dirs", [])
    if not dirs:
        raise ValueError("flow_cytometer: output_dirs must be a non-empty list")
    return FlowCytometerWatcher(
        directories=[str(d) for d in dirs],
        on_new_data=notify_fn,
        poll_interval=int(cfg.get("poll_interval", 30)),
        settle_seconds=int(cfg.get("settle_seconds", 60)),
    )


def build_qpcr(cfg: Dict, notify_fn) -> Any:
    from qpcr_connector import QPCRWatcher
    dirs = cfg.get("output_dirs", [])
    if not dirs:
        raise ValueError("qpcr: output_dirs must be a non-empty list")
    return QPCRWatcher(
        directories=[str(d) for d in dirs],
        on_new_data=notify_fn,
        poll_interval=int(cfg.get("poll_interval", 30)),
        settle_seconds=int(cfg.get("settle_seconds", 30)),
    )


def build_plate_reader(cfg: Dict, notify_fn) -> Any:
    from plate_reader_connector import PlateReaderWatcher
    dirs = cfg.get("output_dirs", [])
    if not dirs:
        raise ValueError("plate_reader: output_dirs must be a non-empty list")
    return PlateReaderWatcher(
        directories=[str(d) for d in dirs],
        on_new_data=notify_fn,
        poll_interval=int(cfg.get("poll_interval", 30)),
        settle_seconds=int(cfg.get("settle_seconds", 30)),
    )


class _BaseSpacePollingWatcher:
    """Simple polling wrapper for BaseSpace connector (no native watcher class)."""
    def __init__(self, cfg: Dict, notify_fn):
        self.cfg = cfg
        self.notify_fn = notify_fn
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    def _loop(self):
        from illumina_basespace_connector import BaseSpaceConnector
        connector = BaseSpaceConnector(access_token=self.cfg["access_token"])
        project_id = self.cfg.get("project_id")
        interval = int(self.cfg.get("poll_interval", 300))
        seen_runs: set = set()

        while self._running:
            try:
                runs = connector.list_completed_runs(project_id=project_id)
                for run in runs:
                    run_id = run.get("Id") or run.get("id")
                    if run_id and run_id not in seen_runs:
                        seen_runs.add(run_id)
                        self.notify_fn({
                            "instrumentType": "basespace",
                            "eventType": "run_complete",
                            "payload": {
                                "runId": run_id,
                                "runName": run.get("Name") or run.get("name", run_id),
                                "projectId": project_id,
                                "workflowHint": self.cfg.get("workflow_hint"),
                            },
                        })
            except Exception as exc:
                log.warning(f"[basespace] Poll error: {exc}")
            time.sleep(interval)


class _BenchlingPollingWatcher:
    """Simple polling wrapper for Benchling connector."""
    def __init__(self, cfg: Dict, notify_fn):
        self.cfg = cfg
        self.notify_fn = notify_fn
        self._running = False

    def start(self):
        self._running = True
        threading.Thread(target=self._loop, daemon=True).start()

    def stop(self):
        self._running = False

    def _loop(self):
        from benchling_connector import BenchlingConnector
        bc = BenchlingConnector(
            api_url=self.cfg["api_url"],
            api_key=self.cfg["api_key"],
        )
        interval = int(self.cfg.get("poll_interval", 300))
        seen_entries: set = set()
        while self._running:
            try:
                entries = bc.list_pending_entries()
                for entry in entries:
                    eid = entry.get("id")
                    if eid and eid not in seen_entries:
                        seen_entries.add(eid)
                        inputs = bc.pull_workflow_inputs(entry_id=eid)
                        if inputs:
                            self.notify_fn({
                                "instrumentType": "benchling",
                                "eventType": "entry_ready",
                                "payload": {"entryId": eid, "inputs": inputs},
                            })
            except Exception as exc:
                log.warning(f"[benchling] Poll error: {exc}")
            time.sleep(interval)


class _SiLA2DiscoveryWatcher:
    """SiLA2 device discovery + status monitor."""
    def __init__(self, cfg: Dict, notify_fn):
        self.cfg = cfg
        self.notify_fn = notify_fn
        self._running = False

    def start(self):
        self._running = True
        threading.Thread(target=self._loop, daemon=True).start()

    def stop(self):
        self._running = False

    def _loop(self):
        import sila2_adapter
        import importlib
        ips = ",".join(str(ip) for ip in self.cfg.get("device_ips", []))
        port = int(self.cfg.get("port", 50051))
        os.environ["SILA2_DEVICE_IPS"] = ips
        importlib.reload(sila2_adapter)

        while self._running:
            try:
                devices = sila2_adapter.discover_devices()
                for ip in devices:
                    status = sila2_adapter.get_device_status(ip)
                    log.info(f"[sila2] {ip}:{port} → {status}")
            except Exception as exc:
                log.warning(f"[sila2] Discovery error: {exc}")
            time.sleep(60)


# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────

CONNECTOR_REGISTRY = {
    "basespace":      lambda cfg, fn: _BaseSpacePollingWatcher(cfg, fn),
    "minknow":        lambda cfg, fn: build_minknow(cfg, fn),
    "epu":            lambda cfg, fn: build_epu(cfg, fn),
    "epu_api":        lambda cfg, fn: build_epu_api(cfg, fn),
    "lcms":           lambda cfg, fn: build_lcms(cfg, fn),
    "flow_cytometer": lambda cfg, fn: build_flow_cytometer(cfg, fn),
    "qpcr":           lambda cfg, fn: build_qpcr(cfg, fn),
    "plate_reader":   lambda cfg, fn: build_plate_reader(cfg, fn),
    "benchling":      lambda cfg, fn: _BenchlingPollingWatcher(cfg, fn),
    "sila2":          lambda cfg, fn: _SiLA2DiscoveryWatcher(cfg, fn),
    # opentrons is event-driven (user-initiated), not a daemon — no polling needed
}


def main():
    parser = argparse.ArgumentParser(
        description="BioMate Lab Connectors — start all configured instrument daemons",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml (default: config.yaml)")
    parser.add_argument("--dry-run", action="store_true", help="Validate config and exit without starting daemons")
    parser.add_argument("--list", action="store_true", help="List available connector types and exit")
    args = parser.parse_args()

    if args.list:
        print("Available connector types:")
        for name in sorted(CONNECTOR_REGISTRY):
            print(f"  {name}")
        return

    # ── Load config ──────────────────────────────────────────────────────────
    config_path = Path(args.config).expanduser().resolve()
    if not config_path.exists():
        print(f"ERROR: Config file not found: {config_path}", file=sys.stderr)
        print(f"Hint: copy config.example.yaml → config.yaml and fill in your settings", file=sys.stderr)
        sys.exit(1)

    with open(config_path) as f:
        config = yaml.safe_load(f)

    # ── Set up logging ───────────────────────────────────────────────────────
    log_cfg = config.get("logging", {})
    log_level = getattr(logging, log_cfg.get("level", "INFO").upper(), logging.INFO)
    handlers = [logging.StreamHandler(sys.stdout)]
    log_file = log_cfg.get("file", "")
    if log_file:
        handlers.append(logging.FileHandler(log_file))
    logging.basicConfig(
        level=log_level,
        format="[%(asctime)s] %(levelname)s [%(name)s] %(message)s",
        handlers=handlers,
    )

    # ── BioMate connection ───────────────────────────────────────────────────
    bm = config.get("biomate", {})
    api_url = bm.get("api_url") or os.environ.get("BIOMATE_API_URL", "http://localhost:5000")
    api_key = bm.get("api_key") or os.environ.get("BIOMATE_API_KEY", "")

    if not api_url:
        print("ERROR: biomate.api_url is required in config.yaml", file=sys.stderr)
        sys.exit(1)

    log.info(f"BioMate API: {api_url}")
    notify_fn = _build_notify_fn(api_url, api_key)

    # ── Build and validate enabled connectors ────────────────────────────────
    instruments_cfg = config.get("instruments", {})
    threads: List[ConnectorThread] = []
    errors: List[str] = []

    for name, factory_fn in CONNECTOR_REGISTRY.items():
        cfg = instruments_cfg.get(name, {})
        if not cfg.get("enabled", False):
            continue

        try:
            # Validate by building (but don't start yet)
            log.info(f"Configuring connector: {name}")
            factory = lambda _cfg=cfg, _fn=notify_fn, _f=factory_fn: _f(_cfg, _fn)
            thread = ConnectorThread(name=name, factory=factory)
            threads.append(thread)
        except Exception as exc:
            errors.append(f"{name}: {exc}")

    if errors:
        for e in errors:
            log.error(f"Config error — {e}")
        sys.exit(1)

    if not threads:
        log.warning("No connectors enabled. Edit config.yaml and set enabled: true for at least one instrument.")
        sys.exit(0)

    log.info(f"Connectors to start: {[t.name for t in threads]}")

    if args.dry_run:
        print("Dry run: config is valid. Connectors that would start:")
        for t in threads:
            print(f"  ✓ {t.name}")
        return

    # ── Start all daemon threads ─────────────────────────────────────────────
    for t in threads:
        t.start()
        log.info(f"[{t.name}] Started")

    # ── Graceful shutdown on Ctrl+C / SIGTERM ────────────────────────────────
    def _shutdown(sig, frame):
        log.info("Shutdown signal received — stopping all connectors...")
        for t in threads:
            t.stop()
        log.info("All connectors stopped. Bye.")
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    print(f"\n✅ {len(threads)} connector(s) running. Press Ctrl+C to stop.\n")
    print("  " + "  ".join(t.name for t in threads))
    print()

    while True:
        time.sleep(5)


if __name__ == "__main__":
    main()
