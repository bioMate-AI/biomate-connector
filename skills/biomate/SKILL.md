---
name: biomate
description: Run real bioinformatics, drug-discovery, and clinical workflows on BioMate's AWS Batch infrastructure. Use whenever the user asks to screen compounds (ADMET, docking), analyze sequencing data (RNA-seq, WGS, variant calling), reconstruct cryo-EM, model PK/PBPK, predict protein structure, or generate IND / CRO regulatory reports. The Skill streams live progress (phase started, step completed, QC gate fired, auto-loop remediation, finding) inline and returns a deep link to the live BioMate panel for the full UI.
license: proprietary
version: 2.0.0
---

# BioMate Skill

BioMate is a bioinformatics + drug-discovery platform with 2,455 indexed Nextflow / Bioconductor workflows, auto-loop QC remediation, and a structured findings/methods report generator. This Skill wires BioMate's MCP server into Claude so the user can run real workflows from chat and watch progress stream inline.

## When to use this Skill

Use the BioMate tools whenever the user's request involves any of:

- **Drug discovery**: ADMET screening, hERG / CYP3A4 liability, docking, virtual screening, lead optimization, PBPK modelling, clinical trial dose escalation (BOIN, mTPI).
- **Sequencing**: bulk or single-cell RNA-seq, WGS / WES variant calling (sarek), ChIP-seq, ATAC-seq, methylation, amplicon (16S / ITS).
- **Structural biology**: cryo-EM single-particle (CryoSPARC, RELION), AlphaFold structure prediction, protein–ligand docking.
- **Regulatory & CRO**: IND module 2.6.1 narrative, CRO compliance package, methods report DOCX/PDF.
- **Knowledge lookups** on proteins, structures, variants, pathways, compounds: UniProt, PDB, AlphaFold, NCBI Gene, ClinVar, KEGG, ChEMBL, PubChem.

Do **not** use these tools for general chit-chat, code generation unrelated to biology, or quick literature questions that don't need a workflow run — those don't need BioMate.

## Tool selection rules

There are 14 tools in three tiers. Default to the **agentic** tier:

1. **For 90 % of requests, call `biomate_session` first.** Pass the user's full natural-language goal verbatim plus any structured inputs (S3 keys, SMILES, FASTQ paths). BioMate will pick the workflow, fill parameters, run on AWS Batch, handle QC gates with auto-loop remediation, and produce findings — all streamed back as progress notifications.

2. **Use the primitives only when the user wants explicit control.** Examples:
   - "Show me what workflows are available for cryo-EM" → `search_workflow`.
   - "What parameters does nf-core/sarek take?" → `search_workflow` then `get_workflow_spec`.
   - "Run rnaseq with these exact params: …" → `run_workflow`.
   - "What's the status of run abc-123?" → `get_run`.
   - "Cancel run abc-123" → `cancel_run`.

3. **For repeat users, call `recall_memory` first.** It returns past runs on similar inputs, validated procedures, and learned parameter preferences. Use the recalled context to enrich the `biomate_session` call (cite the prior run in the user-facing reply: "Picking up where run abc-123 left off…").

4. **For result interpretation after a run completes:**
   - Quick natural-language read: `analyze_results`.
   - Structured report for publication / IND / CRO: `export_report` (`format=pdf` or `docx`).
   - File-level inspection: `preview_file` with the s3_key from `get_run`.

5. **For failed runs:** `explain_error`. Then act on the suggested fix — usually a new `run_workflow` or `biomate_session` call with corrected params.

6. **Knowledge lookups not tied to a workflow:** `query_database`.

7. **Local files larger than a few KB:** `upload_file` returns a signed S3 PUT URL. The user's host uploads directly to S3 (don't proxy bytes through the chat).

## Rendering streamed progress

When `biomate_session` (or `run_workflow` with `stream=true`) streams `notifications/progress`, render each event inline using the templates in `references/render_templates.md`. The progress payload looks like:

```json
{"kind": "qc_gate", "summary_md": "**QC gate:** hERG = 6.2 → halt",
 "view_url": "https://biomate.ai/runs/abc/qc#hERG",
 "thumbnail_png_b64": "…",
 "delta": {...raw data...}}
```

- Always render `summary_md`.
- If `thumbnail_png_b64` is set, display the image immediately under the summary.
- Always end with: *"View the full live panel: `view_url`"* — this is the one-click escape hatch into BioMate's full UI for the user.
- When a `done` event arrives, summarize the run in 2–3 bullet points + deep link + (if the user asked for a report) call `export_report` and attach the PDF.

## Authentication

The Skill expects an OAuth-bound API key in the MCP server's `BIOMATE_API_KEY` env var. If you receive a 401 from any tool call, tell the user:

> Your BioMate connection has expired. Run `npx @biomate/connect claude-code` (or click <https://biomate.ai/connect/claude>) to reauthorize.

Do **not** ask the user for raw API keys in chat — the connect command writes them to the OS keychain automatically.

## Quick examples

```text
USER: Screen these SMILES for hERG and CYP3A4: CC(=O)Oc1ccccc1C(=O)O, CN1C=NC2=C1C(=O)N(C(=O)N2C)C
ASSISTANT: → biomate_session(goal="Screen these SMILES for hERG and CYP3A4 liability", inputs={smiles: [...]})
```

```text
USER: Run nf-core/rnaseq on s3://exp42/fastq/, human paired-end, dUTP stranded
ASSISTANT: → biomate_session(goal="Run nf-core/rnaseq …", inputs={input_dir: "s3://exp42/fastq/", genome: "GRCh38", strandedness: "reverse"})
```

```text
USER: What pipelines do you have for CryoSPARC?
ASSISTANT: → search_workflow(query="CryoSPARC single-particle reconstruction", domain="cryo_em")
```

```text
USER: Why did run xyz-42 fail?
ASSISTANT: → explain_error(run_id="xyz-42")  → suggest fix  → confirm with user before re-running
```

## References

- `references/workflow_catalog.md` — top-200 workflow IDs with one-line descriptions. Read this **before** calling `search_workflow` to avoid round-trips for common workflows.
- `references/render_templates.md` — markdown templates for progress events, QC gate cards, findings cards, auto-loop diffs.
- `assets/qc_gate_card.html`, `assets/finding_card.html` — server-renderable HTML for the file-display capability.
- `scripts/connect.sh` — one-line OAuth installer the user can run from a terminal.

## What this Skill does NOT do

- It does not store credentials or run anything locally. Every tool call hits BioMate's API at `https://api.biomate.ai`.
- It does not substitute for the BioMate web UI for power workflows. When the user wants pixel-perfect parameter editing, parameter overrides on a running job, or interactive 3D viewers, send them to `view_url`.
- It does not access the user's billing, audit log, or other users' data. Those are intentional security boundaries.
