"""
BioMate Connector Tools — Canonical Manifest
============================================

Single source of truth for the tools exposed by every connector surface:

  - MCP server         (backend/lib/mcp/biomate_mcp_server.py)
  - Open Claw          (frontend/server/routes.ts  OPEN_CLAW_TOOLS — loads JSON)
  - Slack / WeChat     (proxy through /api/open-claw/stream — inherit auto)
  - ChatGPT GPT Action (manifest generated from to_openai())
  - Claude Skill       (catalog generated from to_skill_catalog())

Schema is defined once in `TOOL_SCHEMAS`. Surface-specific shapes are produced
by the `to_*()` converters at the bottom.

Drift between this file and any surface-specific schema is a bug; the CI drift
test (`tests/test_tools_manifest_drift.py`) regenerates the JSON and fails if
it differs from the committed copy.

Design notes (see docs/20260513_CONNECTOR_ARCHITECTURE_V2.md):

  - Tier 1 — `biomate_session` is the streaming agentic entry point that hosts
    with progress-notification support (MCP) render inline.
  - Tier 2 — workflow primitives. `get_workflow_spec` and `get_run` merge what
    used to be 4 separate calls.
  - Tier 3 — outputs, analysis, reporting. Includes `recall_memory` and
    `upload_file` (signed PUT URL — host uploads directly to S3).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional


Tier = Literal["agentic", "workflow", "analysis"]


@dataclass(frozen=True)
class ToolSchema:
    name: str
    tier: Tier
    description: str
    input_schema: Dict[str, Any]
    # HTTP endpoint on the BioMate backend the MCP server proxies this tool to.
    # Used by dispatch and by the drift test to detect dead routes.
    backend_path: str
    backend_method: Literal["GET", "POST", "DELETE"] = "POST"
    # Streams progress notifications via MCP notifications/progress when true.
    streaming: bool = False

    # ── Directory / MCP tool annotations (required by the Anthropic Claude
    #    Directory submission). `title` is a human-readable display name; the
    #    *_hint booleans are behavioural annotations per the MCP spec's
    #    ToolAnnotations object. Defaults match the MCP spec defaults
    #    (readOnly=false, destructive=false, idempotent=false, openWorld=false).
    title: str = ""
    read_only_hint: bool = False
    destructive_hint: bool = False
    idempotent_hint: bool = False
    open_world_hint: bool = False

    def annotations(self) -> Dict[str, Any]:
        """MCP `ToolAnnotations` object emitted under each tool's `annotations`."""
        return {
            "title": self.title,
            "readOnlyHint": self.read_only_hint,
            "destructiveHint": self.destructive_hint,
            "idempotentHint": self.idempotent_hint,
            "openWorldHint": self.open_world_hint,
        }


# ──────────────────────────────────────────────────────────────────────────────
# Canonical schema definitions — DO NOT duplicate elsewhere.
# ──────────────────────────────────────────────────────────────────────────────

