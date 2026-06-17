# Manifest drift tests

**File:** `backend/tests/test_tools_manifest.py` + `backend/tests/test_connector_sandbox.py`
**Layer:** L2 (integration) + L3 (sandbox — offline SDK validation)
**Cases:** pre-existing suite (35 sandbox cases + manifest assertions)
**Status:** ✓ green at last full run

## Purpose

The connector architecture has **one source of truth** for the 14-tool MCP
surface: `backend/lib/mcp/tools_manifest.py`. Five downstream artifacts must
generate from this file without drift:

1. `backend/lib/mcp/tools_manifest.json` (cached JSON export)
2. MCP server's runtime `TOOLS` dict in `biomate_mcp_server.py`
3. Node.js `OPEN_CLAW_TOOLS` in `frontend/server/routes.ts`
4. ChatGPT Action OpenAPI 3.1 spec in `connectors/chatgpt/openapi.json`
5. Claude Skill tool catalog in `skills/biomate/references/tool_catalog.md`

The drift test **fails the CI build** if any of these diverge.

## What it covers (existing)

| # | Assertion | Source |
|---|---|---|
| 1 | `tools_manifest.json` matches `tools_manifest.py.export()` | `test_tools_manifest.py::test_manifest_json_matches_python` |
| 2 | Every tool in `TOOLS` (MCP server) has a manifest entry | `test_manifest_covers_mcp_tools` |
| 3 | Every manifest tool has a name + description + input schema | `test_all_tools_well_formed` |
| 4 | Anthropic SDK accepts the manifest as `tools=[...]` | sandbox suite |
| 5 | OpenAI SDK accepts the manifest as `tools=[...]` | sandbox suite |
| 6 | OpenAPI 3.1 schema validator passes on `chatgpt/openapi.json` | sandbox suite |
| 7 | JSON Schema Draft 2020-12 validator passes on each tool's `input_schema` | sandbox suite |
| 8 | All tool names are lowercase_snake_case | sandbox |
| 9 | No tool name collisions | sandbox |
| 10 | Each tool with `stream=true` capability has a `progress_token` parameter | sandbox |
| ... | (25 more checks) | see `docs/20260513_CONNECTOR_SANDBOX_RESULTS.md` |

## What's still TO WRITE (TEST_PLAN §3.3)

| # | Scenario | Why it matters |
|---|---|---|
| §3.3.1 | `connectors/chatgpt/openapi.json` operations match manifest tools | Catches the case where a tool is added to Python but the OpenAPI export isn't regenerated |
| §3.3.2 | `skills/biomate/references/tool_catalog.md` describes all 14 tools | Catches stale Skill bundle published to Anthropic gallery |
| §3.3.3 | `OPEN_CLAW_TOOLS` in `frontend/server/routes.ts` has same set | Catches Node.js side missing a tool |

These three additional cases should be appended to `test_tools_manifest.py`. Stub:

```python
def test_chatgpt_openapi_matches_manifest():
    import json
    from mcp.tools_manifest import all_tools
    with open("connectors/chatgpt/openapi.json") as f:
        spec = json.load(f)
    op_ids = {op["operationId"] for path in spec["paths"].values() for op in path.values() if "operationId" in op}
    manifest_names = {t.name for t in all_tools()}
    missing_in_openapi = manifest_names - op_ids
    assert not missing_in_openapi, f"Tools missing from ChatGPT OpenAPI: {missing_in_openapi}"

def test_skill_tool_catalog_lists_all_tools():
    from mcp.tools_manifest import all_tools
    with open("skills/biomate/references/tool_catalog.md") as f:
        catalog = f.read()
    for tool in all_tools():
        assert f"`{tool.name}`" in catalog, f"Tool {tool.name} not documented in Skill catalog"

def test_open_claw_tools_matches_manifest():
    """Greps frontend/server/routes.ts for OPEN_CLAW_TOOLS entries."""
    import re
    from mcp.tools_manifest import all_tools
    with open("frontend/server/routes.ts") as f:
        routes = f.read()
    open_claw = re.search(r"OPEN_CLAW_TOOLS\s*=\s*\[(.*?)\];", routes, re.DOTALL)
    assert open_claw, "OPEN_CLAW_TOOLS array not found"
    for tool in all_tools():
        assert f'name: "{tool.name}"' in open_claw.group(1) or f"name: '{tool.name}'" in open_claw.group(1), \
            f"Tool {tool.name} missing from OPEN_CLAW_TOOLS"
```

These three new tests are **TODO before GA** — track as part of the pre-launch checklist.

## How to run

```bash
cd /home/yzhang/biomate_worktrees/connectors_v2

# Manifest drift (existing)
PYTHONPATH=backend/lib python -m pytest backend/tests/test_tools_manifest.py -v

# Sandbox (35 cases — offline SDK validation)
PYTHONPATH=backend/lib python -m pytest backend/tests/test_connector_sandbox.py -v
```

## Fixtures

- No env vars
- Reads from `backend/lib/mcp/tools_manifest.py` (canonical) and exported artifacts on disk
- Loads anthropic/openai SDK schemas — no network calls
- Uses `jsonschema` for Draft 2020-12 validation

## When it fails

| Failure | Likely cause | Fix |
|---|---|---|
| `test_manifest_json_matches_python` | Someone edited `tools_manifest.py` without running `scripts/regen_tools_manifest.sh` | Run the regen script, commit the JSON diff |
| `test_manifest_covers_mcp_tools` | A new tool was added to `TOOLS` dict but not to the manifest | Add to manifest first; MCP server reads from manifest |
| OpenAPI schema validator fails | Manual edit to `chatgpt/openapi.json` introduced an invalid schema | Regenerate from manifest, don't hand-edit |
| `test_chatgpt_openapi_matches_manifest` (when added) | New tool not exposed to ChatGPT yet | Add `operationId` for it in the OpenAPI generator |
| Sandbox `additionalProperties` test | A tool schema has `additionalProperties=true` which OpenAI rejects | Add `additionalProperties=false` to that tool's schema |
