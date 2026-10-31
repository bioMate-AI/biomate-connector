"""Local-only (instrument) tool policy and tool scopes on the remote MCP endpoint.

Drives the real low-level MCP server's tools/list and tools/call handlers.
"""

from __future__ import annotations

import remote_mcp.bootstrap  # noqa: F401  (must precede the MCP SDK import)

import anyio
import mcp.types as types
import pytest

import remote_mcp.server as server_mod
import tools_manifest
from remote_mcp.identity import clear_identity, set_identity
from remote_mcp.local_tools import check_instrument_host, grantable_scopes, local_only_tool_names

INSTRUMENT_TOOLS = {"list_instruments", "get_instrument_status", "run_instrument_protocol"}
PROTOCOL = "metadata = {'apiLevel': '2.13'}\ndef run(ctx):\n    pass\n"


@pytest.fixture()
def env(monkeypatch):
    monkeypatch.setenv("BIOMATE_MCP_TOOLS", "*")
    monkeypatch.delenv("BIOMATE_MCP_ALLOW_LOCAL_TOOLS", raising=False)
    monkeypatch.delenv("BIOMATE_MCP_INSTRUMENT_HOSTS", raising=False)
    monkeypatch.setattr(server_mod, "resolve_api_key", lambda user_id: "bm_live_test")
    yield monkeypatch
    clear_identity()


def _list(srv) -> set[str]:
    async def go():
        res = await srv.request_handlers[types.ListToolsRequest](
            types.ListToolsRequest(method="tools/list")
        )
        return {t.name for t in res.root.tools}
    return anyio.run(go)


def _call(srv, name, arguments, scopes):
    set_identity(user_id="user-1", scopes=frozenset(scopes))

    async def go():
        res = await srv.request_handlers[types.CallToolRequest](
            types.CallToolRequest(
                method="tools/call", params=types.CallToolRequestParams(name=name, arguments=arguments)
            )
        )
        return res.root
    try:
        return anyio.run(go)
    finally:
        clear_identity()


def _text(result) -> str:
    return " ".join(getattr(c, "text", "") for c in result.content)


def test_manifest_local_only_set_is_the_instrument_tools():
    assert local_only_tool_names() == INSTRUMENT_TOOLS


def test_every_manifest_tool_has_a_scope():
    missing = {t.name for t in tools_manifest.TOOL_SCHEMAS} - set(server_mod._TOOL_SCOPES)
    assert not missing


def test_local_tools_hidden_by_default_even_with_star(env):
    assert not (server_mod.enabled_tool_names() & INSTRUMENT_TOOLS)
    listed = _list(server_mod.build_server())
    assert "search_workflow" in listed and not (listed & INSTRUMENT_TOOLS)


def test_explicitly_listed_local_tool_still_hidden_and_uncallable(env):
    env.setenv("BIOMATE_MCP_TOOLS", "search_workflow,run_instrument_protocol")
    srv = server_mod.build_server()
    assert _list(srv) == {"search_workflow"}
    res = _call(srv, "run_instrument_protocol",
                {"robot_ip": "10.0.0.5", "protocol_code": PROTOCOL},
                {"instruments:control"})
    assert res.isError and "not enabled" in _text(res)


def test_onprem_flag_exposes_local_tools(env):
    env.setenv("BIOMATE_MCP_ALLOW_LOCAL_TOOLS", "1")
    assert INSTRUMENT_TOOLS <= _list(server_mod.build_server())


def test_instrument_tools_require_instruments_scope(env):
    env.setenv("BIOMATE_MCP_ALLOW_LOCAL_TOOLS", "1")
    env.setenv("BIOMATE_MCP_INSTRUMENT_HOSTS", "10.20.0.0/24")
    res = _call(server_mod.build_server(), "run_instrument_protocol",
                {"robot_ip": "10.20.0.7", "protocol_code": PROTOCOL},
                {"runs:read", "runs:write"})
    assert res.isError and "insufficient_scope" in _text(res)


def test_allowlisted_host_dry_run_succeeds(env):
    env.setenv("BIOMATE_MCP_ALLOW_LOCAL_TOOLS", "1")
    env.setenv("BIOMATE_MCP_INSTRUMENT_HOSTS", "10.20.0.0/24, ot2-bay3.lab")
    srv = server_mod.build_server()
    for host in ("10.20.0.7", "OT2-bay3.lab"):
        res = _call(srv, "run_instrument_protocol",
                    {"robot_ip": host, "protocol_code": PROTOCOL}, {"instruments:control"})
        assert not res.isError, _text(res)
        assert '"dry_run": true' in _text(res)


@pytest.mark.parametrize("host", [
    "10.20.1.7",                       # outside the CIDR
    "169.254.169.254",                 # cloud metadata
    "169.254.169.254/latest/meta-data#",
    "10.20.0.7:80",
    "user@10.20.0.7",
    "evil.example",
    "",
])
def test_non_allowlisted_or_malformed_host_rejected(env, host):
    env.setenv("BIOMATE_MCP_ALLOW_LOCAL_TOOLS", "1")
    env.setenv("BIOMATE_MCP_INSTRUMENT_HOSTS", "10.20.0.0/24")
    res = _call(server_mod.build_server(), "run_instrument_protocol",
                {"robot_ip": host, "protocol_code": PROTOCOL}, {"instruments:control"})
    assert res.isError
    assert "instrument host" in _text(res)


def test_list_instruments_ips_checked(env):
    env.setenv("BIOMATE_MCP_ALLOW_LOCAL_TOOLS", "1")
    env.setenv("BIOMATE_MCP_INSTRUMENT_HOSTS", "10.20.0.0/24")
    res = _call(server_mod.build_server(), "list_instruments",
                {"ips": ["10.20.0.9", "8.8.8.8"]}, {"instruments:control"})
    assert res.isError and "8.8.8.8" in _text(res)


def test_empty_allowlist_rejects_everything(env):
    with pytest.raises(ValueError):
        check_instrument_host("10.0.0.1")


def test_billing_and_target_tools_require_scopes(env):
    srv = server_mod.build_server()
    for name, args, scope in (
        ("get_quota", {}, "billing:read"),
        ("get_usage", {}, "billing:read"),
        ("identify_targets", {"query": "EGFR in NSCLC"}, "workflows:search"),
    ):
        res = _call(srv, name, args, {"runs:read"})
        assert res.isError and f"'{scope}'" in _text(res), (name, _text(res))


def test_instrument_scope_only_grantable_on_prem(env):
    assert "instruments:control" not in grantable_scopes()
    env.setenv("BIOMATE_MCP_ALLOW_LOCAL_TOOLS", "1")
    assert "instruments:control" in grantable_scopes()
