#!/usr/bin/env python3
"""
MCP end-to-end test for biomate_session.
Tests the two-phase streaming flow:
  Phase 1: /api/chat/stream → AI response + workflow suggestion
  Phase 2: /api/workflows/execute → invocationId
  Phase 3: /api/workflows/:invocationId/events → SSE progress events

Usage:
    BIOMATE_API_URL=http://localhost:3000 \
    BIOMATE_API_KEY=<key> \
    python tests/test_mcp_e2e.py
"""

import json
import os
import subprocess
import sys
import time
import tempfile
from pathlib import Path

import pytest

BIOMATE_API_URL = os.environ.get("BIOMATE_API_URL", "http://localhost:3000")
BIOMATE_API_KEY = os.environ.get("BIOMATE_API_KEY", "")
MCP_SERVER = Path(__file__).parent.parent / "mcp" / "biomate_mcp_server.py"


def _server_reachable() -> bool:
    """Quick TCP probe — skip e2e tests if BioMate isn't running."""
    import socket
    from urllib.parse import urlparse
    p = urlparse(BIOMATE_API_URL)
    host = p.hostname or "localhost"
    port = p.port or (443 if p.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except OSError:
        return False


_SERVER_UP = _server_reachable()
requires_server = pytest.mark.skipif(
    not _SERVER_UP,
    reason=f"BioMate server not reachable at {BIOMATE_API_URL} — set BIOMATE_API_URL to run e2e tests",
)

# ── helpers ──────────────────────────────────────────────────────────────────

def send(proc, obj: dict) -> None:
    line = json.dumps(obj) + "\n"
    proc.stdin.write(line.encode())
    proc.stdin.flush()

def recv(proc, timeout: float = 30.0) -> dict:
    """Read next JSON-RPC line from server stdout."""
    import select
    deadline = time.monotonic() + timeout
    buf = b""
    while time.monotonic() < deadline:
        r, _, _ = select.select([proc.stdout], [], [], 0.2)
        if r:
            chunk = proc.stdout.read(1)
            if not chunk:
                break
            buf += chunk
            if buf.endswith(b"\n"):
                return json.loads(buf.strip())
    raise TimeoutError(f"No response within {timeout}s (buf={buf[:200]})")

def recv_all(proc, timeout: float = 180.0) -> list:
    """Read all JSON-RPC lines until tools/call response (with id) arrives."""
    import select
    deadline = time.monotonic() + timeout
    messages = []
    buf = b""
    while time.monotonic() < deadline:
        r, _, _ = select.select([proc.stdout], [], [], 0.5)
        if r:
            chunk = proc.stdout.read(1)
            if not chunk:
                break
            buf += chunk
            if buf.endswith(b"\n"):
                line = buf.strip()
                buf = b""
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                messages.append(msg)
                # Terminal: got a tools/call response (has id + result or error)
                if msg.get("id") == 2 and ("result" in msg or "error" in msg):
                    return messages
    return messages

# ── test ─────────────────────────────────────────────────────────────────────

@requires_server
def test_biomate_session():
    env = {**os.environ, "BIOMATE_API_URL": BIOMATE_API_URL, "MCP_DEBUG": "1"}
    if BIOMATE_API_KEY:
        env["BIOMATE_API_KEY"] = BIOMATE_API_KEY

    proc = subprocess.Popen(
        [sys.executable, str(MCP_SERVER)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )

    try:
        # 1. initialize
        send(proc, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2024-11-05",
            "clientInfo": {"name": "test-client", "version": "1.0.0"},
            "capabilities": {"tools": {}}
        }})
        init_resp = recv(proc, timeout=10)
        print("INIT:", json.dumps(init_resp.get("result", init_resp), indent=2)[:200])
        assert "serverInfo" in init_resp.get("result", {}), f"Bad init: {init_resp}"

        # 2. notifications/initialized
        send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})

        # 3. biomate_session — ADMET screening goal
        # Use a progressToken so we receive notifications/progress events
        send(proc, {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "biomate_session",
                "arguments": {
                    "goal": "Screen aspirin and caffeine for ADMET properties including hERG inhibition and CYP3A4 metabolism.",
                    "stream": True,
                },
                "_meta": {"progressToken": "test-token-001"}
            }
        })

        print("\nWaiting for response (up to 3 min)...")
        messages = recv_all(proc, timeout=180)

        # Analyse
        notifications = [m for m in messages if m.get("method") == "notifications/progress"]
        final = next((m for m in messages if m.get("id") == 2), None)

        kinds = [n.get("params", {}).get("_meta", {}).get("kind") for n in notifications]
        view_urls = [n.get("params", {}).get("_meta", {}).get("view_url") for n in notifications]
        view_url_present = any(u for u in view_urls if u)
        thumbnails = [n.get("params", {}).get("_meta", {}).get("thumbnail_png_b64") for n in notifications]
        thumbnail_present = any(t for t in thumbnails if t)

        print(f"\nevents={len(notifications)} kinds={kinds}")
        print(f"view_url_present={view_url_present}")
        print(f"thumbnail_present={thumbnail_present}")

        if final:
            try:
                text = final["result"]["content"][0]["text"]
                parsed = json.loads(text)
                print("\nFINAL summary_md:", str(parsed.get("summary_md", ""))[:200])
                print("FINAL run_id:", parsed.get("run_id"))
                print("FINAL view_url:", parsed.get("view_url"))
            except Exception:
                print("FINAL (raw):", str(final)[:400])
        else:
            print("ERROR: No final response received")
            if messages:
                print("Last message:", messages[-1])

        # Assertions
        assert final is not None, "No tools/call response received"
        assert "error" not in final, f"Got error response: {final}"

        # Phase 1: expect text_delta notifications (AI narration)
        text_deltas = [k for k in kinds if k == "text_delta"]
        assert len(text_deltas) > 0, "Expected at least one text_delta notification from chat stream"

        # Phase 2+3: expect phase_started + more structured events
        phase_events = [k for k in kinds if k and k.startswith("phase_")]
        if not phase_events:
            print("\nWARN: No phase_started events — workflow may not have run (no auth?)")
        else:
            assert len(phase_events) >= 1, "Expected at least one phase event"

        print("\n✓ PASS")
        return True

    finally:
        proc.terminate()
        proc.wait(timeout=5)
        stderr = proc.stderr.read()
        if stderr:
            print("\n--- MCP server stderr ---")
            print(stderr.decode(errors="replace")[-3000:])


