import { promises as fs } from 'node:fs';
import { randomUUID } from 'node:crypto';
export type Credential = { authBase: string; clientId: string; access_token: string; refresh_token: string; expires_at: number };
function origin(value: string) {
  const url = new URL(value);
  if (url.protocol !== 'https:' && !(url.protocol === 'http:' && ['127.0.0.1', 'localhost'].includes(url.hostname))) throw new Error('OAuth requires HTTPS');
  if (url.username || url.password || url.search || url.hash || url.pathname !== '/') throw new Error('OAuth base must be an origin');
  return url.origin;
}
export async function oauthCredential(file: string): Promise<Credential> {
  const lock = file + '.lock';
  let handle;
  for (let attempt = 0; attempt < 100; attempt++) {
    try { handle = await fs.open(lock, 'wx', 0o600); break; }
    catch (error: any) { if (error.code !== 'EEXIST') throw error; await new Promise(resolve => setTimeout(resolve, 100)); }
  }
  if (!handle) throw new Error('OAuth credentials locked; retry or reconnect BioMate');
  try {
    const current = JSON.parse(await fs.readFile(file, 'utf8')) as Credential;
    current.authBase = origin(current.authBase);
    if (!current.clientId || !current.refresh_token) throw new Error('Reconnect BioMate: incomplete OAuth credentials');
    if (current.access_token && current.expires_at > Date.now() + 60000) return current;
    const response = await fetch(current.authBase + '/oauth/token', {
      method: 'POST', redirect: 'error', signal: AbortSignal.timeout(15000),
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({ grant_type: 'refresh_token', client_id: current.clientId, refresh_token: current.refresh_token }),
    });
    if (!response.ok) throw new Error(`OAuth refresh failed (HTTP ${response.status}); reconnect BioMate`);
    const updated = await response.json() as any;
    if (!updated.access_token || !updated.refresh_token || !(Number(updated.expires_in) > 0)) throw new Error('Invalid OAuth token response; reconnect BioMate');
    const next = { ...current, access_token: updated.access_token, refresh_token: updated.refresh_token, expires_at: Date.now() + Number(updated.expires_in) * 1000 };
    const tmp = file + '.' + randomUUID() + '.tmp';
    await fs.writeFile(tmp, JSON.stringify(next), { mode: 0o600 });
    await fs.rename(tmp, file);
    return next;
  } finally { await handle.close(); await fs.unlink(lock); }
}
