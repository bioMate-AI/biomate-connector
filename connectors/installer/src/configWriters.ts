import { promises as fs } from 'fs';
import * as path from 'path';

export async function upsertJSON(
  filePath: string,
  mutate: (obj: any) => any,
): Promise<void> {
  await fs.mkdir(path.dirname(filePath), { recursive: true });
  let existing: any = {};
  try {
    const raw = await fs.readFile(filePath, 'utf8');
    existing = JSON.parse(raw);
  } catch (e: any) {
    if (e.code !== 'ENOENT') throw e;
  }
  const next = mutate(existing) ?? existing;
  await atomicWrite(filePath, JSON.stringify(next, null, 2));
}

export async function upsertTOML(filePath: string, append: string): Promise<void> {
  await fs.mkdir(path.dirname(filePath), { recursive: true });
  let existing = '';
  try {
    existing = await fs.readFile(filePath, 'utf8');
  } catch (e: any) {
    if (e.code !== 'ENOENT') throw e;
  }
  if (existing.includes('[mcp_servers.biomate]')) {
    // Naive replacement: drop the existing block and re-append.
    existing = existing.replace(/\n\[mcp_servers\.biomate\][\s\S]*?(?=\n\[|$)/g, '');
  }
  await atomicWrite(filePath, existing + append);
}

async function atomicWrite(filePath: string, contents: string): Promise<void> {
  const tmp = `${filePath}.${process.pid}.tmp`;
  await fs.writeFile(tmp, contents, 'utf8');
  await fs.rename(tmp, filePath);
}
