# Demo videos — recording guide

Six demos, one per surface. Each demo is ~60–90 seconds and follows the same
three-beat structure so they cut together cleanly for a launch montage.

## The 3-beat structure

1. **Setup (0:00–0:10)** — surface fresh, prompt visible, no BioMate context
2. **Run (0:10–0:50)** — paste the demo prompt; show the live workflow execution
3. **Payoff (0:50–end)** — open the deep link to biomate.ai/runs/<id>; the methods PDF; the QC card

## Recording stack

- macOS screen recording: `Cmd-Shift-5` → "Record selected portion" → 2560×1440
- Editor: iMovie (free, sufficient for cuts + captions)
- Captions: hardcode key callouts (workflow ID, runtime, cost) as text overlays
- Length cap: 90s per surface, 4 min for the montage

## Six demos

| # | Surface | Demo prompt | Workflow exercised |
|---|---|---|---|
| 1 | Claude Code | `Screen aspirin and caffeine for hERG and CYP3A4` | ADMET (DeepPK) |
| 2 | Claude Desktop | `Run nf-core/rnaseq DE: treated vs control, GRCh38` | RNA-seq |
| 3 | Cursor | `Refine particles.cs with CryoSPARC homogeneous, C2 sym` | CryoSPARC |
| 4 | Codex CLI | `Variant calling on WGS samples, sarek best-practices` | Sarek WGS |
| 5 | ChatGPT | `IND §2.6.1 narrative from my latest ADMET + PBPK runs` | Reports |
| 6 | Open Claw (WeChat) | `筛选 aspirin 的 hERG 抑制` | ADMET (multilingual) |

Detailed scripts: [`01-claude-code.md`](./01-claude-code.md) through [`06-open-claw.md`](./06-open-claw.md).

## Launch montage

After all 6 are recorded, cut a 60-second montage:
- 0:00–0:05 — Logo + tagline: "Run real bioinformatics from anywhere"
- 0:05–0:50 — 5s clip from each demo (just the most striking moment — usually the QC card or the workflow viewer)
- 0:50–0:60 — `npx @biomate/connect <your surface>` + URL

Script for the montage voiceover (if used): [`montage-voiceover.md`](./montage-voiceover.md).
