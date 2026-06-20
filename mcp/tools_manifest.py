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


# ──────────────────────────────────────────────────────────────────────────────
# Canonical schema definitions — DO NOT duplicate elsewhere.
# ──────────────────────────────────────────────────────────────────────────────

TOOL_SCHEMAS: List[ToolSchema] = [
    # ── Tier 1: agentic / streaming ───────────────────────────────────────────
    ToolSchema(
        name="biomate_session",
        tier="agentic",
        description=(
            "Run a complete BioMate scientific session from a natural-language goal. "
            "BioMate picks the workflow, fills parameters from context, runs on BioMate cloud, "
            "handles QC gates with auto-loop remediation, and produces findings. "
            "While running, the tool streams progress events (phase started, step completed, "
            "QC gate fired, auto-loop remediation, finding) to the host so the user sees "
            "real-time updates the same way BioMate's web panel does. "
            "Returns the final run summary, a deep link to the live panel, and the report URL."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "goal": {
                    "type": "string",
                    "description": (
                        "Natural language description of what to do. Examples: "
                        "'screen these 12 SMILES for hERG and CYP3A4 liability', "
                        "'run RNA-seq pipeline on FASTQ files in s3://bucket/exp42/, human, paired-end', "
                        "'predict the structure of P04637 and the top 5 destabilizing mutations'."
                    ),
                },
                "inputs": {
                    "type": "object",
                    "description": (
                        "Optional structured inputs (S3 keys, sequences, SMILES, parameter overrides). "
                        "BioMate will merge these with whatever it extracts from `goal`."
                    ),
                    "additionalProperties": True,
                },
                "experiment_id": {
                    "type": "string",
                    "description": "Optional experiment to attach this run to (from recall_memory or create_experiment).",
                },
                "stream": {
                    "type": "boolean",
                    "description": (
                        "Emit progress notifications during execution. Default true. "
                        "Set false for hosts without notification support (then poll get_run)."
                    ),
                    "default": True,
                },
            },
            "required": ["goal"],
        },
        backend_path="/api/open-claw/stream",
        streaming=True,
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
                "s3_key": {"type": "string", "description": "S3 key (s3://bucket/path) from get_run output_files."},
                "run_id": {"type": "string", "description": "Optional run_id for context-aware parsing."},
                "max_rows": {"type": "integer", "description": "For tabular files (default 100).", "default": 100},
            },
            "required": ["s3_key"],
        },
        backend_path="/api/files/preview",
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
    ),
    ToolSchema(
        name="browse_data",
        tier="analysis",
        description=(
            "Browse a public biological data repository (EBI, NCBI, Ensembl, UCSC) by listing "
            "files and directories at a given path. Use before fetch_public_data to navigate to "
            "the exact file you need (reference genomes, annotation GTFs, VCF datasets, etc.)."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "source_id": {
                    "type": "string",
                    "enum": ["ebi_ftp", "ncbi_ftp", "ensembl_ftp", "ucsc_downloads", "http_public"],
                    "description": "Data source to browse.",
                },
                "path": {
                    "type": "string",
                    "description": "Directory path to list (e.g. '/pub/databases/uniprot/current_release/').",
                },
            },
            "required": ["source_id", "path"],
        },
        backend_path="/api/data/browse",
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
    ),
]


# ──────────────────────────────────────────────────────────────────────────────
# Surface-specific exporters
# ──────────────────────────────────────────────────────────────────────────────


def to_mcp() -> List[Dict[str, Any]]:
    """MCP `tools/list` shape. Uses `inputSchema` (camelCase, per MCP spec)."""
    return [
        {
            "name": t.name,
            "description": t.description,
            "inputSchema": t.input_schema,
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
        "mcp": to_mcp(),
        "anthropic": to_anthropic(),
        "openai": to_openai(),
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
