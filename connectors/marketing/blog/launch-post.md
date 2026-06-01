# Run real bioinformatics from Claude, Cursor, ChatGPT, and Codex — meet BioMate Connectors

*Today we're launching six BioMate connectors — one OAuth flow, one MCP tool surface, six places to call it from.*

---

## The problem with "AI for science" today

If you've tried using an AI assistant for a bioinformatics task — RNA-seq, variant calling, ADMET, cryo-EM — you've hit the same wall:

> The model writes you a Nextflow config. Or a snakemake rule. Or a Python script that imports DESeq2. Then it stops, because it can't actually run any of it.

That gap — between "tell me what to do" and "do it" — is BioMate.

We've spent the last 14 months building an **execution engine**: 2,455 indexed workflows across 34 biological domains, all of nf-core's drug-development and genomics pipelines, CryoSPARC, AlphaFold, OpenMM, GROMACS, AutoDock Vina, the Bioconductor ecosystem, and ~60 custom workflows for drug discovery (PBPK, BOIN, ADMET, IND §2.6.1 narrative generation, …). It runs on AWS Batch (with GPU queues for the protein and cryo-EM workloads), with auto-loop QC gates that catch and try to remediate failures, and produces FDA-formatted methods reports for IND / CRO submissions.

Until today, you used it from `biomate.ai`. Today, you can use it from **wherever you already work**.

## What we're launching

Six connectors:

| Surface | Install |
|---|---|
| [Claude Code](https://github.com/bioMate-AI/biomate-connectors/tree/main/connectors/claude-code) | `npx @biomate/connect claude-code` |
| [Claude Desktop](https://github.com/bioMate-AI/biomate-connectors/tree/main/connectors/claude-desktop) | `npx @biomate/connect claude-desktop` |
| [Cursor](https://github.com/bioMate-AI/biomate-connectors/tree/main/connectors/cursor) | `npx @biomate/connect cursor` |
| [Codex CLI](https://github.com/bioMate-AI/biomate-connectors/tree/main/connectors/codex) | `npx @biomate/connect codex` |
| [ChatGPT](https://chatgpt.com/g/g-biomate) | One-click install from the GPT store |
| [Open Claw (WeChat)](https://github.com/bioMate-AI/biomate-connectors/tree/main/connectors/open-claw) | `npx @biomate/connect open-claw` |

A Slack app is in private beta with three pilot labs and will ship publicly in 2 weeks.

## What you can actually do

```
> Screen aspirin and caffeine for hERG and CYP3A4 inhibition.
```

Pick the ADMET pipeline from the catalog. Normalize the SMILES. Run DeepPK on AWS Batch. Compute the QC gate (hERG IC50 < 10μM blocks; CYP3A4 inhibition ratio > 0.5 flags). Return a structured result + a methods PDF.

```
> Run nf-core/rnaseq differential expression on s3://biomate-demo/rnaseq/treated vs control, GRCh38.
```

Find the FASTQs. Build the sample sheet. Submit STAR + salmon + DESeq2. Show the top-20 DE table inline with a volcano plot thumbnail. Five minutes, six samples, about forty cents.

```
> Generate an IND §2.6.1 nonclinical pharmacology narrative from my last three runs.
```

Recall the runs from memory. Extract the structured results. Hand them to a fine-tuned Gemini Flash narrative writer with FDA-formatted templates. Hand back a 12-page DOCX.

These aren't mockups. Same execution engine biomate.ai uses; same QC gates; same reports.

## Why six surfaces?

Different researchers live in different places. Computational biologists are in Cursor and the terminal. Bench scientists are in ChatGPT. Chinese labs are in WeChat. We didn't want anyone to have to switch tools to use real bioinformatics; we wanted the bioinformatics to come to where they already are.

The architecture makes this easy: **one source of truth for the tool surface**, generated for each host.

## The tool surface — 14 tools, 3 tiers

We resisted the temptation to ship 50 tools. The audit against our platform showed 243 of 253 endpoints were exposed for distribution at the cost of usability — the model had to write polling loops, the user had to read raw JSON. We settled on 14 tools.

**Tier 1 — agentic.** One streaming tool, `biomate_session`. Natural-language goal in. BioMate searches the catalog, fills the params, runs the workflow, streams `phase` / `step` / `qc_gate` / `auto_loop` / `finding` / `done` events back as MCP `notifications/progress`. Casual users only ever call this.

**Tier 2 — workflow primitives.** `search_workflow`, `get_workflow_spec`, `run_workflow`, `cancel_run`, `list_runs`, `get_run` — for power users and scripts.

**Tier 3 — outputs, analysis, reporting.** `preview_file`, `export_report`, `analyze_results`, `explain_error`, `recall_memory`, `upload_file`, `query_database`.

Schemas live in [`backend/lib/mcp/tools_manifest.py`](https://github.com/bioMate-AI/biomate-connectors/blob/main/backend/lib/mcp/tools_manifest.py). The MCP server, the ChatGPT OpenAPI manifest, the Claude Skill bundle's tool catalog, and the Open Claw schema all generate from that file. A CI drift test fails the build if any of them get out of sync.

## OAuth 2.1 + PKCE — proper, not "API keys in env vars"

Every connector runs through one OAuth 2.1 + PKCE flow. Public clients (no shared secrets). Scopes per surface, individually revocable from your BioMate account page. Refresh tokens are hashed at rest (HMAC-SHA256) and rotated on use per OAuth 2.1 §6.1. Access tokens are 30-minute HS256 JWTs.

Switch laptops? `npx @biomate/connect cursor` and you're done.

Want to revoke Cursor's access but keep Claude Code? Hit `/oauth/grants/revoke` with `surface=cursor`. Done.

## What's special about this — and what isn't

- **Special:** running real nf-core / CryoSPARC / AlphaFold from chat. Not running a code interpreter. Not running a Python sandbox. Running the same pipelines that produce IND submissions and Nature methods sections.
- **Special:** auto-loop QC. When a hERG IC50 fails, BioMate's auto-loop suggests revised parameters (e.g. a logP filter cutoff) and tries again — and the chat surface shows you the parameter diff as a was→now table.
- **Special:** methods reports. Every run produces a 5–15 page PDF with methods, parameters, QC results, and citations to the underlying tools. IND-ready, manuscript-ready.
- **Not special:** the MCP protocol. We use the standard one. The connectors are public OSS. Anyone can fork them; anyone can build similar bridges to their own execution engine.

## Pricing

Free tier: 3 runs without an account (IP + device-fingerprint gated, watermarked outputs). Standard tier: existing biomate.ai plans, unchanged. Connectors are not a separate SKU.

## What we built and what's next

Shipped today:
- OAuth 2.1 + PKCE server
- `@biomate/connect` installer for 6 surfaces
- 14-tool MCP surface with streaming
- ChatGPT GPT
- Open Claw (WeChat)
- Public connectors repo: https://github.com/bioMate-AI/biomate-connectors

Next:
- Slack app public release (~2 weeks)
- Codex CLI streaming protocol when OpenAI adds it
- More opinionated `biomate_session` defaults per surface (we found Cursor and Codex want shorter event summaries)

## Try it now

```bash
npx @biomate/connect claude-code
```

Or open the [BioMate GPT](https://chatgpt.com/g/g-biomate) in ChatGPT.

Or scan the WeChat QR code at biomate.ai/wechat.

If you find a bug, file it at [github.com/bioMate-AI/biomate-connectors/issues](https://github.com/bioMate-AI/biomate-connectors/issues). If you want a Slack pilot, [email us](mailto:hello@biomate.ai).

---

*— The BioMate AI team*
