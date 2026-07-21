# Claude Directory — submission package

Target: **Anthropic Claude Directory** (Team/Enterprise admin → Settings →
Directory → Submit connector). This is the *official, reviewed* Claude
Directory — NOT the open-source `modelcontextprotocol/servers` GitHub list
(that PR lives in [`anthropic-mcp-directory.md`](./anthropic-mcp-directory.md)).

Reference: https://claude.com/docs/connectors/building/submission#submit-your-connector

---

## 0. Prerequisites (confirm BEFORE submitting)

- [ ] Submitting from a **Team or Enterprise** organization (individual/Pro cannot submit).
- [ ] Submitter is **Owner / Primary Owner** (or a custom role with Directory-management permission).
- [ ] Access the submission form via **org admin settings → Directory**.

> If BioMate is only on an individual/Pro plan today, this is the gating step —
> open a Team/Enterprise org first.

---

## 1. Listing metadata

| Field | Limit | Value |
|---|---|---|
| **Server name** | ≤100 chars | `BioMate` |
| **Tagline** | ≤55 chars | `Run real bioinformatics pipelines from chat` |
| **URL slug** | permanent, immutable | `biomate` |
| **Categories** | 1–5 | Science & Research; Developer Tools *(pick exact labels from the admin picker)* |
| **Icon / logo** | high-res square PNG/SVG | ⚠️ attach BioMate logo (confirm ≥512×512, transparent bg) |
| **Description** | ≤2000 chars | see §2 |

## 2. Description (≤2000 chars)

