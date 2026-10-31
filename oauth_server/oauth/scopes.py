"""OAuth scope definitions for BioMate connector surfaces.

Scopes follow the form `<resource>:<action>`. The default scope set granted to
new clients is `DEFAULT_SCOPES`. Surfaces request a subset; admins can later
revoke individual scopes per token.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import FrozenSet


@dataclass(frozen=True)
class Scope:
    name: str
    description: str


SCOPES: dict[str, Scope] = {
    "runs:read": Scope("runs:read", "List, inspect, and stream workflow runs"),
    "runs:write": Scope("runs:write", "Start and cancel workflow runs"),
    "workflows:search": Scope(
        "workflows:search", "Search the workflow catalog and fetch specs"
    ),
    "billing:read": Scope("billing:read", "Read account usage and quota"),
    "memory:read": Scope("memory:read", "Recall prior runs, findings, procedures"),
    "memory:write": Scope("memory:write", "Save new memories and feedback"),
    "files:upload": Scope("files:upload", "Obtain signed upload URLs for input files"),
    "reports:export": Scope(
        "reports:export", "Render methods/QC/findings reports as PDF or markdown"
    ),
    "instruments:control": Scope(
        "instruments:control",
        "Discover lab instruments and run protocols on them (physical actuation)",
    ),
}

# Never granted implicitly: a client must request it explicitly and the
# deployment must enable local instrument tools (remote_mcp.local_tools).
INSTRUMENT_SCOPE = "instruments:control"

DEFAULT_SCOPES: FrozenSet[str] = frozenset(
    [
        "runs:read",
        "runs:write",
        "workflows:search",
        "billing:read",
        "memory:read",
        "memory:write",
        "files:upload",
        "reports:export",
    ]
)


# Standard scopes that carry no BioMate privilege. `offline_access` asks for a
# refresh token, which the authorization-code flow always issues; it is removed
# before scope resolution so it can neither fail the request nor widen the grant.
NON_PRIVILEGE_SCOPES: FrozenSet[str] = frozenset({"offline_access"})


def parse_scope_string(scope_str: str | None) -> FrozenSet[str]:
    """Parse a space-delimited scope string per RFC 6749 §3.3."""
    if not scope_str:
        return frozenset()
    parts = [p.strip() for p in scope_str.split() if p.strip()]
    return frozenset(parts)


def validate_scopes(requested: FrozenSet[str]) -> FrozenSet[str]:
    """Drop any unknown scopes; return only the recognized subset."""
    return frozenset(s for s in requested if s in SCOPES)
