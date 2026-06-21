# BioMate Connector — API & Coverage Index
**Date:** 2026-06-21  
**Branch:** connectors/integrations  
**Source:** Derived from `connectors/openapi-biomate-api.yaml`, `mcp/tools_manifest.py`, `frontend/server/routes.ts`, `backend/lib/galaxy/webapps/galaxy/api/nextflow_executor.py`

---

## 1. Connectors Covered

### AI Surface Connectors

| Connector | Surface | Test file | Tests | Type |
|-----------|---------|-----------|-------|------|
| Claude Code / Desktop / Cursor / Codex | MCP stdio (live) | `tests/test_mcp_e2e.py` | 2 | Live post-sync |
| Claude Code / Desktop / Cursor / Codex | MCP protocol + tool schemas | `tests/test_connector_sandbox.py` | 29 | Offline/unit |
| ChatGPT GPT Actions | OpenAPI 3.1 + OAuth | `tests/test_chatgpt_connector.py` | 16 | Unit + live |
| Slack | Slash commands, signature verify, block builders | `tests/test_slack_bot.py` | 33 | Unit |
| WeChat / Open Claw | Query, message handling | `tests/test_wechat_open_claw.py` | 11 | Unit |
| Coze (ByteDance) | Plugin schema + tool dispatch | `tests/test_coze_plugin.py` | 18 | Unit |
| Tool manifest | Schema validity, lite set, Anthropic/OpenAI SDK shape | `tests/test_tools_manifest.py` | 22 | Unit |
| OAuth 2.1 server | PKCE flow, code exchange, refresh rotation | `tests/test_oauth_server.py` | 7 | Integration |
| OAuth 2.1 security | Cross-surface token rejection, scope widening, rate limits, reuse detection | `tests/test_oauth_security.py` | 10 | Security |
| OAuth router | Route-level auth middleware | `tests/test_oauth_router.py` | 15 | Unit |

### Lab Instrument Connectors

| Instrument | Protocol | Test class | Tests | Integration depth |
|------------|----------|------------|-------|-------------------|
| Illumina BaseSpace | OAuth2 + REST | `TestIlluminaBaseSpace` | 11 | Mock HTTP server |
| Oxford Nanopore MinKNOW | HTTP REST | `TestNanoporeMinKNOW` | 9 | Mock HTTP server |
| CryoEM EPU | HTTP REST + file scan | `TestCryoEMEPU` | 8 | Mock HTTP + tmpdir |
| Flow Cytometer (BD/Beckman/Sony) | FCS file | `TestFlowCytometerSmoke` | 3 | Fixture file (smoke) |
| LC-MS (Thermo/Bruker/Waters/SCIEX) | File-based | `TestLCMS` | 5 | tmpdir |
| qPCR (QuantStudio/Bio-Rad CFX) | EDS/PCRD file | `TestQPCRSmoke` | 3 | Fixture file (smoke) |
| Opentrons OT-2 / Flex | HTTP REST | `TestOpentrons` | 8 | Mock HTTP server |
| Plate Reader (BMG/BioTek) | CSV/TXT file | `TestPlateReaderSmoke` | 3 | Fixture file (smoke) |
| SiLA2 (Hamilton/Sartorius) | gRPC stub | `TestSiLA2` | 8 | Stub mode (no gRPC server) |
| Benchling ELN | REST + assay push | `TestBenchling` | 8 | Mock HTTP server |

### End-to-End Integration

| What | Test class | Tests |
|------|-----------|-------|
| Instrument file scan → `_notify_biomate()` → `POST /api/instruments/new-data` | `TestInstrumentToBioMateIntegration` | 9 |

**Total automated tests: 222**  
Three smoke-only connectors (Flow Cytometer, qPCR, Plate Reader) require fixture files to run fully; they skip gracefully without them.

---

## 2. Connector APIs (BioMate Public REST API)

Source: `connectors/openapi-biomate-api.yaml`  
Base URLs: `https://app.biomate.ai` · `http://localhost:5000`

**Authentication**

