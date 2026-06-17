"""
SiLA2 Generic Adapter
======================
Connects BioMate to any SiLA2-compliant lab device (Hamilton, Tecan, centrifuges,
plate readers, incubators). SiLA2 is an IEC 62832-based standard using gRPC.

This adapter provides:
  1. Device discovery (mDNS / explicit IP)
  2. Feature (capability) introspection
  3. Command execution (unary + observable)
  4. Property reads
  5. BioMate workflow integration via instrument_watcher

SiLA2 devices expose typed gRPC endpoints defined by "Feature Definitions" (FD)
in SiLA2 protobuf format. The `sila2` Python library handles protobuf generation.

Installation:
    pip install sila2

Priority devices with known SiLA2 support:
  - Hamilton STAR line (via VENUS SiLA2 extension)
  - Tecan Fluent (via FluentControl SiLA2 driver)
  - Sartorius incubators (BioCockpit SiLA2 module)
  - Cytena C.BIRD (single-cell dispensing)
  - Various plate readers (Synergy, Tecan SPARK)

Environment variables:
    SILA2_DEVICE_IPS   Comma-separated list of device IPs to connect to
    SILA2_PORT         Default gRPC port (default: 50051)
"""

import logging
import os
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

SILA2_DEVICE_IPS = os.environ.get("SILA2_DEVICE_IPS", "")
SILA2_DEFAULT_PORT = int(os.environ.get("SILA2_PORT", "50051"))

# ──────────────────────────────────────────────────────────────────────────────
# SiLA2 device representation
# ──────────────────────────────────────────────────────────────────────────────

