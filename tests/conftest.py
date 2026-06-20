"""Shared pytest fixtures for connector tests."""

from __future__ import annotations

import base64
import os
import secrets
import sys
import tempfile
from pathlib import Path

# Add repo root to sys.path so all packages (mcp, connectors, oauth_server,
# biomate_connector, lab_instruments) are importable without installation.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import pytest

# Set env BEFORE the OAuth router module is imported anywhere — router.py creates
# a module-level OAuthStore() with the default DB path otherwise.
os.environ.setdefault(
    "BIOMATE_OAUTH_SIGNING_KEY",
    base64.urlsafe_b64encode(secrets.token_bytes(64)).decode(),
)
os.environ.setdefault(
    "BIOMATE_OAUTH_DB",
    os.path.join(tempfile.gettempdir(), f"biomate_oauth_test_{os.getpid()}.db"),
)


@pytest.fixture()
def oauth_signing_key() -> str:
    """Force a fresh signing key per test run."""
    return os.environ["BIOMATE_OAUTH_SIGNING_KEY"]
