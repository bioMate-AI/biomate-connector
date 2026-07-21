"""Per-request caller identity, propagated from the verified OAuth bearer.

The ASGI ``BearerGuard`` sets the identity for the duration of a request; the
tool layer reads it to (a) enforce scopes and (b) act as the authenticated
BioMate user. ``anyio.to_thread.run_sync`` copies the context into the worker
thread, so ``current_identity()`` is visible where ``dispatch_tool`` runs.
"""

from __future__ import annotations

import os
from contextvars import ContextVar
from dataclasses import dataclass
from typing import FrozenSet, Optional

_current: ContextVar[Optional["Identity"]] = ContextVar("biomate_identity", default=None)


@dataclass(frozen=True)
class Identity:
    user_id: str
    scopes: FrozenSet[str]


def set_identity(*, user_id: str, scopes: FrozenSet[str]) -> None:
    _current.set(Identity(user_id=user_id, scopes=scopes))


def clear_identity() -> None:
    _current.set(None)


def current_identity() -> Optional[Identity]:
    return _current.get()


def resolve_api_key(user_id: str) -> str:
    """Map an authenticated user_id to the BioMate API key used for REST calls.

    Returns the per-user ``bm_live_`` key minted + stored at authorize time
    (BioMate's middleware resolves it to the owning user, so calls run as that
    user). Falls back to the shared ``BIOMATE_API_KEY`` only if no per-user key
    is stored (e.g. a token issued before minting was wired).
    """
    from .credentials import get_api_key

    return get_api_key(user_id) or os.environ.get("BIOMATE_API_KEY", "")
