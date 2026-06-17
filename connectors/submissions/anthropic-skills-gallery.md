# Anthropic Skills gallery — submission package

Target: https://www.claude.com/skills (gallery submission form, ~Q2 2026)

## Skill bundle

Already in repo at [`skills/biomate/`](../../skills/biomate/):

- `SKILL.md` — instructions, examples per workflow class
- `references/workflow_catalog.md` — top-200 workflow IDs + descriptions
- `references/render_templates.md` — markdown templates for findings cards, QC gate failures, auto-loop diffs
- `references/tool_catalog.md` — 14 tools with usage hints
- `assets/qc_gate_card.html` — server-rendered QC gate card
- `assets/finding_card.html` — server-rendered finding card
- `scripts/connect.sh` — one-line OAuth installer

## Form fields

| Field | Value |
|---|---|
| Skill name | BioMate — bioinformatics workflow execution |
| Author | BioMate AI |
| Tagline | Run real bioinformatics on BioMate cloud from Claude (RNA-seq/WGS, CryoSPARC, AlphaFold, ADMET) |
| Categories | Science, Research, Healthcare |
| Repository | https://github.com/bioMate-AI/biomate-connectors |
| Skill path | `skills/biomate/` |
| Install (Claude.ai) | One-click "Connect BioMate" (OAuth popup) |
| Install (Claude Code) | `npx @biomate/connect claude-code` |
| License | MIT |

## Description

> BioMate is an execution engine for bioinformatics: 2,455 indexed workflows
> across 34 biological domains (RNA-seq/WGS, CryoSPARC, AlphaFold/ESMFold,
> OpenMM, GROMACS, Vina, Bioconductor, custom drug-discovery pipelines),
> running on BioMate cloud with GPU support, QC gates, and FDA-formatted
> methods reports.
>
> This Skill carries:
> - Tool catalog for the 14-tool BioMate MCP surface
> - Workflow catalog covering the top-200 most-used pipelines (so Claude
>   doesn't need a tools/list round-trip to recommend one)
> - Render templates for findings cards, QC gate failures, auto-loop
>   parameter diffs — so the Claude.ai chat surface looks like BioMate's
>   own workflow viewer panel
> - One-line OAuth installer
>
> When the user describes a bioinformatics task, Claude routes through
> `biomate_session` (streaming agentic tool) and renders incoming events
> using the bundled templates.

## Demo prompt for the gallery card

> "Screen aspirin and caffeine for hERG and CYP3A4 inhibition. Block hERG IC50 below 10 μM."

## Pre-submission checklist

- [ ] Skill bundle lints clean against Anthropic's Skills schema
- [ ] First-run OAuth works from claude.ai
- [ ] Demo prompt produces a clean ADMET run end-to-end in <60s
- [ ] Render templates verified to display correctly in Claude.ai chat
