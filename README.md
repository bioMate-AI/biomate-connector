# BioMate Connector

Connect BioMate to the AI tools you already use — Claude Code, Claude Desktop, Cursor, Codex, ChatGPT, Slack, and WeChat — and run real bioinformatics pipelines without leaving your chat window. Also connect your lab instruments (Illumina, Nanopore, CryoEM, LC-MS, and more) so data flows automatically into the right pipeline the moment a run finishes.

```
> Screen aspirin and caffeine for hERG inhibition and CYP3A4 metabolism.
> Run RNA-seq differential expression on s3://my-bucket/fastqs/, treated vs control.
> Refine this cryo-EM stack with CryoSPARC homogeneous refinement, C2 symmetry.
```

BioMate finds the right pipeline from 2,455 indexed workflows, fills the parameters, launches on BioMate cloud, and streams live progress back to your assistant. No copy-pasting commands. No waiting for a dashboard to refresh.

## Architecture

![Architecture](docs/figures/architecture.svg)

## Information Flow

![Data Flow](docs/figures/data_flow.svg)

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
lab_instruments/   Lab instrument connectors (Illumina, Nanopore, CryoEM, LC-MS, and 6 more)
oauth_server/      OAuth 2.1 + PKCE authorization server (self-hostable)
mcp/               Shared MCP tools manifest and server — the single source of truth for all surfaces
skills/biomate/    Claude Skill bundle for the Anthropic Skills gallery
tests/             Connector test suites (offline sandbox + live API + 68 lab instrument checks)
```

---

## Lab Instrument Connectors

Connect physical instruments so raw data is routed automatically to the right BioMate workflow the moment a run finishes — no manual upload, no copy-pasting paths.

| Instrument | File | Trigger |
|-----------|------|---------|
| **Illumina BaseSpace** | `lab_instruments/illumina_basespace_connector.py` | New run via BaseSpace API |
| **Oxford Nanopore MinKNOW** | `lab_instruments/nanopore_minknow_connector.py` | Run complete via MinKNOW HTTP API |
| **CryoEM EPU** | `lab_instruments/cryoem_instrument_connector.py` | New `.mrc`/`.mrcs` micrographs in output dir |
| **LC-MS** | `lab_instruments/lcms_connector.py` | New `.raw`/`.d`/`.wiff` files (Thermo, Bruker, Waters, SCIEX) |
| **Flow Cytometer** | `lab_instruments/flow_cytometer_connector.py` | New `.fcs` files (BD, Beckman, Sony) |
| **qPCR** | `lab_instruments/qpcr_connector.py` | New `.eds` (QuantStudio) or `.pcrd` (Bio-Rad CFX) |
| **Plate Reader** | `lab_instruments/plate_reader_connector.py` | New `.xlsx` exports (BioTek, Molecular Devices) |
| **Opentrons OT-2/Flex** | `lab_instruments/opentrons_connector.py` | Protocol complete via robot HTTP API |
| **Benchling ELN** | `lab_instruments/benchling_connector.py` | New entry or assay result via Benchling API |
| **SiLA2 devices** | `lab_instruments/sila2_adapter.py` | gRPC events (Hamilton, Sartorius, etc.) |

Quick start — copy `config.example.yaml` (in `lab_instruments/`), fill in your instrument details, and run:

```bash
pip install -r requirements.txt
python3 lab_instruments/instrument_watcher.py --config config.yaml
```

---

## The tools your assistant gets

BioMate exposes **17 tools** across three tiers.

### Lite set (consumer surfaces — Claude.ai, ChatGPT GPT, Slack)

| Tool | What it does |
|---|---|
| `biomate_session` | **The main one.** Describe your goal; BioMate picks the workflow, fills params, runs on BioMate cloud, and streams progress back. |
| `upload_file` | Get a presigned S3 URL to upload a local file before running a workflow. |
| `export_report` | Download the findings report (PDF / DOCX) after a run completes. |

### Full set (Claude Desktop / Cursor / Codex / API)

Beyond the lite set, you get workflow primitives (`search_workflow`, `get_workflow_spec`, `run_workflow`, `get_run`, `cancel_run`, `list_runs`), output tools (`preview_file`, `analyze_results`, `explain_error`), database access (`query_database`), memory (`recall_memory`), and data connectors (`resolve_accession`, `browse_data`, `fetch_public_data`).

See [`connectors/README.md`](connectors/README.md) for the full tool reference.

---

## Writing good goals

The `goal` parameter in `biomate_session` is plain English — one to three sentences. Include:

1. **What** — the analysis type (`ADMET screening`, `RNA-seq DE`, `variant calling`, `cryo-EM refinement`)
2. **Data** — inline SMILES/sequences, `s3://` paths, GEO/SRA accession numbers, or upload first with `upload_file`
3. **Key parameters** — organism, comparisons, thresholds, symmetry, strand orientation — anything that matters

You can omit anything BioMate can reasonably infer. It will ask if something is genuinely ambiguous.

**Examples that work well:**

```
Screen aspirin (CC(=O)Oc1ccccc1C(=O)O) and caffeine (Cn1cnc2c1c(=O)n(c(=O)n2C)C)
for hERG inhibition, CYP3A4 liability, and oral bioavailability.
```

```
RNA-seq differential expression on s3://lab-bucket/exp42/fastqs/ — human GRCh38,
dUTP strand-specific, treated (n=3) vs control (n=3), FDR threshold 0.05.
```

```
Whole-genome variant calling on the uploaded FASTQ pair, GRCh38,
GATK HaplotypeCaller, germline mode.
```

```
Fetch GSE183947 from GEO and run the same RNA-seq DE pipeline.
```

```
Run CryoSPARC homogeneous 3D refinement on s3://cryo/job042/, C2 symmetry, box 256.
```

---

## Authentication

BioMate connectors use an API key (or OAuth 2.1 + PKCE for browser-based surfaces).

**Generate an API key:**
1. Go to [biomate.ai → Settings → API Keys](https://app.biomate.ai/settings/api-keys)
2. Click **New key**, give it a name, and copy the value — it's only shown once
3. Set it in your environment:
   ```bash
   export BIOMATE_API_KEY=bm_live_...
   ```

**Test your key:**
```bash
curl -H "X-API-Key: $BIOMATE_API_KEY" https://app.biomate.ai/api/tools/ping
# → {"status": "ok", "user": "you@example.com"}
```

**For Claude Desktop / Cursor / Codex (MCP config):**
```json
{
  "mcpServers": {
    "biomate": {
      "command": "python3",
      "args": ["-m", "mcp.biomate_mcp_server"],
      "env": {
        "BIOMATE_API_URL": "https://app.biomate.ai",
        "BIOMATE_API_KEY": "bm_live_..."
      }
    }
  }
}
```

---

## Self-hosting the OAuth server

If you're integrating BioMate into your own infrastructure, the OAuth 2.1 + PKCE server in `oauth_server/` is self-contained and runnable independently.

```bash
pip install -r requirements.txt
python -m oauth_server
```

See [`oauth_server/oauth/server.py`](oauth_server/oauth/server.py) for configuration options.

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