| Mode | Header | When to use |
|------|--------|-------------|
| JWT | `Authorization: Bearer <jwt>` or `Cookie: biomate_token` | Browser, OAuth surfaces (Claude Desktop, Slack, ChatGPT, WeChat) |
| Static API key | `X-API-Key: <key>` | Headless (Claude Code, Cursor, CI, lab instruments) |

### Auth

| Method | Path | Summary |
|--------|------|---------|
| `POST` | `/api/auth/login` | Login — returns JWT + sets `biomate_token` cookie. `rememberMe=true` → 30-day cookie |
| `POST` | `/api/auth/logout` | Logout — clears cookie |
| `GET` | `/api/auth/me` | Get current authenticated user |

### Agentic Session (Open Claw)

| Method | Path | Summary |
|--------|------|---------|
| `GET` | `/api/open-claw/tools` | List all 17 BioMate tool schemas |
| `POST` | `/api/open-claw/stream` | Agentic Claude session (SSE) — Claude + all tools in loop, max 10 iterations |

SSE event sequence from `/api/open-claw/stream`:

| Event | Payload | Terminal? |
|-------|---------|-----------|
| `delta` | `{text: string}` | No |
| `tool_call_start` | `{id, name}` | No |
| `tool_call_complete` | `{id, name, input}` | No |
| `tool_result` | `{tool_use_id, name, result}` | No |
| `complete` | `{iterations: number}` | **Yes** |
| `error` | `{error: string}` | **Yes** |

### Workflow Discovery & Execution

| Method | Path | Summary |
|--------|------|---------|
| `POST` | `/api/workflows/search` | Semantic search over 2,455-workflow database; returns ranked stubs |
| `POST` | `/api/workflows/execute` | Launch a Nextflow/Galaxy workflow on AWS Batch; returns `WorkflowRun` immediately (async) |

### Run Management

| Method | Path | Summary |
|--------|------|---------|
| `GET` | `/api/workflows/runs` | List runs — filter by `status`, `projectId`, `chatSessionId`, `limit` |
| `GET` | `/api/workflows/runs/{id}` | Full run record + inline `output_files` + `inline_findings` |
| `GET` | `/api/workflows/runs/{id}/status` | Lightweight poll — `status`, `progress`, `currentPhase`, `currentStep` |
| `POST` | `/api/workflows/runs/{id}/cancel` | Cancel running/queued workflow |
| `GET` | `/api/workflows/runs/{id}/findings` | AI-synthesized findings + QC metrics + `summary_md` |
| `GET` | `/api/workflows/runs/{id}/output_files` | List S3 output files (`name`, `s3_uri`, `s3_key`, `size_bytes`) |
| `GET` | `/api/workflows/runs/{id}/logs` | Nextflow `stdout`, `stderr`, `nextflow_log` |

### Analysis

| Method | Path | Summary |
|--------|------|---------|
| `POST` | `/api/workflows/runs/{id}/ai/analyze` | AI interpretation of outputs; optional `question` field |
| `POST` | `/api/workflows/explain_error` | Diagnose failed run — `root_cause`, `category`, `fix`, `fix_params` |

### Data & Databases

| Method | Path | Summary |
|--------|------|---------|
| `POST` | `/api/databases/query` | Query biological/chemical DB — 14 databases supported (see below) |
| `GET` | `/api/data/sources` | List configured public data sources |
| `GET` | `/api/data/browse` | Browse a public FTP/HTTP source directory |
| `POST` | `/api/data/fetch` | Stage a public file into BioMate S3 workspace |
| `GET` | `/api/s3/browse` | List objects in BioMate S3 workspace or user's own bucket |
| `POST` | `/api/accession/resolve` | Resolve GEO/SRA/ENA/DDBJ accession → matched workflow + pre-filled params |

Databases supported by `/api/databases/query`:

> `uniprot` · `pdb` · `alphafold` · `ncbi_gene` · `dbsnp` · `clinvar` · `gnomad` · `kegg` · `reactome` · `chebi` · `pubchem` · `hpo` · `omim` · `string`

