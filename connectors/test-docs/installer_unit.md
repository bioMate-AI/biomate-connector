# Installer unit tests

**Files:**
- `connectors/installer/src/__tests__/pkce.test.ts` (4 cases)
- `connectors/installer/src/__tests__/configWriters.test.ts` (4 cases)

**Layer:** L1 (unit)
**Cases:** 8 total
**Status:** ✓ 8/8 passing

## Purpose

Validates two narrow, security-critical helpers used by `@biomate/connect`:

1. **PKCE pair generator** (`pkce.ts`) — the cryptographic primitive that
   makes the installer's OAuth flow secure against a malicious redirect.
2. **Config writers** (`configWriters.ts`) — atomic JSON / TOML writers that
   merge BioMate's MCP server entry into the user's existing host config
   without clobbering unrelated keys.

If these fail, the installer either generates insecure tokens or corrupts
users' editor configs.

## What it covers

### PKCE (`pkce.test.ts`)

| # | Test | Asserts |
|---|---|---|
| 1 | `verifier length is in [43, 128]` | RFC 7636 §4.1 length conformance over 20 iterations |
| 2 | `challenge equals base64url(sha256(verifier))` | S256 method correctness (compared against Node's `crypto.createHash`) |
| 3 | `uses S256 method` | Method tag is always `'S256'`, never `'plain'` (OAuth 2.1 requirement) |
| 4 | `verifier and challenge are URL-safe` | No `+`, `/`, or `=` characters — safe for query-string embedding |

### Config writers (`configWriters.test.ts`)

| # | Test | Asserts |
|---|---|---|
| 5 | `upsertJSON creates new file with content` | Writes new `~/.claude.json` on a fresh machine |
| 6 | `upsertJSON preserves existing keys` | A user with `theme=dark` and other mcpServers doesn't lose them |
| 7 | `upsertTOML appends a new biomate block` | Codex `[mcp_servers.biomate]` block added without touching other blocks |
| 8 | `upsertTOML replaces existing biomate block on re-install` | Idempotency: re-running install replaces (doesn't duplicate) the BioMate block |

## How to run

```bash
cd /home/yzhang/biomate_worktrees/connectors_v2/connectors/installer
./node_modules/.bin/vitest run src/__tests__/pkce.test.ts src/__tests__/configWriters.test.ts
```

(Or just `npm test` to run all 3 installer test files.)

## Fixtures

- No external services
- `fs.mkdtemp(os.tmpdir() + '/biomate-test-')` per test — writes go to `/tmp/biomate-test-XXXXXX/`, cleaned by OS
- No env vars required

## When it fails

| Failure | Likely cause | Fix |
|---|---|---|
| PKCE `verifier length` test fails | `crypto.randomBytes(48)` changed; base64url encoding stripped wrong chars | revert `pkce.ts:generatePKCE` |
| `challenge ≠ sha256(verifier)` | base64url replace order wrong | `.replace(/=/g, '').replace(/\+/g, '-').replace(/\//g, '_')` — exact order |
| `upsertJSON preserves existing keys` fails | mutate function clobbering top-level object | use `cfg.mcpServers ??= {}; cfg.mcpServers.biomate = ...` not `cfg = { mcpServers: ... }` |
| `upsertTOML replaces ... on re-install` fails | regex not matching block boundary | check `/\n\[mcp_servers\.biomate\][\s\S]*?(?=\n\[|$)/g` |
| All tests fail with `Cannot find module '@rollup/rollup-linux-arm64-gnu'` | known npm bug on ARM64 Linux | `npm install --no-save @rollup/rollup-linux-arm64-gnu` |
