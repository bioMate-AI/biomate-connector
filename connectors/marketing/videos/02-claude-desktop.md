# Demo 2 — Claude Desktop × BioMate (RNA-seq)

**Duration target:** 75 seconds
**Surface:** Claude Desktop (macOS app)
**Workflow exercised:** nf-core/rnaseq

## Pre-roll

- [ ] `~/Library/Application Support/Claude/claude_desktop_config.json` has BioMate MCP
- [ ] Quit + relaunch Claude Desktop
- [ ] Window sized to ~1400×900 so it captures well
- [ ] Have 6 FASTQ paths ready to paste (3 treated, 3 control), e.g. `s3://biomate-demo/rnaseq/{T1,T2,T3,C1,C2,C3}_{R1,R2}.fq.gz`

## Script

### Beat 1 — Setup (0:00–0:10)

**On screen:** Fresh Claude Desktop chat, MCP indicator showing "BioMate" with a green dot.

**Caption overlay:** `Claude Desktop + BioMate — nf-core/rnaseq end-to-end`

### Beat 2 — Run (0:10–1:00)

**Paste:**

```
Run nf-core/rnaseq on the following FASTQs. Treated vs control. GRCh38. Show the top 20 DE genes when done.

Treated:
  s3://biomate-demo/rnaseq/T1_R1.fq.gz, s3://biomate-demo/rnaseq/T1_R2.fq.gz
  s3://biomate-demo/rnaseq/T2_R1.fq.gz, s3://biomate-demo/rnaseq/T2_R2.fq.gz
  s3://biomate-demo/rnaseq/T3_R1.fq.gz, s3://biomate-demo/rnaseq/T3_R2.fq.gz

Control:
  s3://biomate-demo/rnaseq/C1_R1.fq.gz, s3://biomate-demo/rnaseq/C1_R2.fq.gz
  s3://biomate-demo/rnaseq/C2_R1.fq.gz, s3://biomate-demo/rnaseq/C2_R2.fq.gz
  s3://biomate-demo/rnaseq/C3_R1.fq.gz, s3://biomate-demo/rnaseq/C3_R2.fq.gz
```

**On screen:**

- Claude: *"I'll use BioMate's nf-core/rnaseq pipeline (workflow 12733)."*
- `biomate_session` streams:
  - Phase 1: Sample sheet validation ✓
  - Phase 2: STAR alignment (6 samples, ~5 min on Batch)
  - Phase 3: salmon quantification
  - Phase 4: DESeq2 differential expression
- QC gate: STAR mapping rate per sample shown as a small table (all ≥85%, all PASS)
- Final: top 20 DE genes table inline + thumbnail of MA-plot

**Caption overlay (mid-roll):** `6 FASTQs · 5 min · $0.40`

### Beat 3 — Payoff (1:00–1:15)

- Claude: *"Top up-regulated: TNF (log2FC 4.2), IL6 (3.8), CXCL10 (3.5)…  Full report: https://biomate.ai/runs/…"*
- Click the URL → workflow viewer in browser
- Scroll to the volcano plot → click "Download methods report"

**Caption overlay:** `Real nf-core/rnaseq run · STAR + salmon + DESeq2 · methods PDF ready`

## Voiceover

> "Real nf-core/rnaseq. Six samples, full STAR + salmon + DESeq2 pipeline, on AWS Batch from a single Claude Desktop prompt. Live progress, automatic QC, methods PDF for your manuscript."
