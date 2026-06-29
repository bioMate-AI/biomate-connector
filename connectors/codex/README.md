# BioMate × OpenAI Codex CLI

> Run real bioinformatics from the Codex CLI. Tool calls return progress events as a delta list (Codex doesn't render rich progress UI), so the model loops on `get_run` for live updates.

## Install (30 seconds)

```bash
npx @biomate/connect codex
```

This writes the BioMate MCP entry to `~/.codex/config.toml`. Restart `codex` to pick up the new server.

## Try it

```bash
codex "Screen the compounds in compounds.smi for hERG and CYP3A4 inhibition. Use BioMate."
```

```bash
codex "Run RNA-seq DE on the FASTQs in s3://biomate-demo/rnaseq/, treated vs control, GRCh38."
```

Codex prefers terse interactions — BioMate's `biomate_session` returns a markdown summary + deep link on each progress event, which Codex displays inline. For long runs the model polls `get_run` automatically.

## Manual config

`~/.codex/config.toml`:

```toml
[mcp_servers.biomate]
command = "npx"
args = ["-y", "@biomate/mcp-server"]

[mcp_servers.biomate.env]
BIOMATE_API_BASE = "https://api.biomate.ai"
BIOMATE_REFRESH_TOKEN = "<your-refresh-token>"
```

Refresh token: https://biomate.ai/account/connectors/codex.

## Local dev/test config (against a non-prod backend)

To point Codex at a dev/test BioMate backend using the in-repo Python MCP
server (API-key auth instead of OAuth):

```toml
[mcp_servers.biomate]
command = "/path/to/python"
args = ["/path/to/biomate-connector/mcp/biomate_mcp_server.py"]
startup_timeout_sec = 60

[mcp_servers.biomate.env]
BIOMATE_API_URL = "https://test.stage-public.biomate.ai"
BIOMATE_API_KEY  = "bm_live_…"
```

Note the env var names differ from prod: the Python server reads
`BIOMATE_API_URL` + `BIOMATE_API_KEY`; the npm `@biomate/mcp-server` reads
`BIOMATE_API_BASE` + `BIOMATE_REFRESH_TOKEN`.

## Sandbox & approvals (important)

BioMate's MCP tools make outbound network calls (to the BioMate backend).
Codex runs MCP servers under its sandbox, and in the **restricted** sandbox
modes those calls are blocked/auto-cancelled — you'll see:

```
mcp: biomate/query_database started
mcp: biomate/query_database (failed)
user cancelled MCP tool call
```

Verified on `codex-cli 0.142.3`: `read-only` **and** `workspace-write`
(even with `network_access = true`) both fail this way; only **full access**
lets the tool call complete.

- **Codex desktop app:** just **approve / trust** the tool call (or the
  project) when prompted — it runs interactively with the needed access.
- **Headless CLI:** pass full access for that run:

  ```bash
  codex exec --dangerously-bypass-approvals-and-sandbox \
    "Look up UniProt P04637 with BioMate's query_database tool."
  ```

  Prefer the per-run flag over setting `sandbox_mode = "danger-full-access"`
  globally (the global setting weakens the sandbox for *all* Codex usage).

Verified end-to-end (codex exec → biomate MCP → backend): `query_database`
(P04637 → TP53) and `search_workflow` (hERG → `herg_cardiac_safety_qsar`)
both return real data and the model answers correctly.

## Tools available

Same 14-tool surface as the Claude Code integration. See [`../claude-code/README.md`](../claude-code/README.md).

## Streaming on a non-streaming surface

Codex CLI doesn't render MCP `notifications/progress` like Claude Code does. The model loops by calling `get_run(run_id)` every few seconds — `get_run` returns the current phase/step state plus any new findings, so the model can report deltas inline. Same backend code path, same final result.

## License

MIT.
