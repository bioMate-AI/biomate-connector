# Demo 4 — Codex CLI × BioMate (Sarek WGS)

**Duration target:** 75 seconds
**Surface:** OpenAI Codex CLI
**Workflow exercised:** WGS variant-calling pipeline WGS variant calling

## Pre-roll

- [ ] `~/.codex/config.toml` has BioMate `[mcp_servers.biomate]` block
- [ ] Terminal, dark theme, 14pt
- [ ] Demo WGS samples at `s3://biomate-demo/wgs/sample{1,2}_{R1,R2}.fq.gz`

## Script

### Beat 1 (0:00–0:08)

Type:

```bash
codex "Run WGS variant-calling pipeline WGS variant calling on samples in s3://biomate-demo/wgs/. GRCh38. GATK best-practices. Annotate with VEP."
```

### Beat 2 — Run (0:08–1:05)

Codex picks the BioMate tool, calls `biomate_session`. Output streams as markdown:

```
Using BioMate workflow 12849 (WGS variant-calling pipeline)…

▸ FastQ → BAM (BWA-MEM2)            [running 4/4 samples]
▸ Mark duplicates                    pending
▸ BQSR                               pending
▸ HaplotypeCaller                    pending
▸ VEP annotation                     pending

[get_run 30s] Phase 2: Mark duplicates — running
[get_run 60s] Phase 3: BQSR — running
[get_run 30s] Phase 4: HaplotypeCaller — running 2/4 samples
…
✓ Run complete. 4,892 variants called per sample (avg). VEP-annotated VCFs in s3://biomate-runs/run-xyz/output/

Top tier-1 pathogenic findings (ClinVar):
  Sample 1: BRCA2 c.5946delT (P) — frameshift
  Sample 2: TP53 R175H (P) — missense, cancer
```

**Caption overlay:** `Polling — Codex doesn't render progress UI, but the model loops on get_run automatically`

### Beat 3 — Payoff (1:05–1:15)

- `codex "open the methods report"`
- Codex calls `export_report` → returns the PDF URL → `open` opens it

**Caption overlay:** `Same BioMate engine, same QC, terse interface that suits the CLI`

## Voiceover

> "Codex CLI doesn't render rich progress, but with BioMate it polls a paired tool to get the same event stream. Real Sarek WGS run — BWA → GATK → VEP — VCFs and a methods PDF from one terminal command."