TOOL_SCHEMAS: List[ToolSchema] = [
    # ── Tier 1: agentic / streaming ───────────────────────────────────────────
    ToolSchema(
        name="biomate_session",
        tier="agentic",
        description=(
            "**Primary entry point — use this for 90% of requests.** "
            "Run a complete BioMate scientific session from a natural-language goal. "
            "BioMate selects the right workflow from 2,455 indexed pipelines, "
            "pre-fills parameters from your goal text, executes on BioMate cloud, "
            "handles QC gates with auto-loop remediation, and produces structured findings. "
            "While running, the tool streams real-time progress (phase started, step completed, "
            "QC gate, auto-loop remediation, finding) back to the host. "
            "Returns a final run summary, a deep link to the live results panel, and the report URL. "
            "\n\n"
            "**How to write the `goal` parameter** — plain English, one to three sentences:\n"
            "• Include the *what*: analysis type + subject (e.g. 'ADMET screening', 'RNA-seq DE', 'variant calling')\n"
            "• Include *data location*: inline SMILES/sequences, S3 paths, accession numbers, or upload first with upload_file\n"
            "• Include key *parameters* that matter: organism, library type, comparisons, thresholds\n"
            "• You can omit anything BioMate can infer (it will ask if genuinely ambiguous)\n"
            "\n"
            "**Good examples:**\n"
            "  'Screen aspirin (CC(=O)Oc1ccccc1C(=O)O) and caffeine (Cn1cnc2c1c(=O)n(c(=O)n2C)C) for hERG inhibition, CYP3A4, and oral bioavailability'\n"
            "  'RNA-seq differential expression on s3://lab-bucket/exp42/fastqs/ — human GRCh38, dUTP strand-specific, treated (n=3) vs control (n=3), FDR 0.05'\n"
            "  'Whole-genome variant calling on the uploaded FASTQ pair, GRCh38, GATK HaplotypeCaller, germline mode'\n"
            "  'Run homogeneous 3D refinement in CryoSPARC on s3://cryo/job042/, C2 symmetry, box size 256'\n"
            "  'Fetch GSE183947 from GEO and run the same RNA-seq pipeline'\n"
            "\n"
            "Use run_workflow instead when the user wants to call a specific workflow by ID with explicit parameter control."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "goal": {
                    "type": "string",
                    "description": (
                        "Natural-language scientific goal. Include: analysis type, data location "
                        "(inline SMILES/sequences, s3:// paths, or GEO/SRA accession numbers), "
                        "and key parameters (organism, comparisons, thresholds). "
                        "BioMate infers the rest. "
                        "Examples: "
                        "'Screen these 5 SMILES for hERG IC50 and CYP3A4 inhibition', "
                        "'RNA-seq DE on s3://bucket/exp1/ human GRCh38 paired-end treated vs control', "
                        "'Fetch GSE183947 and run differential expression'."
                    ),
                },
                "inputs": {
                    "type": "object",
                    "description": (
                        "Optional structured inputs (S3 keys, sequences, SMILES, parameter overrides). "
                        "BioMate merges these with what it extracts from `goal`. "
                        "Example: {\"smiles_list\": [\"CC(=O)Oc1ccccc1C(=O)O\"], \"organism\": \"human\"}"
                    ),
                    "additionalProperties": True,
                },
                "experiment_id": {
                    "type": "string",
                    "description": "Optional experiment to attach this run to (from recall_memory).",
                },
                "stream": {
                    "type": "boolean",
                    "description": (
                        "Emit progress notifications during execution. Default true. "
                        "Set false for hosts without MCP notification support — then poll with get_run."
                    ),
                    "default": True,
                },
            },
            "required": ["goal"],
        },
        backend_path="/api/open-claw/stream",
        streaming=True,
        title="Run BioMate Session",
        read_only_hint=False,
        destructive_hint=False,
        idempotent_hint=False,
        open_world_hint=True,
    ),

    # ── Tier 2: workflow primitives ───────────────────────────────────────────
    ToolSchema(
        name="search_workflow",
        tier="workflow",
        description=(
            "Search the BioMate workflow catalog (2,455 indexed workflows across 34 domains) "
            "by natural language. Returns ranked workflow cards with id, name, domain, "
            "one-line description, and estimated BioMate cloud cost. Use this when the user "
            "wants to pick a workflow explicitly; otherwise prefer biomate_session."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Natural language description of the analysis."},
                "limit": {"type": "integer", "description": "Max results (default 5, max 20).", "default": 5},
                "domain": {
                    "type": "string",
                    "description": "Optional domain filter: transcriptomics, genomics, proteomics, drug_discovery, cryo_em, etc.",
                },
            },
            "required": ["query"],
        },
        backend_path="/api/workflows/search",
        title="Search Workflows",
        read_only_hint=True,
        open_world_hint=True,
    ),
    ToolSchema(
        name="get_workflow_spec",
        tier="workflow",
        description=(
            "Return the full specification for a workflow: required + optional parameters "
            "(with types and allowed values), default QC profile and thresholds, expected "
            "input files, estimated cost and runtime, and any license requirements. "
            "Call this before run_workflow when the user wants explicit parameter control."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "workflow_id": {"type": "string", "description": "Workflow ID from search_workflow."},
            },
            "required": ["workflow_id"],
        },
        backend_path="/api/workflows/spec",
        backend_method="GET",
        title="Get Workflow Spec",
        read_only_hint=True,
    ),
    ToolSchema(
        name="run_workflow",
        tier="workflow",
        description=(
            "Execute a specific BioMate workflow on BioMate cloud with explicit parameters. "
            "Returns a run_id immediately. If stream=true, also emits progress notifications "
            "until the run terminates (same events as biomate_session). "
            "Use biomate_session instead when the user gave you a natural-language goal."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "workflow_id": {"type": "string", "description": "Workflow ID from search_workflow."},
                "params": {
                    "type": "object",
                    "description": "Parameter dict. Required params come from get_workflow_spec.",
                    "additionalProperties": True,
                },
                "experiment_id": {"type": "string", "description": "Optional experiment to attach to."},
                "stream": {
                    "type": "boolean",
                    "description": "Emit progress notifications while running. Default false.",
                    "default": False,
                },
            },
            "required": ["workflow_id"],
        },
        backend_path="/api/workflows/execute",
        streaming=True,
        title="Run Workflow",
        read_only_hint=False,
        destructive_hint=False,
        idempotent_hint=False,
        open_world_hint=True,
    ),
    ToolSchema(
        name="watch_run",
        tier="workflow",
        description=(
            "Stream real-time progress for a running BioMate workflow and return full results when done. "
            "Emits MCP notifications/progress for every phase start/complete, step update, and QC gate. "
            "When the run finishes, automatically fetches output files (with download URLs) and AI findings. "
            "Use after run_workflow (non-streaming) to watch a submitted run. "
            "Does not require re-submitting — takes an existing run_id."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "run_id": {"type": "string", "description": "Run ID returned by run_workflow."},
            },
            "required": ["run_id"],
        },
        backend_path="/api/workflows/runs/{run_id}",
        backend_method="GET",
        streaming=True,
    ),
    ToolSchema(
        name="get_run",
        tier="workflow",
        description=(
            "Return everything about a run in one call: status (pending|running|completed|failed), "
            "per-phase and per-step progress with timestamps, output files with download URLs, "
            "structured findings, QC gate results, and any auto-loop remediations applied. "
            "Replaces get_run_status + get_run_results + step-level findings polling."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "run_id": {"type": "string", "description": "Run ID."},
                "include_findings": {
                    "type": "boolean",
                    "description": "Include structured findings cards (default true).",
                    "default": True,
                },
            },
            "required": ["run_id"],
        },
        backend_path="/api/workflows/runs/{run_id}",
        backend_method="GET",
        title="Get Run Details",
        read_only_hint=True,
    ),
    ToolSchema(
        name="cancel_run",
        tier="workflow",
        description="Cancel a running or queued BioMate workflow on BioMate cloud.",
        input_schema={
            "type": "object",
            "properties": {"run_id": {"type": "string", "description": "Run ID to cancel."}},
            "required": ["run_id"],
        },
        backend_path="/api/workflows/runs/{run_id}/cancel",
        title="Cancel Run",
        read_only_hint=False,
        destructive_hint=True,
        idempotent_hint=True,
    ),
    ToolSchema(
        name="list_runs",
        tier="workflow",
        description="List the user's recent runs with status and timestamps. Filter by status or experiment.",
        input_schema={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Max runs to return (default 10).", "default": 10},
                "status": {
                    "type": "string",
                    "enum": ["all", "running", "completed", "failed", "pending"],
                    "default": "all",
                },
                "experiment_id": {"type": "string", "description": "Optional experiment filter."},
            },
        },
        backend_path="/api/workflows/runs",
        backend_method="GET",
        title="List Runs",
        read_only_hint=True,
    ),

    # ── Tier 3: outputs, analysis, reporting ──────────────────────────────────
    ToolSchema(
        name="preview_file",
        tier="analysis",
        description=(
            "Render a server-side preview of an output file (FASTA, VCF, CSV/TSV, image, PDF). "
            "Returns markdown + optional thumbnail PNG. Use this to show the user what an "
            "output looks like without downloading multi-GB files. For input QC of files the "
            "user is about to upload, prefer biomate_session (it runs a real QC workflow)."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "s3_key": {"type": "string", "description": "The full s3_uri value (must start with 's3://') copied verbatim from a get_run output_files entry — e.g. 's3://bucket/path/file.json'. NOT a bare key; the s3:// prefix is required."},
                "run_id": {"type": "string", "description": "Optional run_id for context-aware parsing."},
                "max_rows": {"type": "integer", "description": "For tabular files (default 100).", "default": 100},
            },
            "required": ["s3_key"],
        },
        backend_path="/api/files/preview",
        title="Preview Output File",
        read_only_hint=True,
    ),
    ToolSchema(
        name="export_report",
        tier="analysis",
        description=(
            "Render a publication-ready report for a completed run as PDF or markdown. "
            "Includes the methods section, QC audit trail, structured findings, and figures. "
            "This is what users need for IND submissions, CRO compliance packages, and "
            "publication supplementary materials."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "run_id": {"type": "string", "description": "Completed run ID."},
                "format": {"type": "string", "enum": ["pdf", "markdown", "docx"], "default": "pdf"},
                "sections": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["methods", "qc", "findings", "figures", "appendix"]},
                    "description": "Which sections to include (default: all).",
                },
            },
            "required": ["run_id"],
        },
        backend_path="/api/workflows/runs/{run_id}/findings/report",
        title="Export Report",
        read_only_hint=False,
        destructive_hint=False,
        idempotent_hint=True,
    ),
    ToolSchema(
        name="analyze_results",
        tier="analysis",
        description=(
            "Ask BioMate's AI to interpret a completed run. Returns natural-language analysis: "
            "key findings, quality assessment, scientific interpretation, recommended next steps. "
            "Use after get_run when the user asks 'what does this mean?'."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "run_id": {"type": "string", "description": "Completed run ID."},
                "question": {"type": "string", "description": "Optional focused question."},
            },
            "required": ["run_id"],
        },
        backend_path="/api/workflows/runs/{run_id}/ai/analyze",
        title="Analyze Results",
        read_only_hint=True,
        open_world_hint=True,
    ),
    ToolSchema(
        name="explain_error",
        tier="analysis",
        description=(
            "Diagnose a failed run. Returns the likely root cause (genome mismatch, missing input, "
            "OOM, container pull failure, etc.) and the specific fix. Often the next step is "
            "run_workflow with corrected params."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "run_id": {"type": "string", "description": "Failed run ID."},
                "error_log": {"type": "string", "description": "Optional pasted error log."},
            },
            "required": ["run_id"],
        },
        backend_path="/api/workflows/explain_error",
        title="Explain Run Error",
        read_only_hint=True,
        open_world_hint=True,
    ),
    ToolSchema(
        name="query_database",
        tier="analysis",
        description=(
            "Query a biological/chemical database by accession or name. Supported: "
            "uniprot, pdb, alphafold, ncbi_gene, dbsnp, clinvar, gnomad, kegg, reactome, "
            "chebi, chembl, hpo, string, pubmed."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "database": {
                    "type": "string",
                    "enum": [
                        "uniprot", "pdb", "alphafold", "ncbi_gene",
                        "dbsnp", "clinvar", "gnomad",
                        "kegg", "reactome",
                        "chebi", "chembl",
                        "hpo", "string", "pubmed",
                    ],
                },
                "query": {"type": "string", "description": "Accession, gene symbol, or compound name."},
            },
            "required": ["database", "query"],
        },
        backend_path="/api/databases/query",
        title="Query Biological Database",
        read_only_hint=True,
        open_world_hint=True,
    ),
    ToolSchema(
        name="resolve_accession",
        tier="analysis",
        description=(
            "Identify a public archive accession (GEO, SRA, ENA, DDBJ) and return the "
            "best BioMate workflow to fetch it plus pre-filled params ready for run_workflow. "
            "Examples: GSE183947 → Geo Data Connector; SRR12345 → nf-core/fetchngs. "
            "Call this before run_workflow whenever the user provides an accession instead of a file."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "accession": {
                    "type": "string",
                    "description": (
                        "Public archive accession: GSE*, GSM*, GDS*, SRR*, SRX*, SRP*, "
                        "ERR*, ERX*, ERP*, DRR*, PRJNA*, PRJEB*, E-MTAB-*, E-GEOD-*"
                    ),
                },
            },
            "required": ["accession"],
        },
        backend_path="/api/accession/resolve",
        title="Resolve Accession",
        read_only_hint=True,
        open_world_hint=True,
    ),
    ToolSchema(
        name="browse_data",
        tier="analysis",
        description=(
            "Browse a biological data repository or S3 workspace by listing files and directories "
            "at a given path. Public sources (EBI, NCBI, Ensembl, UCSC) require a path. "
            "S3 sources (biomate_workspace, user_s3) accept an optional prefix. "
            "Use before fetch_public_data to navigate to the exact file you need."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "source_id": {
                    "type": "string",
                    "enum": [
                        "ebi_ftp", "ncbi_ftp", "ensembl_ftp", "ucsc_downloads",
                        "http_public", "biomate_workspace", "user_s3",
                    ],
                    "description": (
                        "Data source to browse. "
                        "biomate_workspace = BioMate's S3 work bucket; "
                        "user_s3 = user's own S3 bucket (if configured)."
                    ),
                },
                "path": {
                    "type": "string",
                    "description": "Directory path or S3 prefix to list (e.g. '/pub/databases/uniprot/' or 'results/run-xyz/').",
                },
            },
            "required": ["source_id"],
        },
        backend_path="/api/data/browse",
        title="Browse Data Repository",
        read_only_hint=True,
        open_world_hint=True,
    ),
    ToolSchema(
        name="fetch_public_data",
        tier="analysis",
        description=(
            "Download a file from a public biological repository (EBI, NCBI, Ensembl, UCSC) "
            "into BioMate's S3 workspace and return a presigned URL and S3 URI. "
            "Use the S3 URI as a workflow input parameter. Call browse_data first to find the exact path."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "source_id": {
                    "type": "string",
                    "enum": ["ebi_ftp", "ncbi_ftp", "ensembl_ftp", "ucsc_downloads", "http_public"],
                    "description": "Data source the file comes from.",
                },
                "remote_path": {
                    "type": "string",
                    "description": "Path on the FTP server (e.g. '/pub/databases/uniprot/.../uniprot_sprot.fasta.gz').",
                },
                "url": {
                    "type": "string",
                    "description": "For http_public source: full HTTPS URL of the file to download.",
                },
            },
            "required": ["source_id"],
        },
        backend_path="/api/data/fetch",
        title="Fetch Public Data",
        read_only_hint=False,
        destructive_hint=False,
        idempotent_hint=True,
        open_world_hint=True,
    ),
    ToolSchema(
        name="recall_memory",
        tier="analysis",
        description=(
            "Retrieve relevant prior context for the current goal: past runs on similar inputs, "
            "validated procedures, findings tagged by the user, learned parameter preferences. "
            "Call before biomate_session for repeat users — it dramatically improves param "
            "auto-fill and avoids re-running work."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to recall — usually the user's goal."},
                "scope": {
                    "type": "string",
                    "enum": ["runs", "findings", "procedures", "all"],
                    "default": "all",
                },
                "limit": {"type": "integer", "default": 5},
            },
            "required": ["query"],
        },
        backend_path="/api/memory/relevant",
        title="Recall Memory",
        read_only_hint=True,
    ),
    ToolSchema(
        name="upload_file",
        tier="analysis",
        description=(
            "Get a one-shot signed S3 PUT URL so the host can upload a local file directly to "
            "BioMate's data plane without proxying bytes through the chat transport. Returns "
            "the s3_key to use in subsequent tool calls. For files >5MB this is mandatory; for "
            "small text payloads, inline strings are fine."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "Original filename (sets content-type and extension)."},
                "size_bytes": {"type": "integer", "description": "File size for quota check."},
                "content_type": {"type": "string", "description": "MIME type (auto-detected from filename if omitted)."},
            },
            "required": ["filename"],
        },
        backend_path="/api/uploads/signed_url",
        title="Upload File",
        read_only_hint=False,
        destructive_hint=False,
        idempotent_hint=False,
    ),
]