Accession routing by `/api/accession/resolve`:

| Prefix | Archive | Workflow |
|--------|---------|----------|
| `GSE*`, `GDS*`, `GSM*` | GEO | Geo Data Connector (id=10672) |
| `SRR*`, `SRX*`, `SRS*`, `SRP*`, `PRJNA*` | SRA | nf-core/fetchngs (id=12704) |
| `ERR*`, `ERX*`, `ERP*`, `PRJEB*` | ENA | nf-core/fetchngs (id=12704) |
| `DRR*`, `DRP*`, `PRJDB*` | DDBJ | nf-core/fetchngs (id=12704) |
| `E-MTAB-*`, `E-GEOD-*` | ArrayExpress | nf-core/fetchngs (id=12704) |

### Files

| Method | Path | Summary |
|--------|------|---------|
| `POST` | `/api/files/preview` | Preview S3 file — image (presigned URL) · CSV (HTML table) · FASTA · VCF · text |
| `POST` | `/api/uploads/signed_url` | Get presigned S3 PUT URL for direct large-file upload |

### Memory

| Method | Path | Summary |
|--------|------|---------|
| `GET` | `/api/memory/relevant` | Retrieve prior runs, findings, procedures, and parameter preferences for a goal |

### Export

| Method | Path | Summary |
|--------|------|---------|
| `POST` | `/api/export/qc_report` | Generate QC report — `html` (attachment) or `json` |

**Total connector-facing REST endpoints: 22**

---

## 3. Full System API (Node.js + Galaxy Backend)

### Node.js Server — Port 5000

#### Auth
```
POST   /api/auth/register
POST   /api/auth/login
POST   /api/auth/logout
GET    /api/auth/me
POST   /api/auth/forgot-password
POST   /api/auth/request-password-reset
POST   /api/auth/reset-password
POST   /api/auth/verify-email
POST   /api/auth/resend-verification
GET    /api/auth/config
GET    /api/auth/github
GET    /api/auth/github/callback
GET    /api/auth/google
GET    /api/auth/google/callback
GET    /api/auth/oauth/status
POST   /api/auth/mfa/setup
GET    /api/auth/mfa/status
POST   /api/auth/mfa/verify-setup
POST   /api/auth/mfa/challenge
DELETE /api/auth/mfa/disable
```

#### Chat & AI
```
POST   /api/chat/stream
POST   /api/biomate/chat
GET    /api/biomate/health
GET    /api/biomate/status/:invocationId
POST   /api/ai_assistant/generate_workflow
POST   /api/ai_assistant/research_query
POST   /api/ai_assistant/benchmark_query
GET    /api/ai_assistant/benchmarks/:scenario
POST   /api/ai_assistant/search_ranked
GET    /api/ai_assistant/tool_comparison
GET    /api/ai_assistant/pilot/start
GET    /api/ai_assistant/pilot/:pilotId/status
GET    /api/ai_assistant/pilot/:pilotId/estimate
GET    /api/ai_assistant/skills/available
GET    /api/ai_assistant/citations/methods
GET    /api/ai_assistant/citations/export
POST   /api/llm/classify
POST   /api/compress-session
```

