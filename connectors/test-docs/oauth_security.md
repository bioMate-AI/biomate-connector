# OAuth security tests

**File:** `backend/tests/connectors/test_oauth_security.py`
**Layer:** L2 + L7 (security)
**Cases:** 10 (8 passing + 2 xfail)
**Status:** ✓ 8/8 passing + 2 xfail tracked as known issues

## Purpose

Hardens the OAuth server against the attack scenarios in `TEST_PLAN.md §7`.
These tests encode security invariants — when one fails, treat it as a
**SEV-2 security incident**: do not merge, do not deploy, file a CVE-class
issue, and remediate before any further connector work proceeds.

## What it covers

| # | TEST_PLAN §  | Test | Asserts |
|---|---|---|---|
| 1 | §7.2 | `test_code_issued_for_cursor_rejected_when_presented_by_codex` | Authorization code is bound to issuing client_id; another client can't redeem it |
| 2 | §7.3 | `test_refresh_tokens_stored_hashed_not_plaintext` | DB dump reveals only 64-char hex HMAC-SHA256 hashes, never raw tokens |
| 3 | §7.4 | `test_old_jwt_invalidated_after_signing_key_rotation` | Rotating `BIOMATE_OAUTH_SIGNING_KEY` invalidates all previously-issued access tokens |
| 4 | §7.5 | `test_unregistered_redirect_uri_does_not_leak_code` | Even though the error param is returned via the supplied URI (per OAuth 2.0 §4.1.2.1 — clients validate), no `code` query param leaks |
| 5 | §7.6 | `test_pkce_plain_method_rejected` | OAuth 2.1 strict — `plain` method rejected |
| 6 | §7.7 | `test_refresh_cannot_widen_scope` | Refresh token grants the originally-bound scope set; client-requested wider scope is ignored |
| 7 | §7.8 | `test_expired_authz_code_rejected` | Code older than `AUTHZ_CODE_TTL_SECONDS` (60s) → `invalid_grant`, even before consumption |
| 8 | §7.9 | `test_access_token_surface_claim_matches_issued_client` | JWT `surface` claim is set by the server from the client record, not user-supplied — defeats surface impersonation |

### XFailed (tracked open issues for GA)

| # | TEST_PLAN §  | Test | Reason |
|---|---|---|---|
| 9 | §7.10 | `test_rate_limit_on_token_endpoint` | Rate limiting not yet implemented. See TEST_PLAN §13 open issue #1. Risk: PKCE verifier brute-force. Mitigation plan: `slowapi` middleware in front of `/oauth/token`. |
| 10 | §7.11 | `test_refresh_token_reuse_detected_revokes_family` | Token-family revocation not yet implemented. See TEST_PLAN §13 open issue #2. Risk: rotated refresh token reuse only revokes one token, not the family. Mitigation plan: add `token_family_id` column to refresh_tokens, revoke all on replay. |

## How to run

```bash
cd /home/yzhang/biomate_worktrees/connectors_v2
BIOMATE_OAUTH_SIGNING_KEY=$(python -c "import secrets,base64;print(base64.urlsafe_b64encode(secrets.token_bytes(64)).decode())") \
  BIOMATE_OAUTH_DB=/tmp/biomate_oauth_test.db \
  PYTHONPATH=backend/lib \
  python -m pytest backend/tests/connectors/test_oauth_security.py -v
```

Expect: `8 passed, 2 xfailed`. The xfails are **expected** and **tracked** —
they're failing because the underlying feature isn't built yet, not because
the test is wrong.

## Fixtures

- `two_clients_server`: fresh OAuthServer with both `biomate-cursor` and
  `biomate-codex` clients registered (needed for cross-client code-replay test)
- `app_client`: TestClient + the two-client server, with same monkeypatch
  pattern as `oauth_router.md`
- `monkeypatch.setattr("galaxy.connectors.oauth.store.now", ...)`: clock
  control for testing expired authorization codes

## When it fails

**A failing test in this file is a security regression — do not paper over it.**

| Failure | Severity | Remediation |
|---|---|---|
| `test_refresh_tokens_stored_hashed_not_plaintext` | SEV-1 | Raw refresh tokens in DB. Roll signing key, revoke all tokens, audit DB exports. Fix `OAuthStore.save_refresh_token` to use `hash_refresh_token`. |
| `test_code_issued_for_cursor_rejected_when_presented_by_codex` | SEV-1 | Client_id binding broken — any client can redeem any code. Check `_exchange_authz_code` for `if rec.client_id != client_id`. |
| `test_unregistered_redirect_uri_does_not_leak_code` | SEV-1 | Open redirect with auth code leak. Check `begin_authorize` rejects before any code generation. |
| `test_access_token_surface_claim_matches_issued_client` | SEV-2 | Surface impersonation possible. Ensure `_mint_tokens` uses `client.surface`, never reading it from request body. |
| `test_pkce_plain_method_rejected` | SEV-2 | PKCE downgrade allowed. Audit `verify_pkce` and `begin_authorize`. |
| `test_refresh_cannot_widen_scope` | SEV-2 | Scope escalation. Check `_exchange_refresh_token` ignores any `scope` form param. |
| `test_old_jwt_invalidated_after_signing_key_rotation` | SEV-3 | Key rotation doesn't work; can't revoke compromised keys. Check `verify_access_token` reads env at call time, not import time. |
| `test_expired_authz_code_rejected` | SEV-3 | Stale codes accepted. Check `consume_authz_code` compares to `now()`. |

## Pre-GA blockers

The 2 xfail tests should become passing tests before public launch:

1. **Implement rate limiting** — add `slowapi` to `/oauth/token` with 30 req/min/IP. Then the xfail can be flipped.
2. **Implement refresh-token-family revocation** — add `family_id` column, set it on first authz_code exchange, revoke whole family on replay detection. Then flip the xfail.

Both are 1–2 day projects each.