# ──────────────────────────────────────────────────────────────────────────────
# Lite tool set — 3 tools for consumer surfaces (Claude.ai, ChatGPT GPT, Slack)
# ──────────────────────────────────────────────────────────────────────────────
# Consumer surfaces (Claude.ai skills, ChatGPT GPTs, Slack bots) have context
# constraints and users who expect simplicity. Presenting 17 tools overwhelms
# the AI model's tool-selection heuristics and confuses end users.
#
# The lite set covers 90% of use cases:
#   biomate_session  — run any analysis (the only tool most users ever need)
#   upload_file      — get a presigned URL to upload a local file first
#   export_report    — download findings as PDF/DOCX after a run completes
#
# The full 17-tool set is available for power users who configure MCP directly
# (Claude Desktop, Cursor, Codex) or use the BioMate API programmatically.

LITE_TOOL_NAMES: set = {"biomate_session", "upload_file", "export_report"}

_LITE_SCHEMAS = [t for t in TOOL_SCHEMAS if t.name in LITE_TOOL_NAMES]


# ──────────────────────────────────────────────────────────────────────────────
# Server-level metadata — required by the Anthropic Claude Directory listing
# (privacy policy is mandatory; support + terms + docs are strongly recommended).
# Keep in sync with the OpenAPI `info` block in to_openapi() and README.md.
# ──────────────────────────────────────────────────────────────────────────────

