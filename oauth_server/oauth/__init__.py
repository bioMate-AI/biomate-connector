"""BioMate OAuth 2.1 + PKCE authorization server.

Public surface:
    from biomate_connector.oauth import OAuthServer, OAuthStore, Client
    from biomate_connector.oauth.router import router  # FastAPI

Setup:
    1. Set BIOMATE_OAUTH_SIGNING_KEY (64+ random bytes, base64).
    2. Register clients via OAuthStore.register_client() or scripts/seed_oauth_clients.py.
    3. Mount router under the BioMate backend.
"""

from .pkce import verify_pkce
from .redirects import redirect_uri_problem, validate_redirect_uri
from .scopes import DEFAULT_SCOPES, INSTRUMENT_SCOPE, SCOPES, parse_scope_string, validate_scopes
from .server import (
    AuthorizeError,
    AuthorizeRequest,
    GrantSummary,
    OAuthServer,
    TokenResponse,
    canonical_resource,
    client_credentials,
)
from .store import AuthorizationCode, Client, OAuthStore, RefreshTokenRecord
from .tokens import (
    ACCESS_TOKEN_TTL_SECONDS,
    AccessTokenClaims,
    issue_access_token,
    new_refresh_token,
    verify_access_token,
)

__all__ = [
    "ACCESS_TOKEN_TTL_SECONDS",
    "AccessTokenClaims",
    "AuthorizationCode",
    "AuthorizeError",
    "AuthorizeRequest",
    "Client",
    "DEFAULT_SCOPES",
    "INSTRUMENT_SCOPE",
    "GrantSummary",
    "OAuthServer",
    "OAuthStore",
    "RefreshTokenRecord",
    "SCOPES",
    "TokenResponse",
    "canonical_resource",
    "client_credentials",
    "issue_access_token",
    "new_refresh_token",
    "parse_scope_string",
    "redirect_uri_problem",
    "validate_redirect_uri",
    "validate_scopes",
    "verify_access_token",
    "verify_pkce",
]
