import * as os from 'os';
import * as path from 'path';

export type SurfaceId =
  | 'claude-code'
  | 'claude-desktop'
  | 'cursor'
  | 'codex'
  | 'chatgpt'
  | 'open-claw';

export interface SurfaceConfig {
  id: SurfaceId;
  label: string;
  clientId: string;
  callbackPort: number;
  scopes: string[];
  /** Path of the host's MCP config file. */
  configPath: () => string;
  /** Path-agnostic config writer. Returns a description of what changed. */
  install: (token: string, refreshToken: string) => Promise<string>;
}

const DEFAULT_SCOPES = [
  'runs:read',
  'runs:write',
  'workflows:search',
  'memory:read',
  'memory:write',
  'files:upload',
  'reports:export',
  'billing:read',
];

function home(): string {
  return os.homedir();
}

function platformConfigDir(): string {
  if (process.platform === 'darwin') return path.join(home(), 'Library', 'Application Support');
  if (process.platform === 'win32') return process.env.APPDATA ?? home();
  return path.join(home(), '.config');
}

function mcpServerEntry(refreshToken: string) {
  const apiBase = process.env.BIOMATE_API_BASE ?? 'https://api.biomate.ai';
  return {
    command: 'npx',
    args: ['-y', '@biomate/mcp-server'],
    env: {
      BIOMATE_API_BASE: apiBase,
      BIOMATE_REFRESH_TOKEN: refreshToken,
    },
  };
}

import { upsertJSON, upsertTOML } from './configWriters.js';

export const SURFACES: Record<SurfaceId, SurfaceConfig> = {
  'claude-code': {
    id: 'claude-code',
    label: 'Claude Code',
    clientId: 'biomate-claude-code',
    callbackPort: 53682,
    scopes: DEFAULT_SCOPES,
    configPath: () => path.join(home(), '.claude.json'),
    install: async (_t, refresh) => {
      const p = path.join(home(), '.claude.json');
      await upsertJSON(p, (cfg) => {
        cfg.mcpServers ??= {};
        cfg.mcpServers.biomate = mcpServerEntry(refresh);
        return cfg;
      });
      return p;
    },
  },
  'claude-desktop': {
    id: 'claude-desktop',
    label: 'Claude Desktop',
    clientId: 'biomate-claude-desktop',
    callbackPort: 53683,
    scopes: DEFAULT_SCOPES,
    configPath: () => path.join(platformConfigDir(), 'Claude', 'claude_desktop_config.json'),
    install: async (_t, refresh) => {
      const p = path.join(platformConfigDir(), 'Claude', 'claude_desktop_config.json');
      await upsertJSON(p, (cfg) => {
        cfg.mcpServers ??= {};
        cfg.mcpServers.biomate = mcpServerEntry(refresh);
        return cfg;
      });
      return p;
    },
  },
  cursor: {
    id: 'cursor',
    label: 'Cursor',
    clientId: 'biomate-cursor',
    callbackPort: 53684,
    scopes: DEFAULT_SCOPES,
    configPath: () => path.join(home(), '.cursor', 'mcp.json'),
    install: async (_t, refresh) => {
      const p = path.join(home(), '.cursor', 'mcp.json');
      await upsertJSON(p, (cfg) => {
        cfg.mcpServers ??= {};
        cfg.mcpServers.biomate = mcpServerEntry(refresh);
        return cfg;
      });
      return p;
    },
  },
  codex: {
    id: 'codex',
    label: 'Codex CLI',
    clientId: 'biomate-codex',
    callbackPort: 53685,
    scopes: DEFAULT_SCOPES,
    configPath: () => path.join(home(), '.codex', 'config.toml'),
    install: async (_t, refresh) => {
      const p = path.join(home(), '.codex', 'config.toml');
      const apiBase = process.env.BIOMATE_API_BASE ?? 'https://api.biomate.ai';
      await upsertTOML(p, `\n[mcp_servers.biomate]\ncommand = "npx"\nargs = ["-y", "@biomate/mcp-server"]\n\n[mcp_servers.biomate.env]\nBIOMATE_API_BASE = "${apiBase}"\nBIOMATE_REFRESH_TOKEN = "${refresh}"\n`);
      return p;
    },
  },
  chatgpt: {
    id: 'chatgpt',
    label: 'ChatGPT',
    clientId: 'biomate-chatgpt',
    callbackPort: 53686,  // Not actually used — ChatGPT does OAuth in-app.
    scopes: DEFAULT_SCOPES,
    configPath: () => '(ChatGPT Actions — configured in chat.openai.com)',
    install: async () => {
      console.log(`
ChatGPT connects to BioMate via Custom GPT Actions, not a local config file.
Install steps:
  1. Visit https://chatgpt.com/gpts/editor and create a new GPT.
  2. Import OpenAPI from: https://api.biomate.ai/connectors/chatgpt/openapi.json
  3. Enable OAuth in the Actions panel:
       Authorization URL: https://api.biomate.ai/oauth/authorize
       Token URL:         https://api.biomate.ai/oauth/token
       Client ID:         biomate-chatgpt
       Scope:             ${DEFAULT_SCOPES.join(' ')}
  4. Save and test with: "Screen aspirin for hERG"
`);
      return '(ChatGPT in-app setup)';
    },
  },
  'open-claw': {
    id: 'open-claw',
    label: 'Open Claw (WeChat)',
    clientId: 'biomate-open-claw',
    callbackPort: 53687,
    scopes: DEFAULT_SCOPES,
    configPath: () => '(Open Claw — hosted by BioMate)',
    install: async (_t, refresh) => {
      console.log(`
Open Claw runs as a hosted webhook — there's nothing to install locally.
We've linked your account; in WeChat search the BioMate Open Claw bot and
say hi.
`);
      return '(linked)';
    },
  },
};
