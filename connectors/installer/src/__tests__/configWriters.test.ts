import { describe, expect, it } from 'vitest';
import { promises as fs } from 'fs';
import * as os from 'os';
import * as path from 'path';
import { upsertJSON, upsertTOML } from '../configWriters.js';

async function tmpDir(): Promise<string> {
  return fs.mkdtemp(path.join(os.tmpdir(), 'biomate-test-'));
}

describe('upsertJSON', () => {
  it('creates new file with content', async () => {
    const dir = await tmpDir();
    const f = path.join(dir, 'a', 'b', 'cfg.json');
    await upsertJSON(f, (cfg: any) => {
      cfg.mcpServers = { biomate: { command: 'npx', args: [] } };
      return cfg;
    });
    const got = JSON.parse(await fs.readFile(f, 'utf8'));
    expect(got.mcpServers.biomate.command).toBe('npx');
  });

  it('preserves existing keys', async () => {
    const dir = await tmpDir();
    const f = path.join(dir, 'cfg.json');
    await fs.writeFile(f, JSON.stringify({ theme: 'dark', mcpServers: { other: { command: 'foo' } } }));
    await upsertJSON(f, (cfg: any) => {
      cfg.mcpServers.biomate = { command: 'npx' };
      return cfg;
    });
    const got = JSON.parse(await fs.readFile(f, 'utf8'));
    expect(got.theme).toBe('dark');
    expect(got.mcpServers.other.command).toBe('foo');
    expect(got.mcpServers.biomate.command).toBe('npx');
  });
});

describe('upsertTOML', () => {
  it('appends a new biomate block', async () => {
    const dir = await tmpDir();
    const f = path.join(dir, 'config.toml');
    await fs.writeFile(f, '[other]\nfoo = "bar"\n');
    await upsertTOML(f, '\n[mcp_servers.biomate]\ncommand = "npx"\n');
    const got = await fs.readFile(f, 'utf8');
    expect(got).toContain('[other]');
    expect(got).toContain('[mcp_servers.biomate]');
    expect(got).toContain('command = "npx"');
  });

  it('replaces existing biomate block on re-install', async () => {
    const dir = await tmpDir();
    const f = path.join(dir, 'config.toml');
    await upsertTOML(f, '\n[mcp_servers.biomate]\ntoken = "old"\n');
    await upsertTOML(f, '\n[mcp_servers.biomate]\ntoken = "new"\n');
    const got = await fs.readFile(f, 'utf8');
    expect(got).toContain('token = "new"');
    expect(got).not.toContain('token = "old"');
  });
});
