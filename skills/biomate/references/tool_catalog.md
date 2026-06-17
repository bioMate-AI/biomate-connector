# BioMate Tool Catalog

## Tier 1 — Streaming agentic

### `biomate_session` · **streams**

Run a complete BioMate scientific session from a natural-language goal. BioMate picks the workflow, fills parameters from context, runs on BioMate cloud, handles QC gates with auto-loop remediation, and produces findings. While running, the tool streams progress events (phase started, step completed, QC gate fired, auto-loop remediation, finding) to the host so the user sees real-time updates the same way BioMate's web panel does. Returns the final run summary, a deep link to the live panel, and the report URL.

**Required:** `goal`

## Tier 2 — Workflow primitives

### `search_workflow`

Search the BioMate workflow catalog (2,455 indexed workflows across 34 domains) by natural language. Returns ranked workflow cards with id, name, domain, one-line description, and estimated BioMate cloud cost. Use this when the user wants to pick a workflow explicitly; otherwise prefer biomate_session.

**Required:** `query`

### `get_workflow_spec`

Return the full specification for a workflow: required + optional parameters (with types and allowed values), default QC profile and thresholds, expected input files, estimated cost and runtime, and any license requirements. Call this before run_workflow when the user wants explicit parameter control.

**Required:** `workflow_id`

### `run_workflow` · **streams**

Execute a specific BioMate workflow on BioMate cloud with explicit parameters. Returns a run_id immediately. If stream=true, also emits progress notifications until the run terminates (same events as biomate_session). Use biomate_session instead when the user gave you a natural-language goal.

**Required:** `workflow_id`

### `get_run`

Return everything about a run in one call: status (pending|running|completed|failed), per-phase and per-step progress with timestamps, output files with download URLs, structured findings, QC gate results, and any auto-loop remediations applied. Replaces get_run_status + get_run_results + step-level findings polling.

**Required:** `run_id`

### `cancel_run`

Cancel a running or queued BioMate workflow on BioMate cloud.

**Required:** `run_id`

### `list_runs`

List the user's recent runs with status and timestamps. Filter by status or experiment.

**Required:** `—`

## Tier 3 — Analysis, reporting, memory

### `preview_file`

Render a server-side preview of an output file (FASTA, VCF, CSV/TSV, image, PDF). Returns markdown + optional thumbnail PNG. Use this to show the user what an output looks like without downloading multi-GB files. For input QC of files the user is about to upload, prefer biomate_session (it runs a real QC workflow).

**Required:** `s3_key`

### `export_report`

Render a publication-ready report for a completed run as PDF or markdown. Includes the methods section, QC audit trail, structured findings, and figures. This is what users need for IND submissions, CRO compliance packages, and publication supplementary materials.

**Required:** `run_id`

### `analyze_results`

Ask BioMate's AI to interpret a completed run. Returns natural-language analysis: key findings, quality assessment, scientific interpretation, recommended next steps. Use after get_run when the user asks 'what does this mean?'.

**Required:** `run_id`

### `explain_error`

Diagnose a failed run. Returns the likely root cause (genome mismatch, missing input, OOM, container pull failure, etc.) and the specific fix. Often the next step is run_workflow with corrected params.

**Required:** `run_id`

### `query_database`

Query a biological/chemical database by accession or name. Supported: uniprot, pdb, alphafold, ncbi_gene, dbsnp, clinvar, gnomad, kegg, reactome, chebi, pubchem, hpo, omim, string.

**Required:** `database, query`

### `recall_memory`

Retrieve relevant prior context for the current goal: past runs on similar inputs, validated procedures, findings tagged by the user, learned parameter preferences. Call before biomate_session for repeat users — it dramatically improves param auto-fill and avoids re-running work.

**Required:** `query`

### `upload_file`

Get a one-shot signed S3 PUT URL so the host can upload a local file directly to BioMate's data plane without proxying bytes through the chat transport. Returns the s3_key to use in subsequent tool calls. For files >5MB this is mandatory; for small text payloads, inline strings are fine.

**Required:** `filename`
