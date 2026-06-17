# Demo 1 — Claude Code × BioMate (ADMET)

**Duration target:** 60 seconds
**Surface:** Claude Code (terminal)
**Workflow exercised:** ADMET (DeepPK) with hERG + CYP3A4 gates

## Pre-roll checklist

- [ ] Fresh terminal, 14pt font minimum, dark theme
- [ ] `~/.claude.json` already has BioMate MCP installed (run `npx @biomate/connect claude-code` off-camera)
- [ ] Restart Claude Code so the MCP server is live
- [ ] Logged into biomate.ai in browser (for the deep-link payoff)

## Script

### Beat 1 — Setup (0:00–0:08)

**On screen:** Terminal with Claude Code prompt empty. Maybe a comment: `# Start screen recording`.

**Caption overlay:** `Claude Code + BioMate`

### Beat 2 — Run (0:08–0:50)

**Type slowly:**

```
Screen aspirin and caffeine for hERG inhibition and CYP3A4 metabolism. Block if hERG IC50 < 10μM.
```

**Press Enter. On screen:**

- Claude Code responds: *"I'll use BioMate's ADMET screening pipeline."*
- Calls `biomate_session` tool
- Streams progress events inline:
  ```
  ▸ Phase 1: Compound preparation     ✓ (3s)
  ▸ Phase 2: ADMET prediction         ⠋ running
      ▸ DeepPK hERG model             ✓
      ▸ DeepPK CYP3A4 model           ✓
      ▸ Lipinski + PAINS filters      ✓
  ▸ Phase 3: QC gate evaluation       ⠋ running
  ```
- QC gate result renders as inline markdown table:
  ```
  Compound   hERG IC50 (μM)  CYP3A4 ratio  Gate
  Aspirin    >100            0.12          ✓ PASS
  Caffeine   38.4            0.04          ✓ PASS
  ```
- Total runtime: ~35 seconds on Batch

**Caption overlay:** `Real run on BioMate cloud — not a local mock`

### Beat 3 — Payoff (0:50–0:60)

- Claude posts: *"Both compounds pass. Full report: https://biomate.ai/runs/run-abc123 — methods PDF in the run page."*
- Click the URL. Browser opens biomate.ai/runs/run-abc123 with the workflow viewer.
- Pan over the QC card, then click "Download methods report" (PDF opens).

**Caption overlay:** `IND/CRO-ready methods report`

**End frame:** Logo + `npx @biomate/connect claude-code`

## Voiceover (optional)

> "Generic AI can write code about hERG screening. Claude Code with BioMate **runs it** — on BioMate cloud, with QC gates, and gives you the methods PDF for your IND submission. Connect in 30 seconds."

## Notes for editor

- Trim out any "thinking..." pauses longer than 1.5s
- Highlight the QC table with a soft glow
- Cut to the PDF as soon as it opens — don't show the download progress bar
