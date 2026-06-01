# Installer end-to-end flow tests

**File:** `connectors/installer/src/__tests__/flow.test.ts`
**Layer:** L2 (integration — full OAuth flow against mock server)
**Cases:** 12
**Status:** ✓ 12/12 passing

## Purpose

Drives the `@biomate/connect` installer through the full PKCE flow against a
mock OAuth server (Node `http`), with all surface-specific config writes
landing in a per-test `tmpHome`. This is the highest-confidence test we run
before paying for an E2E install on a fresh VM.

If these pass, the installer's network protocol, config-file mutation, and
keychain stub are all working end-to-end — leaving only host-specific
quirks (Claude Desktop on a real macOS app, Cursor's mcp.json picker UI) for
manual L5 smoke testing.

## What it covers

(All references are to `TEST_PLAN.md §3.2`.)

| # | TEST_PLAN §  | Test | Asserts |
|---|---|---|---|
| 1 | §3.2.1 | `claude-code: writes ~/.claude.json with mcpServers.biomate on fresh machine` | First-install file creation + correct refresh token written |
| 2 | §3.2.2 | `claude-code: preserves existing keys in ~/.claude.json` | Pre-existing `theme=dark` and other mcpServers survive |
| 3 | §3.2.3 | `claude-code: re-install is idempotent (no duplicate biomate entry)` | Running installer twice doesn't duplicate the `biomate` key |
| 4 | §3.2.4 | `cursor: writes ~/.cursor/mcp.json` | Correct path on macOS/Linux |
| 5 | §3.2.5 | `codex: writes [mcp_servers.biomate] to ~/.codex/config.toml` | TOML format, refresh token embedded |
| 6 | §3.2.6 | `chatgpt: prints instructions, writes no file` | Web-only install path doesn't touch local FS |
| 7 | §3.2.7 | `open-claw: prints WeChat instructions, writes no file` | Hosted install path doesn't touch local FS |
| 8 | §3.2.8 | `unknown surface id returns undefined from SURFACES map` | CLI exits cleanly on bad input |
| 9 | §3.2.10 | `OAuth callback error=access_denied surfaces cleanly` | User denied consent → installer throws `Error(/access_denied/)` |
| 10 | §3.2.11 | `state mismatch on callback rejected` | CSRF protection — `?state=` mismatch → throws `Error(/invalid callback/)` |
| 11 | §3.2.12 | `token exchange 400 surfaces the server reply` | Mock returns 400 → installer throws with status code in message |
| 12 | bonus | `code_verifier is presented to /oauth/token` | PKCE verifier is forwarded; length ≥ 43 |

## How to run

```bash
cd /home/yzhang/biomate_worktrees/connectors_v2/connectors/installer
./node_modules/.bin/vitest run src/__tests__/flow.test.ts
```

## Fixtures

The test harness has three moving parts:

### 1. Mock OAuth server

`startMockServer()` creates an HTTP server on an OS-assigned port. Routes:
- `GET /oauth/authorize` → returns 200 (real installer hits this via browser; in tests the mocked `open` calls `/simulate-callback` instead)
- `GET /simulate-callback?code=&error=&state=&port=` → test helper that POSTs to the installer's localhost `/callback` endpoint with the right params
- `POST /oauth/token` → returns a canned `{access_token, refresh_token, scope}`; `behavior.tokenStatus` overrides to 400 for failure tests

### 2. Stubbed `open` module

```ts
vi.mock('open', () => ({
  default: async (url) => { if (__openHandler) await __openHandler(url); return {...}; }
}));
```

A module-level `__openHandler` is set per test via `installOpenStub(mockUrl, port, opts)` — opts can carry an `error` or `stateOverride` to simulate failure paths.

### 3. Per-test tmpHome + mocked `os.homedir`

```ts
vi.mock('os', () => ({ ...actual, homedir: () => __testHome }));
```

`os.homedir()` is non-configurable on Node so we mock the whole `os` module. Each test sets `__testHome = await fs.mkdtemp(...)` in `beforeEach`, and `afterEach` deletes the tmpdir. **This means surfaces.ts's `home()` helper transparently uses the tmpdir during tests — no other code changes needed.**

### 4. Unique callback ports per test

Each surface (`claude-code`, `cursor`, `codex`) has a fixed callback port (53682, 53684, 53685). Reusing them across rapid tests causes `EADDRINUSE` from TCP TIME_WAIT. The `withPort(SURFACES['...'])` helper clones the surface with a fresh port from a monotonic counter starting at 60000.

## When it fails

| Failure | Likely cause | Fix |
|---|---|---|
| `EADDRINUSE :53682` | Two tests reusing the same surface object | Wrap with `withPort(SURFACES[...])` |
| `ENOENT: no such file or directory, open '...~/.claude.json'` | `os.homedir()` not stubbed (still returns real home) | Verify `vi.mock('os', ...)` is at the top of the file, before `import` of surfaces |
| `Test timed out in 5000ms` (§3.2.3 re-install) | Second install reused first install's port; second listener never bound | Use a **fresh** `withPort()` for each install in the same test |
| `Cannot redefine property: default` on `open` mock | `vi.spyOn` instead of `vi.mock` | Use `vi.mock('open', factory)` at module level; runtime handler via `__openHandler` |
| `TypeError: Cannot redefine property: homedir` | Trying to assign `os.homedir = ...` directly | `os` module exports are non-configurable; use `vi.mock` |
| Unhandled rejection `Error: invalid callback` | The state-mismatch test's promise rejection is caught by `expect.rejects` but bubbles before vitest sees it | Acceptable — vitest still reports 12/12 pass |

## What this suite intentionally doesn't cover

- **Real browser launch** — `open` is mocked; can't test that `xdg-open` / `open` / `start` actually fires on the host OS. Covered by L5 manual smoke.
- **Real OS keychain** — `keytar.setPassword` is best-effort and swallowed; tested only by manual install on macOS/Windows.
- **Real biomate.ai backend** — covered by L5 against a staging environment.
- **Codex CLI restart** — codex doesn't auto-detect config changes; user must restart. No way to test programmatically.
