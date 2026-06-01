# Demo 3 — Cursor × BioMate (CryoSPARC)

**Duration target:** 90 seconds
**Surface:** Cursor IDE
**Workflow exercised:** CryoSPARC homogeneous refinement (cryosparc_standard_spa subwf)

## Pre-roll

- [ ] `~/.cursor/mcp.json` has BioMate; restart Cursor
- [ ] Open a Python notebook scratch buffer in Cursor — gives the demo the "researcher in their tool" vibe
- [ ] Demo particle stack at `s3://biomate-demo/cryo/particles_25k.cs` (25K particles, refines in ~8 min)

## Script

### Beat 1 (0:00–0:10)

**On screen:** Cursor open with `analyze_cryo.py` in left pane (empty), chat panel right.

**Type in chat:**

```
@biomate Refine s3://biomate-demo/cryo/particles_25k.cs with CryoSPARC homogeneous refinement. Apply C2 symmetry. Use the 3.0 Å reference at s3://biomate-demo/cryo/ref_3A.mrc.
```

### Beat 2 — Run (0:10–1:15)

Claude (via MCP):
- *"I'll use BioMate's cryosparc_standard_spa subworkflow."*
- Calls `biomate_session` with stream=true
- Progress events render in the chat panel:
  ```
  ▸ Phase 1: Import particles            ✓ 25,431 particles
  ▸ Phase 2: 2D classification            ✓ 8 classes accepted
  ▸ Phase 3: Ab-initio reconstruction     ✓
  ▸ Phase 4: Homogeneous refinement       ⠋ iter 14/30
  ▸ Phase 5: FSC + sharpening             pending
  ```
- Sketch the FSC curve as ASCII inline (the SSE event carries an svg_b64; Cursor renders alt text).

**Mid-demo cutaway:** flip to biomate.ai/runs/<id> showing the 3D Mol* viewer with the density map rendering in real time. ~5 seconds of pan/rotate.

**Caption overlay:** `25K particles · 8 min · GPU on Batch · $1.10`

### Beat 3 — Payoff (1:15–1:30)

- Refinement completes; FSC 0.143 = 3.2 Å (PASS gate)
- Claude posts the result + a thumbnail of the final volume
- Right-click the result file URL → "Add to context" → start writing the figure-generation Python in the left pane using the now-loaded file

**Caption overlay:** `CryoSPARC + Cursor — refine in chat, plot in editor, all in one flow`

## Voiceover

> "Cursor with BioMate runs real CryoSPARC refinement — 25,000 particles, GPU on AWS Batch, FSC 0.143 at 3.2 Å. The result loads straight into your Python buffer for the figure."
