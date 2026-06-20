"""Storage for OAuth state: clients, authorization codes, refresh tokens.

Default backend is SQLite (file path from BIOMATE_OAUTH_DB env var, defaults to
`/var/lib/biomate/oauth.db`). The store is intentionally narrow — three tables
and ~10 methods — so an alternate backend (Postgres via SQLAlchemy in the main
BioMate process) can implement the same interface.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from dataclasses import dataclass
from typing import FrozenSet, Optional

from .tokens import (
    AUTHZ_CODE_TTL_SECONDS,
    REFRESH_TOKEN_TTL_SECONDS,
    hash_refresh_token,
    now,
)

DEFAULT_DB_PATH = os.environ.get("BIOMATE_OAUTH_DB", "/var/lib/biomate/oauth.db")


@dataclass
class Client:
    client_id: str
    name: str  # human-readable: "Claude Code", "Cursor", etc.
    surface: str  # surface identifier matching scopes/tokens.surface
    redirect_uris: list[str]
    public: bool  # PKCE-only public clients vs. confidential clients
    client_secret_hash: Optional[str] = None  # only for confidential clients


@dataclass
class AuthorizationCode:
    code: str
    client_id: str
    user_id: str
    redirect_uri: str
    scopes: FrozenSet[str]
    code_challenge: str
    code_challenge_method: str
    surface: str
    expires_at: int


@dataclass
class RefreshTokenRecord:
    token_hash: str
    client_id: str
    user_id: str
    scopes: FrozenSet[str]
    surface: str
    expires_at: int
    revoked: bool = False


_SCHEMA = """
CREATE TABLE IF NOT EXISTS clients (
    client_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    surface TEXT NOT NULL,
    redirect_uris TEXT NOT NULL,
    public INTEGER NOT NULL,
    client_secret_hash TEXT
);
CREATE TABLE IF NOT EXISTS authz_codes (
    code TEXT PRIMARY KEY,
    client_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    redirect_uri TEXT NOT NULL,
    scopes TEXT NOT NULL,
    code_challenge TEXT NOT NULL,
    code_challenge_method TEXT NOT NULL,
    surface TEXT NOT NULL,
    expires_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS refresh_tokens (
    token_hash TEXT PRIMARY KEY,
    client_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    scopes TEXT NOT NULL,
    surface TEXT NOT NULL,
    expires_at INTEGER NOT NULL,
    revoked INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_refresh_user ON refresh_tokens(user_id);
CREATE INDEX IF NOT EXISTS idx_refresh_surface ON refresh_tokens(surface);
"""


class OAuthStore:
    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._db_path = db_path
        self._lock = threading.RLock()
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        return conn

    # --- clients ---

    def register_client(self, client: Client) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO clients VALUES (?,?,?,?,?,?)",
                (
                    client.client_id,
                    client.name,
                    client.surface,
                    json.dumps(client.redirect_uris),
                    1 if client.public else 0,
                    client.client_secret_hash,
                ),
            )

    def get_client(self, client_id: str) -> Optional[Client]:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM clients WHERE client_id=?", (client_id,)
            ).fetchone()
        if not row:
            return None
        return Client(
            client_id=row["client_id"],
            name=row["name"],
            surface=row["surface"],
            redirect_uris=json.loads(row["redirect_uris"]),
            public=bool(row["public"]),
            client_secret_hash=row["client_secret_hash"],
        )

    # --- authorization codes ---

    def save_authz_code(self, code: AuthorizationCode) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO authz_codes VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    code.code,
                    code.client_id,
                    code.user_id,
                    code.redirect_uri,
                    " ".join(sorted(code.scopes)),
                    code.code_challenge,
                    code.code_challenge_method,
                    code.surface,
                    code.expires_at,
                ),
            )

    def consume_authz_code(self, code: str) -> Optional[AuthorizationCode]:
        """Fetch and delete; codes are single-use per OAuth 2.1 §4.1.2."""
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM authz_codes WHERE code=?", (code,)
            ).fetchone()
            if not row:
                return None
            conn.execute("DELETE FROM authz_codes WHERE code=?", (code,))
        if row["expires_at"] < now():
            return None
        return AuthorizationCode(
            code=row["code"],
            client_id=row["client_id"],
            user_id=row["user_id"],
            redirect_uri=row["redirect_uri"],
            scopes=frozenset(row["scopes"].split()),
            code_challenge=row["code_challenge"],
            code_challenge_method=row["code_challenge_method"],
            surface=row["surface"],
            expires_at=row["expires_at"],
        )

    # --- refresh tokens ---

    def save_refresh_token(
        self,
        token: str,
        *,
        client_id: str,
        user_id: str,
        scopes: FrozenSet[str],
        surface: str,
    ) -> None:
        rec = RefreshTokenRecord(
            token_hash=hash_refresh_token(token),
            client_id=client_id,
            user_id=user_id,
            scopes=scopes,
            surface=surface,
            expires_at=now() + REFRESH_TOKEN_TTL_SECONDS,
        )
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO refresh_tokens VALUES (?,?,?,?,?,?,0)",
                (
                    rec.token_hash,
                    rec.client_id,
                    rec.user_id,
                    " ".join(sorted(rec.scopes)),
                    rec.surface,
                    rec.expires_at,
                ),
            )

    def lookup_refresh_token(self, token: str) -> Optional[RefreshTokenRecord]:
        h = hash_refresh_token(token)
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM refresh_tokens WHERE token_hash=?", (h,)
            ).fetchone()
        if not row or row["revoked"] or row["expires_at"] < now():
            return None
        return RefreshTokenRecord(
            token_hash=row["token_hash"],
            client_id=row["client_id"],
            user_id=row["user_id"],
            scopes=frozenset(row["scopes"].split()),
            surface=row["surface"],
            expires_at=row["expires_at"],
            revoked=bool(row["revoked"]),
        )

    def rotate_refresh_token(self, old_token: str, new_token: str) -> None:
        """Atomic rotate-and-revoke per OAuth 2.1 §6.1."""
        old_hash = hash_refresh_token(old_token)
        rec = self.lookup_refresh_token(old_token)
        if rec is None:
            raise ValueError("refresh token not found or expired")
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN")
            conn.execute(
                "UPDATE refresh_tokens SET revoked=1 WHERE token_hash=?", (old_hash,)
            )
            conn.execute(
                "INSERT OR REPLACE INTO refresh_tokens VALUES (?,?,?,?,?,?,0)",
                (
                    hash_refresh_token(new_token),
                    rec.client_id,
                    rec.user_id,
                    " ".join(sorted(rec.scopes)),
                    rec.surface,
                    now() + REFRESH_TOKEN_TTL_SECONDS,
                ),
            )
            conn.execute("COMMIT")

    def revoke_refresh_token(self, token: str) -> None:
        h = hash_refresh_token(token)
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE refresh_tokens SET revoked=1 WHERE token_hash=?", (h,)
            )

    def revoke_all_for_surface(self, user_id: str, surface: str) -> int:
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                "UPDATE refresh_tokens SET revoked=1 WHERE user_id=? AND surface=? AND revoked=0",
                (user_id, surface),
            )
            return cur.rowcount

    def list_user_grants(self, user_id: str) -> list[dict]:
        """Return active grants for the account-settings page."""
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """SELECT surface, client_id, scopes, expires_at FROM refresh_tokens
                   WHERE user_id=? AND revoked=0 AND expires_at>? ORDER BY expires_at DESC""",
                (user_id, now()),
            ).fetchall()
        return [
            {
                "surface": r["surface"],
                "client_id": r["client_id"],
                "scopes": r["scopes"].split(),
                "expires_at": r["expires_at"],
            }
            for r in rows
        ]


_AUTHZ_TTL = AUTHZ_CODE_TTL_SECONDS  # re-export for callers