SERVER_METADATA: Dict[str, str] = {
    "name": "BioMate",
    "vendor": "BioMate AI",
    "documentation_url": "https://biomate.ai/connectors",
    "privacy_policy_url": "https://biomate.ai/legal/privacy",
    "terms_of_service_url": "https://biomate.ai/legal/terms",
    "support_email": "support@biomate.ai",
    "support_url": "https://biomate.ai/support",
}


def to_server_info() -> Dict[str, str]:
    """Server-level listing metadata (privacy, terms, support, docs)."""
    return dict(SERVER_METADATA)


# ──────────────────────────────────────────────────────────────────────────────
# Surface-specific exporters
# ──────────────────────────────────────────────────────────────────────────────


def to_mcp() -> List[Dict[str, Any]]:
    """MCP `tools/list` shape. Uses `inputSchema` (camelCase, per MCP spec).

    Emits the top-level `title` display name and the `annotations`
    (ToolAnnotations) object required by the Anthropic Claude Directory.
    """
    return [
        {
            "name": t.name,
            "title": t.title,
            "description": t.description,
            "inputSchema": t.input_schema,
            "annotations": t.annotations(),
        }
        for t in TOOL_SCHEMAS
    ]


def to_anthropic() -> List[Dict[str, Any]]:
    """Anthropic Messages API / Open Claw shape. Uses `input_schema` (snake_case)."""
    return [
        {
            "name": t.name,
            "description": t.description,
            "input_schema": t.input_schema,
        }
        for t in TOOL_SCHEMAS
    ]


