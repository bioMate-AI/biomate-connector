# Cursor MCP directory — submission package

Target: https://cursor.directory/mcp (submission form)

## Form fields

| Field | Value |
|---|---|
| Name | BioMate |
| Tagline (≤80 chars) | Run real bioinformatics — nf-core, CryoSPARC, AlphaFold — from Cursor chat |
| Category | Science / Research |
| GitHub URL | https://github.com/bioMate-AI/biomate-connectors |
| Install command | `npx @biomate/connect cursor` |
| Logo URL | https://biomate.ai/static/logo-square-512.png |
| Author | BioMate AI |
| License | MIT |

## Description (Markdown, ~300 words)

> **BioMate** runs real bioinformatics from Cursor's chat panel: nf-core/rnaseq, nf-core/sarek, CryoSPARC, AlphaFold, ADMET screens, PBPK, OpenMM — on AWS Batch with GPU support, with QC gates and FDA-formatted methods reports.
>
> 2,455 indexed workflows across 34 biological domains. Same execution backend as biomate.ai/runs.
>
> ### Install
>
> ```
> npx @biomate/connect cursor
> ```
>
> The installer runs OAuth 2.1 + PKCE against `api.biomate.ai`, writes the MCP server entry into `~/.cursor/mcp.json`, and stashes a refresh token in your OS keychain. Restart Cursor to pick up the new server.
>
> ### Try it
>
> Open chat and ask:
>
> - "Screen aspirin and caffeine for hERG and CYP3A4 inhibition."
> - "Run nf-core/rnaseq DE on s3://biomate-demo/rnaseq/, treated vs control, GRCh38."
> - "Refine s3://biomate-demo/cryo/particles.cs with CryoSPARC homogeneous refinement, C2 symmetry."
>
> ### Tools (14 across 3 tiers)
>
> 1. **Agentic:** `biomate_session` — natural-language goal → orchestrated streaming run
> 2. **Workflow primitives:** `search_workflow`, `get_workflow_spec`, `run_workflow`, `cancel_run`, `list_runs`, `get_run`
> 3. **Outputs/analysis/reporting:** `preview_file`, `export_report`, `analyze_results`, `explain_error`, `recall_memory`, `upload_file`, `query_database`
>
> Streaming progress events render inline in Cursor's chat panel. Add result file URLs to your editor context with one click.

## Screenshots to upload

- `screenshots/cursor-rnaseq-stream.png` (RNA-seq run streaming progress in Cursor chat)
- `screenshots/cursor-admet-qc-gate.png` (ADMET QC gate card rendered inline)
- `screenshots/cursor-cryosparc-result.png` (CryoSPARC result with deep link)
