"""Encrypted user_id -> BioMate API key store.

When a user authorizes, the service mints a per-user `bm_live_` API key (which
BioMate's auth middleware treats as first-class connector identity — "connector
traffic runs as that user") and stores it here, encrypted at rest with a key
derived from ``BIOMATE_OAUTH_SIGNING_KEY``. Tool calls then run as that user
without the connector holding the user's password or a short-lived session JWT.

Stored in the same SQLite file as the OAuth state (``BIOMATE_OAUTH_DB``), in a
separate table.
"""

from __future__ import annotations

import base64
import hashlib
import os
import sqlite3
import threading
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

_lock = threading.Lock()


def _db_path() -> str:
    return os.environ.get("BIOMATE_OAUTH_DB") or "/var/lib/biomate/oauth.db"


def _fernet() -> Fernet:
    sk = os.environ.get("BIOMATE_OAUTH_SIGNING_KEY", "")
    if not sk:
        raise RuntimeError("BIOMATE_OAUTH_SIGNING_KEY is required to (de)crypt stored API keys")
    key = base64.urlsafe_b64encode(hashlib.sha256(sk.encode()).digest())
    return Fernet(key)


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path())
    conn.execute(
        "CREATE TABLE IF NOT EXISTS user_api_keys ("
        " user_id TEXT PRIMARY KEY, enc_key TEXT NOT NULL, created_at INTEGER NOT NULL)"
    )
    return conn


def get_api_key(user_id: str) -> Optional[str]:
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT enc_key FROM user_api_keys WHERE user_id = ?", (user_id,)
        ).fetchone()
    if not row:
        return None
    try:
        return _fernet().decrypt(row[0].encode()).decode()
    except (InvalidToken, Exception):
        return None


def put_api_key(user_id: str, api_key: str, created_at: int) -> None:
    enc = _fernet().encrypt(api_key.encode()).decode()
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO user_api_keys (user_id, enc_key, created_at) VALUES (?,?,?)",
            (user_id, enc, created_at),
        )


def has_api_key(user_id: str) -> bool:
    return get_api_key(user_id) is not None