def to_openai() -> List[Dict[str, Any]]:
    """OpenAI function-calling / ChatGPT GPT Actions shape."""
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.input_schema,
            },
        }
        for t in TOOL_SCHEMAS
    ]


# ── Lite variants (3 tools) ───────────────────────────────────────────────────

def to_lite_mcp() -> List[Dict[str, Any]]:
    """Lite MCP tool list (3 tools) for consumer surfaces."""
    return [
        {
            "name": t.name,
            "title": t.title,
            "description": t.description,
            "inputSchema": t.input_schema,
            "annotations": t.annotations(),
        }
        for t in _LITE_SCHEMAS
    ]


def to_lite_anthropic() -> List[Dict[str, Any]]:
    """Lite Anthropic tool list (3 tools) for Claude.ai skills and API lite usage."""
    return [
        {"name": t.name, "description": t.description, "input_schema": t.input_schema}
        for t in _LITE_SCHEMAS
    ]


def to_lite_openai() -> List[Dict[str, Any]]:
    """Lite OpenAI tool list (3 tools) for ChatGPT GPTs and simple API usage."""
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.input_schema,
            },
        }
        for t in _LITE_SCHEMAS
    ]


def to_openapi() -> Dict[str, Any]:
    """OpenAPI 3.1 spec consumed by ChatGPT GPT Actions and any HTTP client.

    Each tool is exposed as `POST /tools/{tool_name}` with the input_schema
    as the JSON request body. Responses share a common envelope. Auth is
    OAuth 2.0 with the same /oauth/authorize and /oauth/token endpoints used
    by the MCP and Skill credential flows.

    Note: ChatGPT GPT Actions cap operations at ~30 per spec — we ship 14 so
    we are well under the limit.
    """
    paths: Dict[str, Any] = {}
    for t in TOOL_SCHEMAS:
        op_id = t.name
        paths[f"/tools/{t.name}"] = {
            "post": {
                "operationId": op_id,
                "summary": t.description.split(".")[0].strip() + ".",
                "description": t.description,
                "x-streaming": t.streaming,
                "requestBody": {
                    "required": bool(t.input_schema.get("required")),
                    "content": {"application/json": {"schema": t.input_schema}},
                },
                "responses": {
                    "200": {
                        "description": "Tool result",
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ToolResult"}}},
                    },
                    "401": {"description": "Unauthorized — reauthorize via /oauth/authorize"},
                    "402": {"description": "Quota or billing gate triggered — see billing.biomate.ai"},
                    "429": {"description": "Rate limited — retry after the Retry-After header"},
                    "500": {"description": "Internal error"},
                },
                "security": [{"BiomateOAuth": ["runs:read", "runs:write", "workflows:search"]}],
            }
        }

    return {
        "openapi": "3.1.0",
        "info": {
            "title": "BioMate Connector API",
            "description": (
                "Run bioinformatics, drug-discovery, and clinical workflows on BioMate's "
                "BioMate cloud infrastructure. Search 2,455 indexed workflows, execute on real "
                "compute, stream live progress, and retrieve structured findings + reports. "
                "Schema is generated from backend/lib/mcp/tools_manifest.py."
            ),
            "version": "2.0.0",
            "contact": {"name": "BioMate Support", "email": "support@biomate.ai", "url": "https://biomate.ai/support"},
            "x-privacy-policy": "https://biomate.ai/legal/privacy",
            "x-terms-of-service": "https://biomate.ai/legal/terms",
        },
        "servers": [{"url": "https://api.biomate.ai", "description": "Production"}],
        "paths": paths,
        "components": {
            "schemas": {
                "ToolResult": {
                    "type": "object",
                    "description": (
                        "Common envelope returned by every tool. `content[].text` carries the "
                        "structured JSON result (typed per tool). For streaming tools, the host "
                        "should also subscribe to /events/{run_id} for live progress."
                    ),
                    "properties": {
                        "content": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "type": {"type": "string", "enum": ["text", "image"]},
                                    "text": {"type": "string"},
                                },
                                "required": ["type"],
                            },
                        },
                        "isError": {"type": "boolean"},
                        "run_id": {"type": "string"},
                        "view_url": {"type": "string", "format": "uri"},
                    },
                    "required": ["content", "isError"],
                },
            },
            "securitySchemes": {
                "BiomateOAuth": {
                    "type": "oauth2",
                    "flows": {
                        "authorizationCode": {
                            "authorizationUrl": "https://biomate.ai/oauth/authorize",
                            "tokenUrl": "https://biomate.ai/oauth/token",
                            "scopes": {
                                "runs:read": "Read run status and outputs",
                                "runs:write": "Launch and cancel runs",
                                "workflows:search": "Search the workflow catalog",
                                "memory:read": "Read prior experiment context",
                                "memory:write": "Record findings and procedures",
                                "billing:read": "Read usage and quota",
                            },
                        }
                    },
                },
            },
        },
    }


