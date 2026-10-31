"""Policy for local-only (lab instrument) tools on the remote MCP endpoint.

Manifest tools marked ``local_only`` actuate hardware on a lab's private LAN
and dispatch inside this process against a caller-supplied host. On the cloud
endpoint that is a server-side request forgery surface, so they are off unless
an on-prem deployment opts in:

    BIOMATE_MCP_ALLOW_LOCAL_TOOLS=1
        Expose local-only tools (still subject to BIOMATE_MCP_TOOLS and the
        instruments:control scope).
    BIOMATE_MCP_INSTRUMENT_HOSTS=10.20.0.0/24,192.168.1.50,ot2-bay3.lab
        Hosts a tool call may target: IP addresses, CIDR ranges, or exact
        hostnames. Empty means every instrument host is rejected.
"""

from __future__ import annotations

import ipaddress
import os
import re
from typing import Any, FrozenSet

from . import bootstrap  # noqa: F401  — sanitizes sys.path before the import below

import tools_manifest  # connector: mcp/tools_manifest.py
from oauth_server.oauth import INSTRUMENT_SCOPE, SCOPES

ALLOW_ENV = "BIOMATE_MCP_ALLOW_LOCAL_TOOLS"
HOSTS_ENV = "BIOMATE_MCP_INSTRUMENT_HOSTS"

_HOSTNAME = re.compile(
    r"^(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$"
)


def local_tools_allowed() -> bool:
    return os.environ.get(ALLOW_ENV, "0") == "1"


def local_only_tool_names() -> FrozenSet[str]:
    return frozenset(t.name for t in tools_manifest.TOOL_SCHEMAS if t.local_only)


def grantable_scopes() -> FrozenSet[str]:
    """Scopes this endpoint advertises and grants."""
    scopes = frozenset(SCOPES)
    return scopes if local_tools_allowed() else scopes - {INSTRUMENT_SCOPE}


def _allowlist() -> tuple[list, set[str]]:
    networks, hostnames = [], set()
    for entry in os.environ.get(HOSTS_ENV, "").split(","):
        entry = entry.strip()
        if not entry:
            continue
        try:
            networks.append(ipaddress.ip_network(entry, strict=False))
        except ValueError:
            hostnames.add(entry.lower())
    return networks, hostnames


def check_instrument_host(host: Any) -> None:
    """Raise ValueError unless ``host`` is a bare IP/hostname on the allowlist.

    The OpenTrons connector interpolates the host into ``http://{host}:{port}``,
    so anything other than a bare address (paths, ports, userinfo, fragments)
    is rejected before the allowlist is consulted.
    """
    if not isinstance(host, str) or not host.strip():
        raise ValueError("instrument host must be a non-empty IP address or hostname")
    host = host.strip()
    networks, hostnames = _allowlist()
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        if not _HOSTNAME.match(host):
            raise ValueError(f"instrument host {host!r} is not a bare IP address or hostname")
        if host.lower() in hostnames:
            return
    else:
        if any(ip in net for net in networks):
            return
    raise ValueError(f"instrument host {host!r} is not in {HOSTS_ENV}")


def check_instrument_call(name: str, arguments: dict[str, Any]) -> None:
    """Validate every host a local-only tool call would contact."""
    if name == "list_instruments":
        ips = arguments.get("ips") or []
        if not isinstance(ips, list):
            raise ValueError("ips must be a list of hosts")
        for ip in ips:
            check_instrument_host(ip)
        return
    check_instrument_host(arguments.get("robot_ip"))