class SiLA2Device:
    """
    Represents a connected SiLA2 device.
    Uses the `sila2` Python library if available; falls back to a stub.

    For full functionality: pip install sila2
    GitHub: https://github.com/SiLA2/SiLA_Python
    """

    def __init__(self, host: str, port: int = SILA2_DEFAULT_PORT):
        self.host = host
        self.port = port
        self._client = None
        self._features: Optional[List[Dict[str, Any]]] = None

    def connect(self) -> bool:
        """
        Establish gRPC connection to the device.
        Returns True if connected, False if sila2 library not available or device unreachable.
        """
        try:
            from sila2.client import SilaClient
            self._client = SilaClient(self.host, self.port, insecure=True)
            log.info(f"Connected to SiLA2 device at {self.host}:{self.port}")
            return True
        except ImportError:
            log.warning(
                "sila2 library not installed. Install with: pip install sila2\n"
                "SiLA2 adapter operating in discovery-only mode."
            )
            return False
        except Exception as exc:
            log.error(f"SiLA2 connection failed ({self.host}:{self.port}): {exc}")
            return False

    def get_server_info(self) -> Dict[str, Any]:
        """Return SiLA2 server metadata: name, UUID, type, description, version."""
        if self._client is None:
            return {"error": "Not connected", "host": self.host, "port": self.port}
        try:
            info = self._client.SiLAService.Get_ImplementedFeatures()
            return {
                "host": self.host,
                "port": self.port,
                "features": [f.feature_identifier for f in info],
            }
        except Exception as exc:
            return {"error": str(exc)}

    def list_features(self) -> List[Dict[str, Any]]:
        """
        List all SiLA2 features (capabilities) the device exposes.
        Each feature has: identifier, display_name, description, commands, properties.
        """
        if self._client is None:
            if not self.connect():
                return []
        try:
            result = []
            for feature_name in dir(self._client):
                if feature_name.startswith("_"):
                    continue
                feature = getattr(self._client, feature_name, None)
                if feature and hasattr(feature, "__class__") and "Feature" in type(feature).__name__:
                    result.append({
                        "identifier": feature_name,
                        "type": type(feature).__name__,
                    })
            return result
        except Exception as exc:
            log.error(f"list_features failed: {exc}")
            return []

    def run_command(
        self,
        feature_identifier: str,
        command_name: str,
        parameters: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Execute a SiLA2 command on the device.

        feature_identifier: e.g. "PumpFluidDosingService", "LiquidHandlingService"
        command_name:        e.g. "SetFillLevel", "Aspirate", "Dispense"
        parameters:          dict of parameter names to values

        Example (Hamilton STAR aspirate):
            device.run_command(
                "LiquidHandlingService",
                "Aspirate",
                {"Volume": 50.0, "LiquidClass": "Water", "Well": "A1"}
            )
        """
        if self._client is None:
            if not self.connect():
                return {"error": "Could not connect to SiLA2 device"}

        try:
            feature = getattr(self._client, feature_identifier, None)
            if feature is None:
                return {"error": f"Feature not found: {feature_identifier}"}
            command = getattr(feature, command_name, None)
            if command is None:
                return {"error": f"Command not found: {command_name}"}
            result = command(**(parameters or {}))
            return {"success": True, "result": str(result)}
        except Exception as exc:
            log.error(f"SiLA2 command error ({feature_identifier}.{command_name}): {exc}")
            return {"error": str(exc)}

    def read_property(self, feature_identifier: str, property_name: str) -> Any:
        """
        Read a SiLA2 property from the device.
        Properties are observable values (temperature, status, positions, etc.)
        """
        if self._client is None:
            if not self.connect():
                return None
        try:
            feature = getattr(self._client, feature_identifier, None)
            if feature is None:
                return None
            prop = getattr(feature, f"Get_{property_name}", None)
            if prop is None:
                return None
            return prop()
        except Exception as exc:
            log.error(f"SiLA2 property read error: {exc}")
            return None

    def disconnect(self) -> None:
        """Close the gRPC connection."""
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None


# ──────────────────────────────────────────────────────────────────────────────
# Device registry
# ──────────────────────────────────────────────────────────────────────────────

class SiLA2Registry:
    """
    Manages a set of SiLA2 devices. Supports static IP configuration
    and mDNS discovery (if `zeroconf` is available).
    """

    def __init__(self):
        self._devices: Dict[str, SiLA2Device] = {}

    def add_device(self, host: str, port: int = SILA2_DEFAULT_PORT) -> SiLA2Device:
        """Register a device by IP/hostname."""
        key = f"{host}:{port}"
        if key not in self._devices:
            device = SiLA2Device(host, port)
            self._devices[key] = device
        return self._devices[key]

    def load_from_env(self) -> List[SiLA2Device]:
        """Load devices from SILA2_DEVICE_IPS environment variable."""
        devices = []
        for entry in SILA2_DEVICE_IPS.split(","):
            entry = entry.strip()
            if not entry:
                continue
            if ":" in entry:
                host, port_str = entry.rsplit(":", 1)
                port = int(port_str)
            else:
                host = entry
                port = SILA2_DEFAULT_PORT
            devices.append(self.add_device(host, port))
        return devices

    def discover_mdns(self, timeout: float = 5.0) -> List[SiLA2Device]:
        """
        Discover SiLA2 devices on the local network via mDNS (_sila._tcp).
        Requires: pip install zeroconf
        """
        try:
            from zeroconf import Zeroconf, ServiceBrowser
            import time

            found: List[SiLA2Device] = []
            zc = Zeroconf()

            class Listener:
                def add_service(self, zc_inner, type_, name):
                    info = zc_inner.get_service_info(type_, name)
                    if info:
                        host = ".".join(str(b) for b in info.addresses[0].split())
                        device = SiLA2Registry().add_device(host, info.port)
                        found.append(device)
                        log.info(f"Discovered SiLA2 device: {name} at {host}:{info.port}")
                def remove_service(self, *_): pass
                def update_service(self, *_): pass

            browser = ServiceBrowser(zc, "_sila._tcp.local.", Listener())
            time.sleep(timeout)
            zc.close()
            return found
        except ImportError:
            log.warning("zeroconf not installed — mDNS discovery disabled. pip install zeroconf")
            return []

    def list_all(self) -> List[SiLA2Device]:
        return list(self._devices.values())

    def get_device(self, host: str, port: int = SILA2_DEFAULT_PORT) -> Optional[SiLA2Device]:
        return self._devices.get(f"{host}:{port}")


# ──────────────────────────────────────────────────────────────────────────────
# Known device profiles
# ──────────────────────────────────────────────────────────────────────────────

# Pre-defined profiles for devices with known SiLA2 feature sets.
# Maps device type → feature_id → typical commands.
DEVICE_PROFILES: Dict[str, Dict[str, List[str]]] = {
    "Hamilton STAR": {
        "LiquidHandlingService": ["Aspirate", "Dispense", "Mix"],
        "GripperService": ["GetPlate", "SetPlate"],
        "PlatePadService": ["GetStatus", "SetTemperature"],
    },
    "Tecan Fluent": {
        "LiquidHandlingService": ["Aspirate", "Dispense", "Mix"],
        "WorktableService": ["MoveArm", "GetLabware"],
        "GravimetricVerification": ["VerifyVolume"],
    },
    "Sartorius Incubator": {
        "IncubatorService": ["SetCO2", "SetTemperature", "SetHumidity", "GetStatus"],
    },
    "Generic Plate Reader": {
        "AbsorbanceReaderService": ["ReadAbsorbance", "ReadFluorescence"],
        "PlateHandlerService": ["LoadPlate", "EjectPlate"],
    },
}


# ──────────────────────────────────────────────────────────────────────────────
# Singleton registry
# ──────────────────────────────────────────────────────────────────────────────

# Global registry — populated at application startup
sila2_registry = SiLA2Registry()

# Load devices from environment on module import
if SILA2_DEVICE_IPS:
    sila2_registry.load_from_env()
