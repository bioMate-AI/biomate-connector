#!/usr/bin/env node
import { runPKCEFlow } from './oauth.js';
import { SURFACES, SurfaceId } from './surfaces.js';

const AUTH_BASE = process.env.BIOMATE_AUTH_BASE ?? 'https://api.biomate.ai';

const USAGE = `\nUsage:  npx @biomate/connect <surface> [--auth-base URL]\n\nSurfaces:\n  claude-code      Anthropic Claude Code (CLI)\n  claude-desktop   Anthropic Claude Desktop\n  cursor           Cursor IDE\n  codex            OpenAI Codex CLI\n  chatgpt          ChatGPT (Custom GPT Actions)\n  open-claw        WeChat / Open Claw\n\nExamples:\n  npx @biomate/connect claude-code\n  npx @biomate/connect cursor\n\nEnvironment:\n  BIOMATE_AUTH_BASE   Auth server URL (default ${AUTH_BASE})\n  BIOMATE_API_BASE    API base URL for installed configs\n`;

async function main(): Promise<number> {
  const args = process.argv.slice(2);
  if (args.length === 0 || args[0] === '--help' || args[0] === '-h') {
    console.log(USAGE);
    return 0;
  }
  const id = args[0] as SurfaceId;
  const surface = SURFACES[id];
  if (!surface) {
    console.error(`Unknown surface: ${id}\n${USAGE}`);
    return 1;
  }

  try {
    const tokens = await runPKCEFlow(surface, AUTH_BASE);
    const where = await surface.install(tokens.access_token, tokens.refresh_token);

    // Stash the refresh token in the OS keychain too (best-effort).
    try {
      const keytar = await import('keytar');
      await keytar.default.setPassword('biomate', surface.id, tokens.refresh_token);
    } catch {
      /* keychain optional */
    }

    console.log(`\n✓ ${surface.label} connected.`);
    if (where && !where.startsWith('(')) {
      console.log(`  Config written: ${where}`);
    }
    console.log(`  Scopes granted: ${tokens.scope}`);
    if (id !== 'chatgpt' && id !== 'open-claw') {
      console.log(`\nRestart ${surface.label} to pick up the new MCP server.`);
    }
    return 0;
  } catch (err: any) {
    console.error(`\n✗ Connect failed: ${err.message ?? err}`);
    return 1;
  }
}

main().then((c) => process.exit(c));
