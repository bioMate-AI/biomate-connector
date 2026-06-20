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
}

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


def parse_scope_string(scope_str: str | None) -> FrozenSet[str]:
    """Parse a space-delimited scope string per RFC 6749 §3.3."""
    if not scope_str:
        return frozenset()
    parts = [p.strip() for p in scope_str.split() if p.strip()]
    return frozenset(parts)


def validate_scopes(requested: FrozenSet[str]) -> FrozenSet[str]:
    """Drop any unknown scopes; return only the recognized subset."""
    return frozenset(s for s in requested if s in SCOPES)
