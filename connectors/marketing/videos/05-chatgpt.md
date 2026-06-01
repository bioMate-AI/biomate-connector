# Demo 5 — ChatGPT × BioMate (IND §2.6.1 narrative)

**Duration target:** 90 seconds
**Surface:** ChatGPT (web, custom GPT)
**Workflow exercised:** Multi-run report synthesis (ADMET + PBPK + clinical) → IND §2.6.1 narrative

## Pre-roll

- [ ] Demo account `demo@biomate.ai` has 3 prior runs in the account:
  - ADMET screen (run-1)
  - PBPK rat→human (run-2)
  - Phase-1 BOIN simulation (run-3)
- [ ] BioMate GPT live at chatgpt.com/g/g-biomate (or in editor preview)
- [ ] Browser zoomed to 110% for capture

## Script

### Beat 1 (0:00–0:10)

ChatGPT, open the BioMate GPT. Type:

```
Pull my recent ADMET, PBPK, and BOIN simulation runs and generate an IND §2.6.1 nonclinical pharmacology narrative.
```

### Beat 2 — Run (0:10–1:15)

ChatGPT actions:
- Calls `recall_memory` → returns 3 run IDs
- Calls `get_run` × 3 → fetches the structured results
- Calls `export_report` with format=ind_2_6_1, run_ids=[run-1, run-2, run-3]
- BioMate's LLM narrative generator (Gemini Flash) writes the §2.6.1 prose
- ChatGPT shows a preview of the rendered DOCX

**On-screen narrative excerpt (showcase this):**

> ## §2.6.1.1 Brief summary
> The investigational compound BMT-DEMO-001 (CAS XXXXX) was evaluated in a
> standard nonclinical pharmacology package comprising in vitro ADMET, PBPK
> simulation, and a Bayesian optimal interval (BOIN) phase-1 dose-finding
> simulation.
>
> ## §2.6.1.2 Primary pharmacodynamics
> ADMET screening (workflow 12722; n=…) demonstrated hERG IC50 >100 μM and
> CYP3A4 inhibition ratio 0.12 (PASS). Lipinski + PAINS filters passed…

**Caption overlay:** `Real IND §2.6.1 narrative — three workflow runs → one regulatory document`

### Beat 3 — Payoff (1:15–1:30)

- ChatGPT posts: *"Generated. Download: `IND_BMT-DEMO-001_2_6_1.docx` (12 pages, FDA-formatted)."*
- Click the file → DOCX opens in Word/Pages
- Scroll through the headings (2.6.1.1 through 2.6.1.5)

**Caption overlay:** `12-page FDA-formatted DOCX, ready for QC review`

## Voiceover

> "ChatGPT with BioMate doesn't just summarize past work — it synthesizes a real IND §2.6.1 nonclinical pharmacology narrative from your runs. Three workflows in, one regulatory submission document out, FDA-formatted."
