# Live API tool routing tests

**File:** `backend/tests/test_connector_live.py`
**Layer:** L4 (live LLM API)
**Cases:** 4 currently passing + 10 specified, not yet committed
**Status:** ✓ 4/4 Anthropic cases pass (OpenAI requires key, skipped cleanly)

## Purpose

Validates that real Claude and GPT-4o models, given the 14-tool BioMate
manifest as `tools=[...]`, **pick the intended tool** for representative
prompts. This is the only test layer that catches subtle problems in tool
descriptions — e.g. "biomate_session" being too generic and shadowing
"search_workflow" for every prompt.

Skips cleanly when `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` are absent so
contributors and CI without paid keys aren't blocked.

## What it covers (currently committed)

| # | Prompt | Expected tool | Expected key arg |
|---|---|---|---|
| 1 | "Screen these SMILES for hERG and CYP3A4…" | `biomate_session` | `inputs.compounds=[aspirin, caffeine]`, `stream=true` |
| 2 | "What workflows do you have for CryoSPARC?" | `search_workflow` | `domain=cryo_em` |
| 3 | "Cancel my run with id run-abc-123" | `cancel_run` | `run_id=run-abc-123` |
| 4 | "Look up UniProt P04637" | `query_database` | `database=uniprot` |

Cost per full run: ~$0.04 (12k input + 550 output tokens).

## What's specified but not yet committed (TEST_PLAN §5)

10 additional cases, one per remaining tool, defined in `TEST_PLAN.md §5`:

| # | Prompt | Expected tool |
|---|---|---|
| 5 | "Show me what params WGS variant-calling pipeline needs" | `get_workflow_spec` |
| 6 | "Run workflow 12849 with stream=true" | `run_workflow` |
| 7 | "List my runs from last week" | `list_runs` |
| 8 | "What's the status of run-xyz?" | `get_run` |
| 9 | "Show me the volcano plot from run-xyz" | `preview_file` |
| 10 | "Generate a methods report for run-xyz" | `export_report` |
| 11 | "What do these DE results mean?" | `analyze_results` |
| 12 | "My run failed — explain the error" | `explain_error` |
| 13 | "Pull my prior CRISPR screens" | `recall_memory` |
| 14 | "I have a local FASTQ to upload" | `upload_file` |

**These are TODO before GA**, but can't be authored without spending ~$0.10
on iterations to confirm the model actually picks the intended tool — some
prompts may be ambiguous and require description tightening. Track as part
of the pre-launch checklist.

## How to run

```bash
cd /home/yzhang/biomate_worktrees/connectors_v2
ANTHROPIC_API_KEY=sk-ant-... OPENAI_API_KEY=sk-... \
  PYTHONPATH=backend/lib \
  python -m pytest backend/tests/test_connector_live.py -v
```

Without keys: tests skip with `SkipTest: ANTHROPIC_API_KEY not set` — clean
pass.

## Fixtures

- Anthropic SDK with explicit `base_url="https://api.anthropic.com"` to
  bypass any harness proxy in `ANTHROPIC_BASE_URL`
- OpenAI SDK with default base URL
- Both clients use `tools=manifest_to_anthropic()` / `manifest_to_openai()`
  helpers in `backend/lib/mcp/connector_adapters.py`
- Models tested: `claude-sonnet-4-5`, `gpt-4o`
- No real BioMate backend hit — these only assert which tool the LLM
  selects, not that the tool actually runs

## When it fails

| Failure | Likely cause | Fix |
|---|---|---|
| Tool not selected | LLM picked a different tool — likely description ambiguity | Tighten the description in `tools_manifest.py`. Don't change the test. |
| Tool selected but args wrong | LLM extracted a different field name | Either rename the arg in the manifest (if confusing) or tighten the prompt |
| Anthropic call returns 401 | `ANTHROPIC_API_KEY` invalid or expired | Re-generate at console.anthropic.com |
| Anthropic call returns 529 | Anthropic API overloaded | Re-run; ephemeral |
| OpenAI call returns 429 | Rate limited | Wait, re-run; or use a different tier |
| Test asserts model name doesn't exist | `claude-sonnet-4-5` deprecated | Update to current Sonnet model — check `backend/config/llm_providers.yml` |

## Ceiling on these tests

LLMs are nondeterministic. Even with `temperature=0`, a sufficiently
ambiguous prompt can flip ~5% of the time between two near-equivalent
tools. **Don't add cases at the borderline of ambiguity.** Each test
should have an unambiguous answer; if you can't write a prompt where
the right tool is obvious, the tool descriptions themselves need work.

Acceptable tolerance: 13/14 cases pass on any given run (one flake/run).
Below that, treat as a regression and investigate.