> BioMate turns natural-language requests into real bioinformatics, drug-discovery,
> and clinical workflows that run on BioMate's cloud compute — not simulations.
>
> Ask in plain English ("RNA-seq differential expression on these FASTQs, human
> GRCh38, treated vs control" or "screen these SMILES for hERG and CYP3A4") and
> BioMate selects the right pipeline from 2,455 indexed workflows across 34
> domains, pre-fills parameters from your goal, executes on AWS Batch/GPU, runs QC
> gates with auto-loop remediation, and streams live progress back into your chat.
> You get structured findings, previewable outputs, and publication-ready reports
> (PDF/DOCX) suitable for IND submissions, CRO compliance packages, and paper
> supplements.
>
> Coverage includes 400+ community bioinformatics pipelines (RNA-seq, WGS/variant
> calling, ATAC/ChIP, single-cell), CryoSPARC cryo-EM, AlphaFold/ESMFold/OpenFold
> structure prediction, OpenMM/GROMACS molecular dynamics, AutoDock Vina docking,
> the Bioconductor ecosystem, and ~60 custom drug-discovery workflows (PBPK, BOIN
> dose-escalation, ADMET, IND §2.6.1 narrative generation).
>
> The primary tool, `biomate_session`, handles ~90% of requests end to end.
> Sixteen additional tools give power users explicit control: search the catalog,
> inspect a workflow spec, run with exact parameters, poll runs, preview output
> files, export reports, analyze results, explain failures, query biological
> databases, resolve public accessions (GEO/SRA/ENA), browse and fetch public
> data, recall prior context, and upload files.
>
> Auth is OAuth 2.1 + PKCE with per-surface, individually-revocable scopes.
> Install: `npx @biomate/connect claude-code` (also Claude Desktop, Cursor, Codex).

## 3. Documentation, legal & support

| Field | Value | Status |
|---|---|---|
| Documentation URL | https://biomate.ai/connectors | ⚠️ confirm live & public |
| Privacy policy URL (HTTPS) | https://biomate.ai/legal/privacy | ⚠️ publish [`connectors/legal/privacy.md`](../legal/privacy.md) |
| Terms of service | https://biomate.ai/terms | ✅ |
| Support email | support@biomate.ai | ✅ |
| Support URL | https://biomate.ai/support | ✅ |

The privacy URL is referenced in the connector `README.md`, the generated
`mcp/tools_manifest.json` (`server.privacy_policy_url`), and
`connectors/chatgpt/openapi.json` (`info.x-privacy-policy`). Keep all in sync.

## 4. Technical requirements

| Requirement | Status | Notes |
|---|---|---|
| OAuth 2.0 / 2.1 | ✅ | PKCE authorization server in `oauth_server/` |
| Remote HTTPS MCP endpoint (Streamable HTTP or SSE) | ✅ | **LIVE (staging):** `https://mcp.stage-public.biomate.ai/mcp` — Streamable HTTP (MCP `2025-11-25`), OAuth 2.1 + PKCE + DCR + RFC 9728/8414 discovery, valid Let's Encrypt cert. Code: `remote_mcp/`. Prod cutover to `https://app.biomate.ai/mcp` pending. The repo also ships a local stdio server for Desktop/Cursor/Codex. |
| Per-tool `title` | ✅ | All 17 tools — `mcp/tools_manifest.py`, regenerated into `tools_manifest.json` |
| `readOnlyHint` / `destructiveHint` annotations | ✅ | All 17 tools; `cancel_run` → destructive; read tools → readOnly. Emitted under each tool's `annotations`. |
| Tools tested | ✅ | `tests/test_tools_manifest.py`, `tests/test_mcp_e2e.py`, `tests/test_connector_live.py` |

### Tool annotation summary (17 tools)

| Tool | title | readOnly | destructive |
|---|---|---|---|
| biomate_session | Run BioMate Session | – | – |
| search_workflow | Search Workflows | ✓ | – |
| get_workflow_spec | Get Workflow Spec | ✓ | – |
| run_workflow | Run Workflow | – | – |
| get_run | Get Run Details | ✓ | – |
| cancel_run | Cancel Run | – | **✓** |
| list_runs | List Runs | ✓ | – |
| preview_file | Preview Output File | ✓ | – |
| export_report | Export Report | – | – |
| analyze_results | Analyze Results | ✓ | – |
| explain_error | Explain Run Error | ✓ | – |
| query_database | Query Biological Database | ✓ | – |
| resolve_accession | Resolve Accession | ✓ | – |
| browse_data | Browse Data Repository | ✓ | – |
| fetch_public_data | Fetch Public Data | – | – |
| recall_memory | Recall Memory | ✓ | – |
| upload_file | Upload File | – | – |

## 5. Reviewer access

- [ ] Provide a **test account** on a Team/Enterprise org with the connector installed.
- [ ] Provide **credentials** (OAuth login OR a scoped API key) so the reviewer can execute a real run.
- [ ] Provide a **1-line demo prompt** that completes cheaply, e.g.:
      `Screen aspirin (CC(=O)Oc1ccccc1C(=O)O) for hERG inhibition and oral bioavailability`
- [ ] Note expected runtime + that a small BioMate cloud cost is incurred per run.

## 6. Policy attestations (7 — check each at submission)

- [ ] Complies with the Directory / connector guidelines
- [ ] Complies with Anthropic's API / usage policies
- [ ] Financial-transaction disclosure (usage-based platform billing on biomate.ai)
- [ ] AI-generated media disclosure (reports/narratives are AI-assisted)
- [ ] Prompt-injection handling (untrusted tool output is not executed as instructions)
- [ ] Data-collection disclosure (matches the published privacy policy)
- [ ] Public documentation available (docs URL resolves)

## 7. Pre-submission checklist (blockers)

- [x] **Tool annotations** — 17 tools have `title` + `readOnlyHint`/`destructiveHint` (was blocker #1)
- [ ] **Privacy policy page LIVE** — publish `connectors/legal/privacy.md` at `biomate.ai/legal/privacy` after legal review (was blocker #2; content drafted)
- [ ] Team/Enterprise org + Owner role confirmed
- [x] **Remote HTTPS MCP endpoint published & reachable** — LIVE at `https://mcp.stage-public.biomate.ai/mcp` (staging); prod `app.biomate.ai/mcp` cutover pending
- [ ] Documentation URL live and public
- [ ] Logo asset attached (≥512×512)
- [ ] Reviewer test account + credentials prepared

## 8. Naming decision

Submit as **BioMate** with slug **`biomate`** — not `biomate-connect`.
`@biomate/connect` is only the installer CLI's npm package name (an
implementation detail); the Directory lists the product/brand. Users search for
"BioMate". Mention `npx @biomate/connect …` inside the description as the
install method.
