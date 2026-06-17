"""
OpenTrons Robot Connector
=========================
Connects BioMate to OpenTrons OT-2 and Flex liquid-handling robots via
the OpenTrons HTTP API (local, no internet required).

API reference:
  OT-2:  http://<robot-ip>/server/  (legacy)
  Flex:  http://<robot-ip>/           (v6 REST API)

Usage:
    connector = OpenTronsConnector("http://192.168.1.42")
    robots = connector.health_check()
    protocol_id = connector.upload_protocol(python_code)
    run_id = connector.start_run(protocol_id)
    status = connector.get_run_status(run_id)
    outputs = connector.get_run_outputs(run_id)

Environment variables:
    OPENTRONS_ROBOT_IPS   Comma-separated list of robot IPs to scan (optional)
"""

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

import requests

log = logging.getLogger(__name__)

# Default network scan range (RFC1918 /24 subnets where OT-2 typically lands)
DEFAULT_SCAN_CIDRS = [
    "192.168.1.{}",
    "192.168.0.{}",
    "10.0.0.{}",
]
OT2_PORT = 31950          # OT-2 legacy API port
FLEX_PORT = 31950         # Flex uses same port
HTTP_TIMEOUT = 10         # seconds


class OpenTronsConnector:
    """
    Client for a single OpenTrons robot.

    Supports both OT-2 (API v2, Python protocols) and Flex (API v6, Python protocols).
    The API surface is the same — Flex just supports new hardware modules.
    """

    def __init__(self, robot_ip: str, port: int = OT2_PORT):
        self.base_url = f"http://{robot_ip}:{port}"
        self.robot_ip = robot_ip
        self.session = requests.Session()
        self.session.headers["Opentrons-Version"] = "6"

    # ──────────────────────────────────────────────────────────────────────────
    # Discovery / health
    # ──────────────────────────────────────────────────────────────────────────

    def health_check(self) -> Dict[str, Any]:
        """
        Returns robot health info: name, model, robot type, firmware version.
        Raises requests.RequestException if unreachable.
        """
        r = self.session.get(f"{self.base_url}/health", timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        return r.json()

    @classmethod
    def discover(cls, ips: Optional[List[str]] = None) -> List["OpenTronsConnector"]:
        """
        Probe a list of IPs (or environment-configured IPs) for OpenTrons robots.
        Returns a list of connectors for each reachable robot.
        """
        env_ips = os.environ.get("OPENTRONS_ROBOT_IPS", "")
        if env_ips:
            candidate_ips = [ip.strip() for ip in env_ips.split(",") if ip.strip()]
        elif ips:
            candidate_ips = ips
        else:
            candidate_ips = []

        found: List[OpenTronsConnector] = []
        for ip in candidate_ips:
            try:
                c = cls(ip)
                health = c.health_check()
                log.info(f"Found OpenTrons robot at {ip}: {health.get('name', 'unknown')}")
                found.append(c)
            except Exception:
                pass  # Not a robot or unreachable
        return found

    # ──────────────────────────────────────────────────────────────────────────
    # Protocol management
    # ──────────────────────────────────────────────────────────────────────────

    def list_protocols(self) -> List[Dict[str, Any]]:
        """List all protocols stored on the robot."""
        r = self.session.get(f"{self.base_url}/protocols", timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        return r.json().get("data", [])

    def upload_protocol(self, protocol_code: str, protocol_name: str = "biomate_protocol.py") -> str:
        """
        Upload a Python protocol to the robot.
        Returns the protocol_id assigned by the robot.

        protocol_code: string content of a valid OT-2/Flex Python protocol
        protocol_name: filename to use on the robot (must end in .py)
        """
        files = {
            "files": (protocol_name, protocol_code.encode(), "text/x-python"),
        }
        r = self.session.post(
            f"{self.base_url}/protocols",
            files=files,
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        protocol_id: str = data["data"]["id"]
        log.info(f"Protocol uploaded: {protocol_id} ({protocol_name})")
        return protocol_id

    def delete_protocol(self, protocol_id: str) -> bool:
        """Delete a protocol from the robot. Returns True on success."""
        r = self.session.delete(
            f"{self.base_url}/protocols/{protocol_id}",
            timeout=HTTP_TIMEOUT,
        )
        return r.status_code in (200, 204)

    # ──────────────────────────────────────────────────────────────────────────
    # Run management
    # ──────────────────────────────────────────────────────────────────────────

    def start_run(
        self,
        protocol_id: str,
        run_params: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Create and start a run for a protocol.
        Returns the run_id.

        run_params: optional dict of CSV/JSON runtime parameters to inject
        """
        payload: Dict[str, Any] = {"data": {"protocolId": protocol_id}}
        if run_params:
            payload["data"]["runTimeParameters"] = run_params
        r = self.session.post(
            f"{self.base_url}/runs",
            json=payload,
            timeout=15,
        )
        r.raise_for_status()
        run_id: str = r.json()["data"]["id"]

        # Start the run (it begins in 'idle' state after creation)
        r2 = self.session.post(
            f"{self.base_url}/runs/{run_id}/actions",
            json={"data": {"actionType": "play"}},
            timeout=10,
        )
        r2.raise_for_status()
        log.info(f"Run started: {run_id} (protocol={protocol_id})")
        return run_id

    def get_run_status(self, run_id: str) -> Dict[str, Any]:
        """
        Get run status. Returns dict with:
          status: 'idle' | 'running' | 'paused' | 'succeeded' | 'failed' | 'stopped'
          currentCommand: description of active pipette action
          completedAt: ISO timestamp if terminal
          errors: list of error dicts
        """
        r = self.session.get(
            f"{self.base_url}/runs/{run_id}",
            timeout=HTTP_TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()["data"]
        return {
            "run_id": run_id,
            "status": data.get("status"),
            "createdAt": data.get("createdAt"),
            "startedAt": data.get("startedAt"),
            "completedAt": data.get("completedAt"),
            "errors": data.get("errors", []),
        }

    def wait_for_completion(self, run_id: str, poll_interval: int = 10, timeout: int = 7200) -> Dict[str, Any]:
        """
        Block until run reaches a terminal state (succeeded | failed | stopped).
        Returns final status dict.
        """
        terminal = {"succeeded", "failed", "stopped"}
        deadline = time.time() + timeout
        while time.time() < deadline:
            status = self.get_run_status(run_id)
            if status["status"] in terminal:
                return status
            time.sleep(poll_interval)
        raise TimeoutError(f"Run {run_id} did not complete within {timeout}s")

    def get_run_outputs(self, run_id: str) -> List[Dict[str, Any]]:
        """
        Return list of output files / data blobs from a completed run.
        OT-2 runs typically produce CSV plate maps and JSON result files.
        """
        r = self.session.get(
            f"{self.base_url}/runs/{run_id}/commands",
            timeout=HTTP_TIMEOUT,
        )
        r.raise_for_status()
        commands = r.json().get("data", [])
        # Filter commands that produced output data (e.g. custom commands writing results)
        outputs = []
        for cmd in commands:
            if cmd.get("commandType") == "custom" and cmd.get("result", {}).get("output"):
                outputs.append({
                    "command_id": cmd["id"],
                    "type": "custom_output",
                    "data": cmd["result"]["output"],
                })
        return outputs

    def stop_run(self, run_id: str) -> bool:
        """Send a stop action to a running run."""
        r = self.session.post(
            f"{self.base_url}/runs/{run_id}/actions",
            json={"data": {"actionType": "stop"}},
            timeout=10,
        )
        return r.status_code in (200, 201)

    # ──────────────────────────────────────────────────────────────────────────
    # Protocol generation helpers
    # ──────────────────────────────────────────────────────────────────────────

    @staticmethod
    def generate_plate_transfer_protocol(
        source_labware: str,
        dest_labware: str,
        pipette: str,
        transfers: List[Dict[str, Any]],
        metadata: Optional[Dict[str, str]] = None,
    ) -> str:
        """
        Generate a minimal OT-2 Python protocol for well-to-well transfers.

        Args:
            source_labware: OT-2 labware name (e.g. 'corning_96_wellplate_360ul_flat')
            dest_labware: OT-2 labware name for destination
            pipette: mount+pipette (e.g. 'p300_single_gen2')
            transfers: list of {from_well, to_well, volume_ul}
            metadata: optional dict with apiLevel, protocolName, author, description
        """
        meta = metadata or {}
        api_level = meta.get("apiLevel", "2.15")
        protocol_name = meta.get("protocolName", "BioMate Transfer Protocol")
        author = meta.get("author", "BioMate AI")
        description = meta.get("description", "Auto-generated transfer protocol")

        transfer_lines = "\n".join(
            f"    pipette.transfer({t['volume_ul']}, source['{t['from_well']}'], dest['{t['to_well']}'])"
            for t in transfers
        )

        return f'''from opentrons import protocol_api

metadata = {{
    "apiLevel": "{api_level}",
    "protocolName": "{protocol_name}",
    "author": "{author}",
    "description": "{description}",
}}

def run(protocol: protocol_api.ProtocolContext):
    # Labware
    source = protocol.load_labware("{source_labware}", location="1")
    dest   = protocol.load_labware("{dest_labware}", location="2")
    tips   = protocol.load_labware("opentrons_96_tiprack_300ul", location="3")

    # Pipette
    pipette = protocol.load_instrument("{pipette}", "right", tip_racks=[tips])

    # Transfers
{transfer_lines}
'''


# ──────────────────────────────────────────────────────────────────────────────
# BioMate integration point
# ──────────────────────────────────────────────────────────────────────────────

def send_protocol_to_robot(
    robot_ip: str,
    protocol_code: str,
    protocol_name: str = "biomate_protocol.py",
    auto_start: bool = True,
) -> Dict[str, Any]:
    """
    High-level helper: upload a protocol to a robot and optionally start it.
    Returns {protocol_id, run_id, status}.
    """
    connector = OpenTronsConnector(robot_ip)
    health = connector.health_check()
    protocol_id = connector.upload_protocol(protocol_code, protocol_name)
    result: Dict[str, Any] = {
        "robot": health.get("name", robot_ip),
        "robot_type": health.get("robotType", "OT-2"),
        "protocol_id": protocol_id,
        "run_id": None,
        "status": "uploaded",
    }
    if auto_start:
        run_id = connector.start_run(protocol_id)
        result["run_id"] = run_id
        result["status"] = "running"
    return result
