#!/usr/bin/env node
/**
 * @biomate/mcp-server
 *
 * MCP stdio server that exposes BioMate's scientific workflow capabilities
 * to any MCP-compatible AI host (Claude Code, Claude Desktop, Cursor, Codex CLI).
 *
 * Configuration (environment variables):
 *   BIOMATE_API_BASE   BioMate server URL (default: https://dev-public.biomate.ai)
 *   BIOMATE_API_KEY    Personal API key (bm_live_xxx) from biomate.ai/account/api-keys
 *   BIOMATE_OAUTH_FILE  Protected credential file written by @biomate/connect
 *
 * Each tool call POSTs to ${BIOMATE_API_BASE}/tools/${toolName} with the
 * Authorization header set. The /tools/* routes are served by BioMate's
 * Node.js API server.
 */

import { oauthCredential } from "./auth.js";
import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
  type Tool,
} from "@modelcontextprotocol/sdk/types.js";
import { createRequire } from "module";

const require = createRequire(import.meta.url);

// ── Config ────────────────────────────────────────────────────────────────────
const API_BASE =
  (process.env.BIOMATE_API_BASE ?? "https://dev-public.biomate.ai").replace(/\/$/, "");
const API_KEY = process.env.BIOMATE_API_KEY ?? "";

// ── Tools manifest ────────────────────────────────────────────────────────────
const manifest = require("./tools_manifest.json") as {
  mcp: Array<{
    name: string;
    title?: string;
    description: string;
    inputSchema: object;
  }>;
};

const TOOLS: Tool[] = manifest.mcp.map((t) => ({
  name: t.name,
  description: t.description,
  inputSchema: t.inputSchema as Tool["inputSchema"],
}));

// ── Server ────────────────────────────────────────────────────────────────────
const server = new Server(
  { name: "biomate", version: "2.0.0" },
  { capabilities: { tools: {} } }
);

server.setRequestHandler(ListToolsRequestSchema, async () => ({ tools: TOOLS }));

server.setRequestHandler(CallToolRequestSchema, async (req) => {
  const { name, arguments: args } = req.params;

  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  if (API_KEY) {
    headers["Authorization"] = `Bearer ${API_KEY}`;
  }

  let data: unknown;
  let ok = true;

  try {
    if (!TOOLS.some(tool => tool.name === name)) throw new Error("Unknown tool");
    let endpoint = `${API_BASE}/tools/${name}`;
    if (!API_KEY) {
      if (!process.env.BIOMATE_OAUTH_FILE) throw new Error("Reconnect BioMate with the updated installer or configure a personal API key");
      const credential = await oauthCredential(process.env.BIOMATE_OAUTH_FILE);
      headers.Authorization = `Bearer ${credential.access_token}`;
      endpoint = `${credential.authBase}/oauth/tools/${name}`;
    }
    const res = await fetch(endpoint, {
      redirect: "error",
      method: "POST",
      headers,
      body: JSON.stringify(args ?? {}),
      signal: AbortSignal.timeout(120_000),
    });
    ok = res.ok;
    try {
      data = await res.json();
    } catch {
      data = { error: `HTTP ${res.status}: ${res.statusText}` };
      ok = false;
    }
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err);
    data = { error: msg };
    ok = false;
  }

  return {
    content: [{ type: "text", text: JSON.stringify(data, null, 2) }],
    isError: !ok,
  };
});

// ── Start ─────────────────────────────────────────────────────────────────────
if (!API_KEY && !process.env.BIOMATE_OAUTH_FILE) {
  process.stderr.write(
    "[biomate-mcp] WARNING: BIOMATE_API_KEY not set. " +
    "Get your key at https://dev-public.biomate.ai/account/api-keys\n"
  );
}

const transport = new StdioServerTransport();
await server.connect(transport);
