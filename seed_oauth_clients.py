#!/usr/bin/env python3
"""Seed the OAuth client table with one entry per BioMate connector surface.

All connector surfaces are PUBLIC clients (PKCE, no client_secret).

Usage:
    BIOMATE_OAUTH_DB=/var/lib/biomate/oauth.db python backend/scripts/seed_oauth_clients.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lib"))

from biomate_connector.oauth import Client, OAuthStore  # noqa: E402

CLIENTS = [
    Client(
        client_id="biomate-claude-code",
        name="Claude Code",
        surface="claude-code",
        redirect_uris=["http://127.0.0.1:53682/callback", "http://localhost:53682/callback"],
        public=True,
    ),
    Client(
        client_id="biomate-claude-desktop",
        name="Claude Desktop",
        surface="claude-desktop",
        redirect_uris=["http://127.0.0.1:53683/callback", "http://localhost:53683/callback"],
        public=True,
    ),
    Client(
        client_id="biomate-cursor",
        name="Cursor",
        surface="cursor",
        redirect_uris=["http://127.0.0.1:53684/callback", "http://localhost:53684/callback"],
        public=True,
    ),
    Client(
        client_id="biomate-codex",
        name="Codex CLI",
        surface="codex",
        redirect_uris=["http://127.0.0.1:53685/callback", "http://localhost:53685/callback"],
        public=True,
    ),
    Client(
        client_id="biomate-chatgpt",
        name="ChatGPT",
        surface="chatgpt",
        # ChatGPT Actions only allows HTTPS redirect URIs on chat.openai.com.
        redirect_uris=["https://chat.openai.com/aip/g-biomate/oauth/callback"],
        public=True,
    ),
    Client(
        client_id="biomate-open-claw",
        name="Open Claw",
        surface="open-claw",
        redirect_uris=["https://biomate.ai/connectors/open-claw/callback"],
        public=True,
    ),
]


def main() -> None:
    store = OAuthStore()
    for c in CLIENTS:
        store.register_client(c)
        print(f"registered {c.client_id} ({c.surface})")


if __name__ == "__main__":
    main()
