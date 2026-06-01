"""
Redirect: the canonical BioMate MCP server is at backend/mcp/biomate_mcp_server.py (v1.1.0).

That server has 8 tools: chat, search_workflows, get_workflow_params, list_runs,
get_run_status, get_output_files, upload_file, research_query, rerun_workflow.

This shim forwards `python -m backend.lib.mcp` to the real server so the documented
entry point keeps working.
"""
import subprocess
import sys
from pathlib import Path

real_server = Path(__file__).resolve().parents[3] / "mcp" / "biomate_mcp_server.py"

if not real_server.exists():
    print(f"[biomate-mcp] ERROR: real server not found at {real_server}", file=sys.stderr)
    print(f"[biomate-mcp] Expected: {real_server}", file=sys.stderr)
    sys.exit(1)

subprocess.run(
    [sys.executable, str(real_server)],
    stdin=sys.stdin,
    stdout=sys.stdout,
    stderr=sys.stderr,
)
