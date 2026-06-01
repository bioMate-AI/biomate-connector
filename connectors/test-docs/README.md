# Test suite documentation

Per-suite docs for the BioMate connectors test layers. Each doc is structured:

1. **Purpose** — what the suite proves
2. **What it covers** — case list with TEST_PLAN.md refs
3. **How to run** — exact command
4. **Fixtures** — what env vars / files / network it touches
5. **When it fails** — diagnostic steps

| Suite | Layer | File | Doc |
|---|---|---|---|
| OAuth core (Python) | L1 unit | `backend/tests/connectors/test_oauth_server.py` | [oauth_unit.md](./oauth_unit.md) |
| OAuth HTTP router | L2 integration | `backend/tests/connectors/test_oauth_router.py` | [oauth_router.md](./oauth_router.md) |
| OAuth security | L2/L7 | `backend/tests/connectors/test_oauth_security.py` | [oauth_security.md](./oauth_security.md) |
| Installer unit (TS) | L1 | `connectors/installer/src/__tests__/{pkce,configWriters}.test.ts` | [installer_unit.md](./installer_unit.md) |
| Installer flow | L2 | `connectors/installer/src/__tests__/flow.test.ts` | [installer_flow.md](./installer_flow.md) |
| Manifest drift | L2/L3 | `backend/tests/test_tools_manifest.py` + sandbox | [manifest_drift.md](./manifest_drift.md) |
| Live API routing | L4 | `backend/tests/test_connector_live.py` | [live_api.md](./live_api.md) |

## Current pass rates

| Suite | Cases | Status |
|---|---|---|
| OAuth core | 7 | ✓ 7/7 passing |
| OAuth router | 15 | ✓ 15/15 passing |
| OAuth security | 10 | ✓ 8/8 passing + 2 xfail (tracked open issues) |
| Installer unit (PKCE) | 4 | ✓ 4/4 passing |
| Installer unit (configWriters) | 4 | ✓ 4/4 passing |
| Installer flow | 12 | ✓ 12/12 passing |
| Manifest drift | (existing) | ✓ pre-existing suite still green |
| Live API | 4 + 10 new | 4 existing pass; 10 new defined, not yet committed (see live_api.md) |

**Total automated: 50/50 implemented & passing + 2 xfailed.**

## Run everything

```bash
cd /home/yzhang/biomate_worktrees/connectors_v2

# All Python suites (L1 + L2 + L7)
BIOMATE_OAUTH_SIGNING_KEY=$(python -c "import secrets,base64;print(base64.urlsafe_b64encode(secrets.token_bytes(64)).decode())") \
  BIOMATE_OAUTH_DB=/tmp/biomate_oauth_test.db \
  PYTHONPATH=backend/lib \
  python -m pytest backend/tests/connectors/ -v

# All TS suites (installer)
cd connectors/installer && ./node_modules/.bin/vitest run
```
