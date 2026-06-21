# BioMate Connectors

> Real bioinformatics — `WGS variant-calling pipeline`, CryoSPARC, AlphaFold, ADMET, PBPK, OpenMM, GROMACS — from your favorite AI coding assistant or chat surface.

| Surface | Install | Status |
|---|---|---|
| [Claude Code](./claude-code/) | `npx @biomate/connect claude-code` | ✓ |
| [Claude Desktop](./claude-desktop/) | `npx @biomate/connect claude-desktop` | ✓ |
| [Cursor](./cursor/) | `npx @biomate/connect cursor` | ✓ |
| [Codex CLI](./codex/) | `npx @biomate/connect codex` | ✓ |
| [ChatGPT](./chatgpt/) | [Custom GPT setup →](./chatgpt/INSTALL.md) | ✓ |
| [Open Claw / WeChat](./open-claw/) | `npx @biomate/connect open-claw` | ✓ |
| [Slack](./slack/) | [Add to Slack →](https://biomate.ai/connectors/slack/install) | Pilot (public ~2 weeks post-launch) |
| [Telegram](./telegram/) | [BotFather setup →](./telegram/README.md) | ✓ |
| [Feishu / Lark](./feishu/) | [Open-platform setup →](./feishu/README.md) | ✓ |

## The 30-second pitch

Generic AI assistants can write code about bioinformatics. BioMate **runs** it.

Connect once, then ask anywhere:

> Screen aspirin and caffeine for hERG and CYP3A4 inhibition.

> Run RNA-seq pipeline differential expression on s3://biomate-demo/rnaseq/, treated vs control.

> Refine this particle stack with CryoSPARC homogeneous refinement.

> Look up UniProt P04637 and summarize cancer-associated mutations.

Each prompt finds the right workflow from 2,455 indexed pipelines, fills required parameters, launches on BioMate cloud, and streams phase/step/QC/finding events back to your chat surface in real time.

## Architecture

```
   Claude Code / Cursor / Codex / Claude Desktop
                       │ MCP (stdio)
                       ↓
                  @biomate/mcp-server
                       │
                       ↓
   ChatGPT Action ──→  api.biomate.ai  ←── Slack bot
                       │                ←── Open Claw (WeChat)
                       │                ←── Telegram bot
                       │                ←── Feishu / Lark bot
                       ↓
                BioMate execution engine
                  │   │   │   │
                Cloud execution engine  BioMate
                Batch  workflows
```

- **One source of truth for tools** — [`backend/lib/mcp/tools_manifest.py`](../backend/lib/mcp/tools_manifest.py) defines 14 tools across 3 tiers. Every surface generates its schema from this file; a CI drift test fails the build if they diverge.
- **One OAuth 2.1 + PKCE service** — [`oauth_server/`](../oauth_server/). Scopes are per-surface; revoke individual surfaces at https://biomate.ai/account/connectors.
- **One streaming event format** — every progress event carries `kind`, `summary_md`, `view_url`, optional `thumbnail_png_b64`, plus a structured `delta`. Hosts that support `notifications/progress` (Claude Code, Cursor) render the event timeline; hosts that don't (Codex, Slack, WeChat) poll a paired tool and get the same payloads.

## What's different from a generic "run code" tool

| | Generic | BioMate |
|---|---|---|
| Workflow catalog | None | 2,455 indexed pipelines across 34 domains |
| Execution | Local sandbox | BioMate cloud with GPU support |
| QC gates | None | ADMET hERG, RNA-seq STAR mapping %, CryoSPARC FSC 0.143 — automatic |
| Auto-remediation | None | Auto-loop suggests + tries new params on gate failure |
| Reports | None | Methods + QC + findings as IND/CRO-ready PDF |
| Storage | Ephemeral | S3 with signed-URL uploads + permanent run archives |

## Tools (the 14-tool surface)

### Tier 1 — Agentic (the wow tool)
- **`biomate_session`** — natural-language goal → orchestrated run with streaming events

### Tier 2 — Workflow primitives
`search_workflow` · `get_workflow_spec` · `run_workflow` · `cancel_run` · `list_runs` · `get_run`

### Tier 3 — Outputs, analysis, reporting
`preview_file` · `export_report` · `analyze_results` · `explain_error` · `recall_memory` · `upload_file` · `query_database`

See each surface's README for examples.

## Privacy & security

- OAuth 2.1 + PKCE — public clients, no shared secrets
- Per-surface scope grants, individually revocable
- Refresh tokens hashed at rest (HMAC-SHA256) and rotated on use
- 30-minute JWT access tokens, signed HS256
- No telemetry beyond standard API logs
- Free-tier runs are watermarked; private S3 paths require an account

## Repository layout

```
connectors/
├── installer/         # @biomate/connect npm package (OAuth flow + config writer)
├── claude-code/       # Per-surface README, MCP config snippet
├── claude-desktop/
├── cursor/
├── codex/
├── chatgpt/           # OpenAPI 3.1 spec + GPT setup
├── open-claw/         # WeChat hosted bridge
├── wechat/            # WeChat-native branding
├── slack/             # Slack App bot
├── telegram/          # Telegram Bot API bot
└── feishu/            # Feishu / Lark bot
backend/lib/mcp/                       # Tools manifest (single source of truth)
oauth_server/   # OAuth 2.1 + PKCE server
skills/biomate/                        # Claude Skill bundle (catalog + render templates + OAuth)
```

## Development

```bash
# OAuth server tests
cd backend && python -m pytest tests/connectors/test_oauth_server.py -v

# Installer tests
cd connectors/installer && npm test

# Tools manifest drift test
cd backend && python -m pytest tests/test_tools_manifest.py -v

# Live MCP routing test (requires ANTHROPIC_API_KEY)
cd backend && python -m pytest tests/test_connector_live.py -v
```

## Submission targets

- Anthropic MCP directory: https://github.com/modelcontextprotocol/servers
- Cursor MCP list: https://cursor.directory/mcp
- Anthropic Skills gallery: https://www.claude.com/skills
- OpenAI GPT store: https://chatgpt.com/gpts
- Slack App Directory: https://slack.com/apps

See [`submissions/`](./submissions/) for pre-filled metadata packages.

## License

MIT. BioMate platform usage is governed by https://biomate.ai/terms.
