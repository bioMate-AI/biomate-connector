# OAuth HTTP router tests

**File:** `backend/tests/connectors/test_oauth_router.py`
**Layer:** L2 (integration — HTTP)
**Cases:** 15
**Status:** ✓ 15/15 passing

## Purpose

Validates the FastAPI HTTP layer that exposes the OAuth server. Real `Request` /
`Response` round trips through Starlette's TestClient, with form encoding,
content-type negotiation, and redirect handling. Catches bugs that the unit
suite can't — e.g. incorrect status codes, missing `Cache-Control: no-store`,
wrong query-string encoding of `error` redirects.

## What it covers

(All references are to `TEST_PLAN.md` §3.1.)

### Auth gating

| # | Test | Asserts |
|---|---|---|
| 1 | `test_authorize_unauthenticated_returns_401` | The authorize_get handler intercepts the dependency's 401 and redirects to `/login?next=...` |
| 2 | `test_authorize_authenticated_returns_consent_html` | 200 OK with rendered consent HTML; client name + scope rows visible |

### Invalid request params

| # | Test | Asserts |
|---|---|---|
| 3 | `test_authorize_unknown_client_redirects_with_error` | Unknown `client_id` → 302 with `error=invalid_client`, no code issued |
| 4 | `test_authorize_redirect_uri_not_registered` | Unregistered redirect URI → `error=invalid_request`, **no code parameter in redirect** |
| 5 | `test_authorize_pkce_plain_method_rejected` | OAuth 2.1 forbids `code_challenge_method=plain` → `error=invalid_request` |

### Consent decisions

| # | Test | Asserts |
|---|---|---|
| 6 | `test_authorize_post_allow_issues_code` | POST `decision=allow` → 302 to redirect_uri with `code` + `state` |
| 7 | `test_authorize_post_deny_returns_access_denied` | POST `decision=deny` → 302 with `error=access_denied`, no code |

### Token endpoint

| # | Test | Asserts |
|---|---|---|
| 8 | `test_token_authz_code_happy` | 200, body has access_token + refresh_token, `Cache-Control: no-store` header set (OAuth 2.1 §5.1) |
| 9 | `test_token_wrong_code_verifier` | 400 + `{"error":"invalid_grant"}` |
| 10 | `test_token_refresh_returns_new_pair` | refresh_token grant returns new pair; old refresh token rejected on reuse |
| 11 | `test_token_unsupported_grant_type` | client_credentials grant → 400 + `unsupported_grant_type` |

### Revocation

| # | Test | Asserts |
|---|---|---|
| 12 | `test_revoke_known_token` | 200; subsequent refresh with revoked token fails |
| 13 | `test_revoke_unknown_token_silently_succeeds` | 200 — RFC 7009 §2.2: clients MUST NOT learn whether token existed |

### Grant management

| # | Test | Asserts |
|---|---|---|
| 14 | `test_list_grants_returns_active` | `GET /oauth/grants` returns one row with surface/scopes/expires_at |
| 15 | `test_revoke_surface_kills_all_tokens` | `POST /oauth/grants/revoke surface=cursor` → all that surface's tokens invalidated |

## How to run

```bash
cd /home/yzhang/biomate_worktrees/connectors_v2
BIOMATE_OAUTH_SIGNING_KEY=$(python -c "import secrets,base64;print(base64.urlsafe_b64encode(secrets.token_bytes(64)).decode())") \
  BIOMATE_OAUTH_DB=/tmp/biomate_oauth_test.db \
  PYTHONPATH=backend/lib \
  python -m pytest backend/tests/connectors/test_oauth_router.py -v
```

## Fixtures

- `client_authenticated`: FastAPI `TestClient` with two dependency overrides:
  - `get_server` → fresh per-test `OAuthServer` (tmp SQLite via `tmp_path`)
  - `current_user_id` → returns `"user-42"` (bypasses session middleware)
  - Plus `monkeypatch.setattr` on the function-level `current_user_id` because `authorize_get` calls it directly (not via `Depends`) so it can redirect-on-401 instead of raise
- `client_anonymous`: same but no user override (tests 401 → `/login` redirect)
- `BIOMATE_OAUTH_DB`: must be set before import (router.py creates a module-level `OAuthStore()`); conftest.py handles this

## When it fails

| Failure | Likely cause | Fix |
|---|---|---|
| `404 == 200` on authorize_get | dependency_overrides not applied — `current_user_id` is called directly, not via Depends | use `monkeypatch.setattr(oauth_router_mod, "current_user_id", ...)` |
| `PermissionError: /var/lib/biomate/oauth.db` | `BIOMATE_OAUTH_DB` not set before import | set env var in conftest.py at module level |
| `Cache-Control` assertion fails | regression in token_endpoint | add `headers={"Cache-Control": "no-store"}` to JSONResponse |
| State param not echoed in error redirect | `_error_redirect` not passing state | check `if state: params["state"] = state` |
| `assert "code" not in qs` fails for unregistered redirect_uri | code is leaking to attacker's URI! security regression | immediate revert; redirect must include `error` only, never `code` |
