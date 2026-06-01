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

## Tools available

Same 14-tool surface as the Claude Code integration. See [`../claude-code/README.md`](../claude-code/README.md).

## Streaming on a non-streaming surface

Codex CLI doesn't render MCP `notifications/progress` like Claude Code does. The model loops by calling `get_run(run_id)` every few seconds — `get_run` returns the current phase/step state plus any new findings, so the model can report deltas inline. Same backend code path, same final result.

## License

MIT.
