# BioMate × Claude Code

> Run real bioinformatics from your terminal. RNA-seq, CryoSPARC, ADMET, PBPK, AlphaFold, variant calling — all via Claude Code's MCP transport.

![BioMate in Claude Code](./screenshot.png)

## Install (30 seconds)

```bash
npx @biomate/connect claude-code
```

The installer opens your browser, runs OAuth 2.1 + PKCE against `biomate.ai`, and writes the MCP server entry into `~/.claude.json`.

**Restart Claude Code** to pick up the new server.

## Try it

```
> Screen aspirin and caffeine for hERG inhibition and CYP3A4 metabolism.
```

```
> Run RNA-seq pipeline differential expression on these FASTQs:
  s3://biomate-demo/treated_R1.fq.gz, s3://biomate-demo/treated_R2.fq.gz
  s3://biomate-demo/control_R1.fq.gz, s3://biomate-demo/control_R2.fq.gz
  Compare treated vs control, GRCh38.
```

```
> Refine this cryo-EM particle stack with CryoSPARC homogeneous refinement:
  s3://biomate-demo/particles.cs
  Apply C2 symmetry.
```

```
> Look up UniProt P04637 and summarize the top 3 cancer-associated mutations.
```

## What you get

Claude Code gains 14 MCP tools across 3 tiers:

### Tier 1 — Streaming agentic tool (the wow tool)

- **`biomate_session`** — natural-language goal in, BioMate orchestrates: searches the 2,455-workflow catalog, fills required parameters, submits to BioMate cloud, streams phase + step + QC + finding events back as `notifications/progress`. Claude renders the live timeline inline.

### Tier 2 — Workflow primitives

| Tool | Purpose |
|---|---|
| `search_workflow` | Ranked catalog search across 34 biological domains |
| `get_workflow_spec` | Required + optional params, allowed values, default QC profile, cost estimate |
| `run_workflow` | Start a run; `stream: true` for inline events |
| `cancel_run` | Cancel an in-flight run |
| `list_runs` | History with experiment/workflow/status filters |
| `get_run` | One call: status + phases + steps + findings + output files |

### Tier 3 — Outputs, analysis, reporting

| Tool | Purpose |
|---|---|
| `preview_file` | Server-side preview/parse of FASTA / VCF / CSV / MRC / images / PDF |
| `export_report` | Render methods + QC + findings as PDF or markdown (IND/CRO-ready) |
| `analyze_results` | AI interpretation, free-form |
| `explain_error` | Root-cause diagnosis when a run fails |
| `recall_memory` | Retrieve relevant prior runs, findings, procedures |
| `upload_file` | Get a signed S3 PUT URL for local uploads |
| `query_database` | UniProt / PDB / AlphaFold / NCBI / ChEMBL |

## Why it's different

- **It actually runs.** Not "explain how to run WGS variant-calling pipeline" — Claude Code starts the run on BioMate cloud and streams progress back. Same execution engine as biomate.ai/runs.
- **No surface lock-in.** Same OAuth, same tools across Claude Code, Cursor, Codex, ChatGPT. Switch hosts without re-onboarding.
- **2,455 indexed workflows.** Including 400+ community bioinformatics pipelines, CryoSPARC, AlphaFold/ESMFold/OpenFold, OpenMM/GROMACS, AutoDock Vina, RDKit, DESeq2/edgeR/limma, Seurat, scanpy, BOIN, NONMEM/nlmixr2, and 1000+ Bioconductor packages.
- **Auto-loop QC.** ADMET hERG too high? BioMate's auto-loop re-suggests parameters; Claude shows the diff inline.

## Manual config (advanced)

If `npx @biomate/connect` is not available, add this to `~/.claude.json`:

```json
{
  "mcpServers": {
    "biomate": {
      "command": "npx",
      "args": ["-y", "@biomate/mcp-server"],
      "env": {
        "BIOMATE_API_BASE": "https://api.biomate.ai",
        "BIOMATE_REFRESH_TOKEN": "<your-refresh-token>"
      }
    }
  }
}
```

Get your refresh token at https://biomate.ai/account/connectors/claude-code.

## Disconnect

```bash
curl -X POST https://api.biomate.ai/oauth/grants/revoke \
  -H "Cookie: <your-session-cookie>" \
  -d "surface=claude-code"
```

Or visit https://biomate.ai/account/connectors and click **Revoke** next to Claude Code.

## License

MIT — code in this directory only. BioMate platform usage is governed by https://biomate.ai/terms.
