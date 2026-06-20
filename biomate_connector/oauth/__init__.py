"""Public OAuth API for biomate_connector.

All sub-modules (router, store, tokens) are registered as aliases for the
oauth_server.oauth counterparts so that monkeypatching works correctly in
tests: patching biomate_connector.oauth.router.X is the same as patching
oauth_server.oauth.router.X because they are the same module object.
"""
from __future__ import annotations

import sys
import importlib

# ── Register sub-modules as same-object aliases in sys.modules ───────────────
# Also set as attributes on this package so getattr(this_module, 'store') works.
# This is required for pytest monkeypatch.setattr("biomate_connector.oauth.X.Y")
# to affect the same attribute as monkeypatch.setattr("oauth_server.oauth.X.Y").

_this = sys.modules[__name__]

for _bc_sub, _os_sub in (
    ("biomate_connector.oauth.router", "oauth_server.oauth.router"),
    ("biomate_connector.oauth.store", "oauth_server.oauth.store"),
    ("biomate_connector.oauth.tokens", "oauth_server.oauth.tokens"),
    ("biomate_connector.oauth.server", "oauth_server.oauth.server"),
    ("biomate_connector.oauth.pkce", "oauth_server.oauth.pkce"),
    ("biomate_connector.oauth.scopes", "oauth_server.oauth.scopes"),
):
    _mod = importlib.import_module(_os_sub)
    sys.modules[_bc_sub] = _mod
    # Bind as attribute so `getattr(biomate_connector.oauth, 'store')` works.
    setattr(_this, _bc_sub.split(".")[-1], _mod)

# ── Re-export top-level symbols for `from biomate_connector.oauth import X` ──
from oauth_server.oauth import *  # noqa: F401, F403
from oauth_server.oauth import (  # explicit for type checkers
    ACCESS_TOKEN_TTL_SECONDS,
    AccessTokenClaims,
    AuthorizationCode,
    AuthorizeError,
    AuthorizeRequest,
    Client,
    DEFAULT_SCOPES,
    GrantSummary,
    OAuthServer,
    OAuthStore,
    RefreshTokenRecord,
    SCOPES,
    TokenResponse,
    issue_access_token,
    new_refresh_token,
    parse_scope_string,
    validate_scopes,
    verify_access_token,
    verify_pkce,
)
