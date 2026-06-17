# @biomate/connect

> One-line installer that wires BioMate AI into Claude Code, Claude Desktop, Cursor, OpenAI Codex, ChatGPT, and Open Claw (WeChat).

Real bioinformatics — `WGS variant-calling pipeline`, CryoSPARC, AlphaFold, ADMET screens, PBPK — runs from your favorite AI coding assistant. No SaaS lock-in: tokens are stored locally and revocable per-surface from your BioMate account.

## Install

```bash
# Connect Claude Code
npx @biomate/connect claude-code

# Connect Cursor
npx @biomate/connect cursor

# Connect Codex CLI
npx @biomate/connect codex
```

That's it. The CLI opens your browser, runs OAuth 2.1 + PKCE against `https://biomate.ai`, writes the MCP server entry into the right host config file, and stashes a refresh token in your OS keychain.

## Supported surfaces

| Surface | Install command | Config file written |
|---|---|---|
| Claude Code | `npx @biomate/connect claude-code` | `~/.claude.json` |
| Claude Desktop | `npx @biomate/connect claude-desktop` | `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) |
| Cursor | `npx @biomate/connect cursor` | `~/.cursor/mcp.json` |
| Codex CLI | `npx @biomate/connect codex` | `~/.codex/config.toml` |
| ChatGPT | `npx @biomate/connect chatgpt` | (Custom GPT — instructions printed) |
| Open Claw | `npx @biomate/connect open-claw` | (hosted webhook — account linked) |

## What you get

After connecting, your AI assistant has access to BioMate's 14-tool MCP surface:

- **`biomate_session`** — natural-language goal → streaming workflow run with phase/step/QC/findings events
- **`search_workflow`** + **`get_workflow_spec`** — discover from 2,455 indexed workflows
- **`run_workflow`** + **`get_run`** + **`cancel_run`** — fine control
- **`preview_file`**, **`export_report`**, **`recall_memory`**, **`upload_file`**, **`analyze_results`**, **`explain_error`**, **`query_database`**

## Try it

Open your assistant and paste:

```
Screen aspirin and caffeine for hERG and CYP3A4 inhibition.
```

```
Run RNA-seq differential expression on these FASTQs:
  s3://biomate-demo/sample_R1.fq.gz, s3://biomate-demo/sample_R2.fq.gz
Compare treated vs control.
```

```
Look up UniProt P04637.
```

## Privacy & security

- OAuth 2.1 + PKCE; tokens scoped per surface, revocable individually at
  https://biomate.ai/account/connectors
- Refresh tokens hashed at rest with HMAC-SHA256
- Access tokens are 30-minute JWTs; refresh tokens rotate on use (OAuth 2.1 §6.1)
- No telemetry beyond standard API logs

## Development

```bash
npm install
npm run build
npm test
```

## License

MIT