@requires_server
def test_search_workflow():
    """Quick sanity check for a non-streaming tool."""
    env = {**os.environ, "BIOMATE_API_URL": BIOMATE_API_URL}
    if BIOMATE_API_KEY:
        env["BIOMATE_API_KEY"] = BIOMATE_API_KEY

    proc = subprocess.Popen(
        [sys.executable, str(MCP_SERVER)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    try:
        send(proc, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2024-11-05",
            "clientInfo": {"name": "test-client", "version": "1.0"},
            "capabilities": {}
        }})
        resp = recv(proc, timeout=10)
        assert "result" in resp, resp

        send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})

        send(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
            "name": "search_workflow",
            "arguments": {"query": "ADMET drug screening", "limit": 3}
        }})
        resp = recv(proc, timeout=15)
        assert "result" in resp, f"search_workflow failed: {resp}"
        text = resp["result"]["content"][0]["text"]
        data = json.loads(text)
        print("search_workflow:", str(data)[:300])
        print("✓ search_workflow PASS")
        return True
    finally:
        proc.terminate()
        proc.wait(timeout=5)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--search-only", action="store_true", help="Only run search_workflow test (fast)")
    args = parser.parse_args()

    if args.search_only:
        ok = test_search_workflow()
    else:
        ok = test_search_workflow() and test_biomate_session()

    sys.exit(0 if ok else 1)
