# BioMate Connector

Connect BioMate to the AI tools you already use — Claude Code, Claude Desktop, Cursor, Codex, ChatGPT, Slack, and WeChat — and run real bioinformatics pipelines without leaving your chat window.

```
> Screen aspirin and caffeine for hERG inhibition and CYP3A4 metabolism.
> Run nf-core/rnaseq differential expression on s3://my-bucket/fastqs/, treated vs control.
> Refine this cryo-EM stack with CryoSPARC homogeneous refinement, C2 symmetry.
```

BioMate finds the right pipeline from 2,455 indexed workflows, fills the parameters, launches on AWS Batch, and streams live progress back to your assistant. No copy-pasting commands. No waiting for a dashboard to refresh.

---

## Get started in 30 seconds

```bash
npx @biomate/connect claude-code
```

Pick your surface, authenticate once via your browser, and you're done. The CLI writes the MCP config for you and stores your token in the OS keychain.

| Surface | Command |
|---|---|
| Claude Code | `npx @biomate/connect claude-code` |
| Claude Desktop | `npx @biomate/connect claude-desktop` |
| Cursor | `npx @biomate/connect cursor` |
| Codex CLI | `npx @biomate/connect codex` |
| ChatGPT | See [`connectors/chatgpt/INSTALL.md`](connectors/chatgpt/INSTALL.md) |
| Slack | See [`connectors/slack/README.md`](connectors/slack/README.md) |
| WeChat / Open Claw | `npx @biomate/connect open-claw` |

---

## What's in this repo

```
connectors/        Per-surface install guides, MCP config snippets, and the @biomate/connect CLI
oauth-server/      OAuth 2.1 + PKCE authorization server (self-hostable)
mcp/               Shared MCP tools manifest and server — the single source of truth for all surfaces
skills/biomate/    Claude Skill bundle for the Anthropic Skills gallery
tests/             Connector test suites (offline sandbox + live API)
```

---

## The tools your assistant gets

Once connected, your AI assistant can call 14 BioMate tools:

**`biomate_session`** — the main one. Describe your goal in plain language; BioMate orchestrates the rest and streams progress back as it runs.

Beyond that, workflow primitives (`search_workflow`, `run_workflow`, `get_run`, `cancel_run`) and output tools (`preview_file`, `export_report`, `analyze_results`, `query_database`) give you fine-grained control when you need it.

See [`connectors/README.md`](connectors/README.md) for the full tool reference.

---

## Self-hosting the OAuth server

If you're integrating BioMate into your own infrastructure, the OAuth 2.1 + PKCE server in `oauth-server/` is self-contained and runnable independently.

```bash
pip install -r requirements.txt
python -m oauth-server
```

See [`oauth-server/oauth/server.py`](oauth-server/oauth/server.py) for configuration options.

---

## Security

- OAuth 2.1 + PKCE — no shared secrets, no passwords stored
- Per-surface scope grants, individually revocable at [biomate.ai/account/connectors](https://biomate.ai/account/connectors)
- Refresh tokens hashed at rest (HMAC-SHA256) and rotated on every use
- 30-minute JWT access tokens

---

## License

MIT — for the connector code in this repository. BioMate platform usage is governed by [biomate.ai/terms](https://biomate.ai/terms).

Questions? [support@biomate.ai](mailto:support@biomate.ai)