#### Workflows & Runs
```
GET    /api/workflows/search
GET    /api/workflows/spec
GET    /api/workflows/by-domain
POST   /api/workflows/execute
POST   /api/workflows/validate
POST   /api/workflows/preflight
POST   /api/workflows/validate_profile
POST   /api/workflows/estimate_cost
GET    /api/workflows/events
GET    /api/workflows/license-requirements
POST   /api/workflows/parse_error
POST   /api/workflows/explain_error
POST   /api/workflows/type-compatibility
GET    /api/workflows/:name/citations
GET    /api/workflows/:id/extract-subworkflow

GET    /api/workflows/runs
GET    /api/workflows/runs/overview
GET    /api/workflows/runs/:id
GET    /api/workflows/runs/:id/status
GET    /api/workflows/runs/:id/logs
GET    /api/workflows/runs/:id/outputs
GET    /api/workflows/runs/:id/output_files
GET    /api/workflows/runs/:id/findings
GET    /api/workflows/runs/:id/findings/report
GET    /api/workflows/runs/:id/iterations
GET    /api/workflows/runs/:id/interactions
GET    /api/workflows/runs/:id/step-file
GET    /api/workflows/runs/:id/compare
GET    /api/workflows/runs/:id/reproducibility
GET    /api/workflows/runs/:id/download_commands
POST   /api/workflows/runs/:id/cancel
POST   /api/workflows/runs/:id/pause
POST   /api/workflows/runs/:id/resume
POST   /api/workflows/runs/:id/rerun-from-step
POST   /api/workflows/runs/:id/promote
POST   /api/workflows/runs/:runId/rerun
POST   /api/workflows/runs/:id/ai/analyze
POST   /api/workflows/runs/:id/ai/autofix
POST   /api/workflows/runs/:id/ai/query
GET    /api/runs/:anyId/meta
GET    /api/runs/:anyId/outputs
GET    /api/runs/:runId/output/:filename

GET    /api/invocations
GET    /api/invocations/:id
GET    /api/invocations/:id/outputs
GET    /api/invocations/:id/report
GET    /api/invocations/:id/fetch-outputs
GET    /api/invocations/:id/download-all
POST   /api/invocations/:id/nextflow/status
POST   /api/invocations/:id/ai/autofix
GET    /api/invocations/:invocationId/qc-events
GET    /api/invocations/:invocationId/qc-audit-download
GET    /api/invocations/:invocationId/methods-report
GET    /api/invocations/:invocationId/software
```

#### Workflow Chains & SSE
```
GET    /api/workflows/chain
POST   /api/workflows/chain
GET    /api/workflows/chain/:chainRunId
GET    /api/workflows/chain/:chainRunId/manifest
GET    /api/workflows/chain/:chainRunId/outputs
POST   /api/workflow-chains/record-run
GET    /api/workflow-chains
GET    /api/workflows/:invocationId/events
GET    /api/workflows/:invocationId/realtime/status
GET    /api/workflows/:invocationId/deep_diagnose
GET    /api/workflows/:invocationId/error-summary
GET    /api/pipelines/:pipelineId/events
GET    /api/galaxy/workflows/sse/:workflowId/:invocationId
```

#### Files, S3 & Data
```
GET    /api/s3/browse
POST   /api/s3/upload/init
GET    /api/s3/download
GET    /api/s3/files/:key
POST   /api/uploads/signed_url
GET    /api/outputs/:id
GET    /api/outputs/:id/preview
GET    /api/outputs/:id/download
GET    /api/outputs/:id/display
GET    /api/outputs/:id/parse
GET    /api/files/preview
POST   /api/data/analyze
GET    /api/data/browse
POST   /api/data/fetch
GET    /api/data/sources
POST   /api/datasets/upload
POST   /api/datasets/import-url
POST   /api/datasets/register-path
POST   /api/datasets/validate
GET    /api/datasets/session
GET    /api/datasets/deferred
GET    /api/datasets/:id/materialize
POST   /api/accession/resolve
```

#### Lab Instruments
```
GET    /api/instruments
POST   /api/instruments
GET    /api/instruments/:id
GET    /api/instruments/:id/health
GET    /api/instruments/events
GET    /api/instruments/events/:id
POST   /api/instruments/new-data
```

