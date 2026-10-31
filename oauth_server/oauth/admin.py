"""Admin operations for pre-registered (manual) OAuth clients.

Used when a host cannot use Dynamic Client Registration and instead asks an
admin for a Client ID + Client Secret (e.g. Benchling "Manual OAuth
registration"). The secret is generated here, stored only as a hash, and
printed once.

    BIOMATE_OAUTH_DB=/var/lib/biomate/oauth.db python -m oauth_server.oauth.admin \\
        register-confidential --client-id benchling-acme --name "Benchling (acme)" \\
        --redirect-uri https://acme.benchling.com/1/api/llm/mcp/oauth/callback
"""

from __future__ import annotations

import argparse
from typing import Optional
from .redirects import validate_redirect_uri
from .store import Client, OAuthStore
from .tokens import hash_client_secret, new_client_secret


def register_confidential_client(
    store: OAuthStore,
    *,
    client_id: str,
    name: str,
    redirect_uris: list[str],
    surface: Optional[str] = None,
) -> str:
    """Create or rotate a confidential client; returns the plaintext secret.

    Re-running with the same client_id rotates the secret (the old one stops
    working immediately) and replaces the redirect URIs.
    """
    if not client_id or not redirect_uris:
        raise ValueError("client_id and at least one redirect URI are required")
    for uri in redirect_uris:
        validate_redirect_uri(uri)
    secret = new_client_secret()
    store.register_client(
        Client(
            client_id=client_id,
            name=name,
            surface=surface or client_id,
            redirect_uris=list(redirect_uris),
            public=False,
            client_secret_hash=hash_client_secret(secret),
        )
    )
    return secret


def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m oauth_server.oauth.admin")
    sub = parser.add_subparsers(dest="cmd", required=True)
    reg = sub.add_parser("register-confidential", help="create or rotate a confidential client")
    reg.add_argument("--client-id", required=True)
    reg.add_argument("--name", required=True)
    reg.add_argument("--redirect-uri", action="append", required=True, dest="redirect_uris")
    reg.add_argument("--surface", default=None, help="grant-revocation surface (default: client id)")
    args = parser.parse_args(argv)

    store = OAuthStore()
    secret = register_confidential_client(
        store,
        client_id=args.client_id,
        name=args.name,
        redirect_uris=args.redirect_uris,
        surface=args.surface,
    )
    print(f"client_id:     {args.client_id}")
    print(f"client_secret: {secret}")
    print("Store the secret now; only its hash is kept. Re-run to rotate.")


if __name__ == "__main__":
    main()