def to_skill_catalog() -> str:
    """Markdown catalog for the Claude Skill bundle's references/."""
    by_tier: Dict[str, List[ToolSchema]] = {"agentic": [], "workflow": [], "analysis": []}
    for t in TOOL_SCHEMAS:
        by_tier[t.tier].append(t)

    lines: List[str] = ["# BioMate Tool Catalog", ""]
    titles = {
        "agentic": "## Tier 1 — Streaming agentic",
        "workflow": "## Tier 2 — Workflow primitives",
        "analysis": "## Tier 3 — Analysis, reporting, memory",
    }
    for tier_key, title in titles.items():
        lines.append(title)
        lines.append("")
        for t in by_tier[tier_key]:
            req = ", ".join(t.input_schema.get("required", [])) or "—"
            stream = " · **streams**" if t.streaming else ""
            lines.append(f"### `{t.name}`{stream}")
            lines.append("")
            lines.append(t.description)
            lines.append("")
            lines.append(f"**Required:** `{req}`")
            lines.append("")
    return "\n".join(lines)


def build_manifest_json(output_path: Optional[Path] = None) -> Path:
    """Write the consolidated manifest JSON consumed by the Node.js side."""
    if output_path is None:
        output_path = Path(__file__).parent / "tools_manifest.json"

    payload = {
        "version": "2.0.0",
        "generated_from": "mcp/tools_manifest.py",
        # Server-level listing metadata (privacy policy, terms, support, docs).
        "server": to_server_info(),
        # Full 17-tool set (MCP Desktop, Cursor, Codex, programmatic API)
        "mcp": to_mcp(),
        "anthropic": to_anthropic(),
        "openai": to_openai(),
        # Lite 3-tool set (Claude.ai skills, ChatGPT GPTs, Slack/WeChat bots)
        "lite": {
            "mcp": to_lite_mcp(),
            "anthropic": to_lite_anthropic(),
            "openai": to_lite_openai(),
            "tool_names": sorted(LITE_TOOL_NAMES),
        },
        "backend_routes": [
            {
                "name": t.name,
                "method": t.backend_method,
                "path": t.backend_path,
                "streaming": t.streaming,
            }
            for t in TOOL_SCHEMAS
        ],
    }
    output_path.write_text(json.dumps(payload, indent=2) + "\n")

    # Also write the standalone OpenAPI spec consumed by ChatGPT GPT Actions.
    # output_path is mcp/tools_manifest.json; repo root is one level up.
    openapi_path = output_path.parent.parent / "connectors" / "chatgpt" / "openapi.json"
    openapi_path.parent.mkdir(parents=True, exist_ok=True)
    openapi_path.write_text(json.dumps(to_openapi(), indent=2) + "\n")
    return output_path


def get_tool(name: str) -> Optional[ToolSchema]:
    for t in TOOL_SCHEMAS:
        if t.name == name:
            return t
    return None


if __name__ == "__main__":
    # CLI: `python -m mcp.tools_manifest` regenerates the JSON.
    out = build_manifest_json()
    print(f"Wrote {len(TOOL_SCHEMAS)} tools → {out}")