#### Galaxy Passthrough
```
GET    /api/galaxy/version
GET    /api/galaxy/tools/search
GET    /api/galaxy/tools/categories
GET    /api/galaxy/tools/installed/list
GET    /api/galaxy/tools/:toolId
GET    /api/galaxy/tools/:toolId(*)/schema
GET    /api/galaxy/datasets/search
GET    /api/galaxy/datasets/category/:category
GET    /api/galaxy/datasets/:datasetId
GET    /api/galaxy/datasets/:datasetId/resolve
GET    /api/galaxy/histories
GET    /api/galaxy/histories/:historyId/datasets
GET    /api/galaxy/jobs/:jobId
GET    /api/galaxy/jobs/:jobId/stdout
GET    /api/galaxy/jobs/:jobId/stderr
POST   /api/galaxy/jobs/:jobId/pause
POST   /api/galaxy/jobs/:jobId/resume
GET    /api/galaxy/workflows
POST   /api/galaxy/workflows/invoke
GET    /api/galaxy/workflows/:workflowId/invocations/:invocationId
POST   /api/galaxy/workflows/:workflowId/invocations/:invocationId/autofix3/run
GET    /api/galaxy/workflows/:workflowId/invocations/:invocationId/autofix3/sessions
GET    /api/galaxy/autofix3/status
```

#### Sessions, Memory & User
```
GET    /api/sessions
GET    /api/sessions/:sessionId
POST   /api/sessions/generate-title
GET    /api/user/domain-profile
GET    /api/user/domain-taxonomy
GET    /api/users/me/usage
GET    /api/users/credentials
GET    /api/users/credentials/map
GET    /api/users/credentials/:toolId
GET    /api/users/credentials/s3
POST   /api/users/credentials/s3/verify
GET    /api/memory/relevant
GET    /api/memory/user
GET    /api/memory/user/episodes
GET    /api/memory/user/preferences
POST   /api/memory/user/corrections
GET    /api/memory/sessions
GET    /api/memory/sessions/:sessionId
POST   /api/memory/sessions/:sessionId/end
GET    /api/memory/projects
GET    /api/memory/projects/search
GET    /api/memory/projects/:projectId
GET    /api/memory/projects/:projectId/episodes
GET    /api/memory/projects/:projectId/knowledge
GET    /api/memory/projects/:projectId/procedures
POST   /api/memory/projects/:projectId/procedures/match
POST   /api/memory/projects/:projectId/consolidate
GET    /api/memory/episodes/:episodeId/outcome
GET    /api/memory/interactions
GET    /api/memory/procedures/:procedureId/usage
POST   /api/memory/session-summary
POST   /api/memory/sync
```

#### QC, Code & Nextflow
```
GET    /api/qc/profiles
POST   /api/export/qc_report
POST   /api/databases/query
POST   /api/code/run
GET    /api/nextflow/steps
```

#### Payments & Billing
```
GET    /api/payments/pricing
GET    /api/payments/subscription
POST   /api/payments/create-checkout-session
POST   /api/payments/create-portal-session
POST   /api/payments/cancel-subscription
POST   /api/payments/add-credits
POST   /api/payments/start-trial
POST   /api/payments/webhook
```

#### Templates & Collections
```
GET    /api/workflow-templates
GET    /api/workflow-templates/search
GET    /api/workflow-templates/popular/:limit?
GET    /api/workflow-templates/validated
GET    /api/workflow-templates/:id
POST   /api/workflow-templates/:id/usage
GET    /api/parameter-templates
GET    /api/parameter-templates/search
GET    /api/parameter-templates/popular/:limit?
GET    /api/parameter-templates/validated
GET    /api/parameter-templates/tool/:toolId
GET    /api/parameter-templates/:id
POST   /api/parameter-templates/:id/usage
GET    /api/collections
POST   /api/collections
POST   /api/collections/auto-pair
POST   /api/collections/create-paired
```

#### Admin, Telemetry & Misc
```
GET    /api/health
GET    /api/resources/status
GET    /api/admin/process-health
GET    /api/admin/index-health
GET    /api/admin/error-rates
GET    /api/admin/audit-log
GET    /api/admin/audit-log/summary
GET    /api/admin/invite-codes
POST   /api/admin/invite-codes
GET    /api/admin/invite-codes/:id
GET    /api/admin/invite-codes/:id/uses
POST   /api/telemetry/event
GET    /api/telemetry/events
GET    /api/telemetry/summary
GET    /api/telemetry/cost-summary
POST   /api/telemetry/confusion-triage
POST   /api/feedback
POST   /api/feedback/pilot-kpi
POST   /api/contact
POST   /api/newsletter/subscribe
GET    /api/rationale/:rationale_id
GET    /api/workflows/:workflowName/types
POST   /api/workflow/step/execute
GET    /api/workflow/step/:step_id/status
POST   /api/workflow/validate-before-execute
POST   /api/experiments
GET    /api/experiments
GET    /api/experiments/:id
GET    /api/experiments/:id/results
GET    /api/interactive-tools/launch
POST   /api/interactive/launch
GET    /api/interactive/sessions
```

