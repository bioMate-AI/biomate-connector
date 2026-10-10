# @biomate/mcp-server

BioMate MCP server — exposes 19 scientific workflow tools to any MCP-compatible AI host (Claude Code, Claude Desktop, Cursor, Codex CLI).

## Install

Add to your Claude Code or Claude Desktop config:

**`~/.mcp.json`** (Claude Code):
```json
{
  "mcpServers": {
    "biomate": {
      "command": "npx",
      "args": ["-y", "@biomate/mcp-server"],
      "env": {
        "BIOMATE_API_KEY": "bm_live_your_key_here"
      }
    }
  }
}
```

Get your API key at **[dev-public.biomate.ai/account/api-keys](https://dev-public.biomate.ai/account/api-keys)**.

## Tools (19)

| Tool | Purpose |
|---|---|
| `biomate_session` | Plan an analysis and return workflow suggestions and clarification requests |
| `search_workflow` | Search the workflow catalog by natural language |
| `get_workflow_spec` | Full parameter spec for a workflow |
| `run_workflow` | Execute with explicit parameters |
| `get_run` / `watch_run` | Status snapshots, outputs and available findings |
| `cancel_run` | Cancel a running job |
| `list_runs` | History with filters |
| `preview_file` | Server-side preview of output files |
| `export_report` | Markdown findings report (may return pending during preparation) |
| `analyze_results` | AI interpretation of findings |
| `explain_error` | Root-cause diagnosis for failed runs |
| `query_database` | UniProt, PDB, NCBI, ChEMBL, AlphaFold |
| `resolve_accession` | Resolve a public accession |
| `browse_data` / `fetch_public_data` | Browse and stage public datasets |
| `upload_file` | Get signed S3 PUT URL for local uploads |
| `recall_memory` | Retrieve relevant prior runs and procedures |
| `search_literature` | PubMed, Semantic Scholar, OpenAlex |

## Environment variables

| Variable | Description |
|---|---|
| `BIOMATE_API_KEY` | Personal API key (`bm_live_xxx`) — alternative to OAuth |
| `BIOMATE_API_BASE` | BioMate server URL (default: `https://dev-public.biomate.ai`) |

## Example

```
> Screen aspirin and caffeine for hERG inhibition and CYP3A4 metabolism.
```

```
> Run RNA-seq differential expression on s3://bucket/exp1/ — human GRCh38, treated vs control.
```

```
> Fetch GSE183947 from GEO and run the DESeq2 pipeline.
```

## License

MIT — this package only. BioMate platform usage governed by [biomate.ai/terms](https://biomate.ai/terms).

## OAuth sign-in (0.1.1)

Run `npx @biomate/connect@0.1.1 claude-code --auth-base https://dev-public.biomate.ai` (or use `claude-desktop`, `cursor`, or `codex`). This explicitly selects the development deployment. The installer stores a protected credential file and sets `BIOMATE_OAUTH_FILE` in the host configuration. The client refreshes and persists rotating tokens and calls the OAuth gateway using the current user identity. Existing OAuth installations must reconnect; a refresh token is never used as a personal API key.

This package has 19 tools. Instrument tools, PDF/DOCX exports, automatic session execution and progress notifications are not supported by this stdio adapter. Use `get_run` to poll status and export Markdown findings.
