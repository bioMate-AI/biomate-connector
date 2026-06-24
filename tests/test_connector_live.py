"""
Live API smoke tests for the connector tool surface.

Issues exactly one real call to each provider with our exported tool schemas
and asserts the model picks `biomate_session` for a representative bio prompt.
This is the final pre-submission check that the descriptions in the manifest
actually steer real frontier models to the right tool.

Skipped silently when the relevant API key env var is absent — CI for
contributors without paid keys stays green.

Run:
    ANTHROPIC_API_KEY=sk-ant-… ANTHROPIC_BASE_URL= \
        PYTHONPATH=. pytest backend/tests/test_connector_live.py -v -s

Important:
  - Keys are read from env, never hardcoded.
  - For the Anthropic case, ANTHROPIC_BASE_URL must be unset (or empty)
    so the SDK hits api.anthropic.com, not the Claude Code harness proxy.

Cost per run: a few hundred input tokens + one tool-call response per model,
well under $0.01 total.
"""

from __future__ import annotations

import os
from typing import Any

import pytest

from mcp import tools_manifest as tm


# A prompt that should unambiguously route to biomate_session for any frontier
# model with our tool descriptions loaded.
SMOKE_PROMPT = (
    "Screen these SMILES for hERG and CYP3A4 liability and let me know which "
    "ones are likely safe: aspirin (CC(=O)Oc1ccccc1C(=O)O), caffeine "
    "(CN1C=NC2=C1C(=O)N(C(=O)N2C)C). Use the BioMate workflow."
)


# ──────────────────────────────────────────────────────────────────────────────
# Anthropic
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY not set",
)
def test_claude_picks_biomate_session():
    """Real call to claude-sonnet-4-6 with our Anthropic tools export.

    Asserts the model's first tool_use block targets biomate_session.
    """
    import anthropic

    # The Claude Code harness sets ANTHROPIC_BASE_URL to its proxy; that proxy
    # uses a different auth scheme. For this test we must hit api.anthropic.com
    # directly using the caller-supplied key. Override at the client level so
    # we don't mutate process env.
    client = anthropic.Anthropic(
        api_key=os.environ["ANTHROPIC_API_KEY"],
        base_url="https://api.anthropic.com",
    )

    tools = tm.to_anthropic()

    resp = client.messages.create(
        model="claude-sonnet-4-5",  # widely-available stable model id
        max_tokens=1024,
        tools=tools,
        messages=[{"role": "user", "content": SMOKE_PROMPT}],
    )

    print(f"\n[anthropic] stop_reason={resp.stop_reason} usage={resp.usage}")
    tool_uses = [b for b in resp.content if getattr(b, "type", None) == "tool_use"]
    text_blocks = [b for b in resp.content if getattr(b, "type", None) == "text"]
    if text_blocks:
        print(f"[anthropic] preamble: {text_blocks[0].text[:200]!r}")
    for tu in tool_uses:
        print(f"[anthropic] tool_use: {tu.name} input={tu.input}")

    assert resp.stop_reason == "tool_use", (
        f"expected tool_use stop, got {resp.stop_reason}; content={resp.content}"
    )
    assert tool_uses, "no tool_use block in response"
    first = tool_uses[0]
    assert first.name == "biomate_session", (
        f"expected biomate_session, got {first.name}. "
        f"All tool calls: {[t.name for t in tool_uses]}"
    )
    assert "goal" in first.input, f"biomate_session called without goal: {first.input}"
    # The goal should mention the actual chemistry context
    goal_lower = str(first.input["goal"]).lower()
    assert any(kw in goal_lower for kw in ("herg", "cyp3a4", "smiles", "screen", "admet")), (
        f"goal didn't reflect user intent: {first.input['goal']!r}"
    )


