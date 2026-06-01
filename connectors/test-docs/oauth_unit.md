# OAuth core unit tests

**File:** `backend/tests/connectors/test_oauth_server.py`
**Layer:** L1 (unit)
**Cases:** 7
**Status:** ✓ 7/7 passing

## Purpose

Validates the framework-agnostic `OAuthServer` class — the core authorization
server logic, independent of HTTP. These are pure-Python tests that exercise
the entire OAuth 2.1 + PKCE flow against an in-memory `OAuthStore` backed by
a tmp SQLite file.

If these fail, the OAuth implementation is broken at the core; no HTTP test
can compensate.

## What it covers

| # | Test | Asserts |
|---|---|---|
| 1 | `test_full_flow` | authorize → consent → token exchange produces a valid JWT with correct `sub` / `surface` / `scopes` claims |
| 2 | `test_pkce_mismatch_rejected` | wrong code_verifier → `invalid_grant` |
| 3 | `test_code_single_use` | replaying a code → `invalid_grant` (OAuth 2.1 §4.1.2) |
| 4 | `test_refresh_token_rotation` | refresh issues a new token; old token unusable (§6.1) |
| 5 | `test_revoke_surface` | `revoke_surface(user, 'cursor')` invalidates all the user's cursor tokens |
| 6 | `test_unknown_scopes_dropped` | unknown scopes filtered silently |
| 7 | `test_no_recognized_scopes_rejected` | only-unknown scopes → `invalid_scope` |

## How to run

```bash
cd /home/yzhang/biomate_worktrees/connectors_v2
BIOMATE_OAUTH_SIGNING_KEY=$(python -c "import secrets,base64;print(base64.urlsafe_b64encode(secrets.token_bytes(64)).decode())") \
  PYTHONPATH=backend/lib \
  python -m pytest backend/tests/connectors/test_oauth_server.py -v
```

## Fixtures

- `BIOMATE_OAUTH_SIGNING_KEY` env var: required, any base64-encoded 64-byte string
- `OAuthStore` instantiated per test with `db_path=tmp_path / 'oauth.db'` (pytest's tmp_path fixture)
- One `Client` registered per test: `biomate-cursor` with redirect URI `http://127.0.0.1:53684/callback`, public (PKCE-only)
- No network, no real LLM calls, no AWS

## When it fails

| Failure | Likely cause | Fix |
|---|---|---|
| `RuntimeError: BIOMATE_OAUTH_SIGNING_KEY env var is required` | env not set | export the var (see "How to run") |
| `test_full_flow` AssertionError on claims.surface | server.complete_authorize is not propagating client.surface | check `_mint_tokens` in server.py |
| `test_refresh_token_rotation` second call returns valid token | rotation not happening | `OAuthStore.rotate_refresh_token` is being skipped — check `_exchange_refresh_token` |
| PKCE tests pass but claim is None | `BIOMATE_OAUTH_SIGNING_KEY` rotated mid-test | restart pytest |
| `PermissionError: /var/lib/biomate` | default DB path used | tests should always pass `db_path=tmp_path` — check fixture |