#### Open Claw
```
POST   /api/open-claw/stream
GET    /api/open-claw/tools
```

#### Internal (Galaxy ↔ Node)
```
GET    /internal/runs/by-invocation/:invId/status
POST   /internal/sse-inject
```

---

### Galaxy Backend — Port 8081

```
POST   /api/nextflow/execute
GET    /api/nextflow/status/{run_id}
GET    /api/nextflow/alive/{run_id}
POST   /api/nextflow/cancel/{run_id}
POST   /api/nextflow/pause/{run_id}
POST   /api/nextflow/resume/{run_id}
GET    /api/nextflow/steps
GET    /api/nextflow/runs/{run_id}/output_files
GET    /api/nextflow/runs/{run_id}/reproducibility
POST   /api/nextflow/analyze-outputs
POST   /api/nextflow/parse_error
POST   /api/nextflow/fetch_public_data
GET    /api/nextflow/outputs/download
POST   /api/nextflow/estimate_cost
GET    /api/nextflow/s3/browse
GET    /api/cryosparc/status/{run_id}
GET    /api/cryosparc/gpu_metrics
GET    /api/cryosparc/instances
POST   /api/cryosparc/execute
GET    /api/qc/profiles
GET    /api/workflows/preflight
GET    /api/workflows/estimate_cost
POST   /api/workflows/validate_profile
GET    /api/workflows/{workflow_name}/citations
GET    /api/workflows/{workflow_name}/phases/{phase_id}/rationale
GET    /api/invocations/{invocation_id}/qc-events
GET    /api/invocations/{invocation_id}/qc-audit-download
GET    /api/invocations/{invocation_id}/methods-report
GET    /api/invocations/{invocation_id}/software
POST   /api/invocations/{invocation_id}/autoloop-suggestions
POST   /api/invocations/{invocation_id}/autoloop-dismiss
GET    /api/rationale/{rationale_id:path}
GET    /api/admin/index-health
```

---

## MCP Tool Set Reference

### Lite set — 3 tools (Claude.ai, ChatGPT GPT, Slack, WeChat)

| Tool | Purpose |
|------|---------|
| `biomate_session` | **Primary entry point.** Describe goal in plain English → BioMate picks workflow, fills params, runs, streams progress |
| `upload_file` | Get presigned S3 URL for file upload before a run |
| `export_report` | Download PDF/DOCX report after a run completes |

### Full set — 17 tools (Claude Desktop, Cursor, Codex, API)

| Tool | Purpose |
|------|---------|
| `biomate_session` | As above |
| `search_workflow` | Find workflows by intent/keyword |
| `get_workflow_spec` | Fetch full params + steps for a workflow_id |
| `run_workflow` | Launch run with explicit params |
| `get_run` | Poll run status + findings |
| `cancel_run` | Cancel in-flight run |
| `list_runs` | List recent runs with status filter |
| `preview_file` | Render S3 file (table / image / text) |
| `export_report` | Download PDF/DOCX report |
| `analyze_results` | Ask AI a question about run outputs |
| `explain_error` | Get AI diagnosis of a failed run |
| `query_database` | Query UniProt, ChEMBL, PDB, NCBI Gene, etc. |
| `resolve_accession` | Resolve GEO/SRA/ENA/DDBJ → workflow + params |
| `browse_data` | List files in S3 / FTP / public sources |
| `fetch_public_data` | Download public dataset to BioMate S3 |
| `recall_memory` | Semantic search over past runs / params |
| `upload_file` | Get presigned S3 URL for upload |