# Routing test matrix — different user intents must route to different tools.
# This validates the tool descriptions actually distinguish the surface, not
# just that the agentic biomate_session is irresistible for everything.
#
# Coverage: biomate_session is exercised by the smoke test above; the 16 cases
# below cover the remaining 16 tools, so the matrix proves all 17 tools are
# individually reachable from a plain-English prompt. Prompts mirror the L4
# routing table in docs/20260621_TEST_PLAN.md, sharpened slightly so intent is
# unambiguous (each names the primitive operation, not "run a workflow").
ROUTING_CASES = [
    pytest.param(
        "What workflows do you have for CryoSPARC single-particle reconstruction? "
        "Just show me the catalog — don't run anything yet.",
        "search_workflow",
        id="search_workflow",
    ),
    pytest.param(
        "Cancel my BioMate run with id run-abc-123 — I made a mistake in the params.",
        "cancel_run",
        id="cancel_run",
    ),
    pytest.param(
        "I have a UniProt ID P04637. Just look up its basic info — no workflow needed.",
        "query_database",
        id="query_database",
    ),
    pytest.param(
        "For the WGS variant-calling workflow (id 12704), show me the full list of "
        "input parameters and steps it expects. Don't launch it — I just want the spec.",
        "get_workflow_spec",
        id="get_workflow_spec",
    ),
    pytest.param(
        "Launch workflow 12849 with explicit params: my already-uploaded FASTQ at "
        "s3://biomate-demo/sample_R1.fastq.gz as the reads input. Start the run now.",
        "run_workflow",
        id="run_workflow",
    ),
    pytest.param(
        "List the BioMate runs I started in the last week, most recent first.",
        "list_runs",
        id="list_runs",
    ),
    pytest.param(
        "What's the current status and progress of my run run-xyz-789?",
        "get_run",
        id="get_run",
    ),
    pytest.param(
        # preview_file requires an explicit s3_key; give one so the model doesn't
        # need get_run to discover the file path first.
        "Render a preview of the output file at s3_key "
        "'runs/run-xyz-789/deseq2/volcano.png' so I can see what it looks like — "
        "I already have the path, no need to look up the run.",
        "preview_file",
        id="preview_file",
    ),
    pytest.param(
        "Generate a downloadable PDF methods report for run run-xyz-789.",
        "export_report",
        id="export_report",
    ),
    pytest.param(
        # analyze_results' own description says "use after get_run"; presuppose the
        # run is already fetched so the only remaining step is AI interpretation.
        "I've already pulled run run-xyz-789 and I'm looking at its differential-"
        "expression table now — no need to fetch it again. Ask BioMate's AI to "
        "interpret these results: what do they mean biologically, which pathways stand out?",
        "analyze_results",
        id="analyze_results",
    ),
    pytest.param(
        "My run run-xyz-789 failed. Diagnose the error and tell me the root cause and how to fix it.",
        "explain_error",
        id="explain_error",
    ),
    pytest.param(
        "Resolve the accession GSE183947 to the matching workflow and pre-filled params.",
        "resolve_accession",
        id="resolve_accession",
    ),
    pytest.param(
        "List the files in my BioMate S3 workspace so I can see what data I already have.",
        "browse_data",
        id="browse_data",
    ),
    pytest.param(
        "Download the UniProt human proteome FASTA from its public URL into my BioMate S3 workspace.",
        "fetch_public_data",
        id="fetch_public_data",
    ),
    pytest.param(
        "Pull up the results from my previous CRISPR screen runs — I want to reuse what I did before.",
        "recall_memory",
        id="recall_memory",
    ),
    pytest.param(
        "I have a local FASTQ file on my laptop I need to upload before running anything — "
        "give me a way to upload it to BioMate storage.",
        "upload_file",
        id="upload_file",
    ),
]


@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY not set",
)
@pytest.mark.parametrize("prompt,expected_tool", ROUTING_CASES)
def test_claude_routing_distinguishes_tools(prompt: str, expected_tool: str):
    """Different intents must select different primitive tools, not all biomate_session."""
    import anthropic

    client = anthropic.Anthropic(
        api_key=os.environ["ANTHROPIC_API_KEY"],
        base_url="https://api.anthropic.com",
    )

    resp = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=512,
        tools=tm.to_anthropic(),
        messages=[{"role": "user", "content": prompt}],
    )

    print(f"\n[routing:{expected_tool}] stop_reason={resp.stop_reason} usage={resp.usage}")
    tool_uses = [b for b in resp.content if getattr(b, "type", None) == "tool_use"]
    for tu in tool_uses:
        print(f"  tool_use: {tu.name} input={tu.input}")

    assert tool_uses, f"no tool_use for prompt={prompt!r}"
    picked = tool_uses[0].name
    assert picked == expected_tool, (
        f"expected {expected_tool}, model picked {picked} for prompt={prompt!r}"
    )


# ──────────────────────────────────────────────────────────────────────────────
# OpenAI (gpt-4o / gpt-4.1)
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY"),
    reason="OPENAI_API_KEY not set",
)
def test_gpt4o_picks_biomate_session():
    """Real call to gpt-4o with our OpenAI function-tools export."""
    from openai import OpenAI

    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

    tools = tm.to_openai()

    resp = client.chat.completions.create(
        model="gpt-4o",
        tools=tools,
        tool_choice="auto",
        messages=[{"role": "user", "content": SMOKE_PROMPT}],
    )

    msg = resp.choices[0].message
    print(f"\n[openai] finish_reason={resp.choices[0].finish_reason} usage={resp.usage}")
    if msg.content:
        print(f"[openai] preamble: {msg.content[:200]!r}")
    for tc in (msg.tool_calls or []):
        print(f"[openai] tool_call: {tc.function.name} args={tc.function.arguments[:200]}")

    assert msg.tool_calls, "no tool_calls on the response"
    first = msg.tool_calls[0]
    assert first.function.name == "biomate_session", (
        f"expected biomate_session, got {first.function.name}. "
        f"All tool calls: {[t.function.name for t in msg.tool_calls]}"
    )
    import json
    args = json.loads(first.function.arguments)
    assert "goal" in args, f"biomate_session called without goal: {args}"
