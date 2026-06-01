import { createServer, IncomingMessage, ServerResponse } from 'http';
import { URL } from 'url';
import open from 'open';
import { generatePKCE } from './pkce.js';
import { SurfaceConfig } from './surfaces.js';

export interface TokenSet {
  access_token: string;
  refresh_token: string;
  expires_in: number;
  scope: string;
}

export async function runPKCEFlow(
  surface: SurfaceConfig,
  authBaseUrl: string,
): Promise<TokenSet> {
  const { verifier, challenge } = generatePKCE();
  const port = surface.callbackPort;
  const redirectUri = `http://127.0.0.1:${port}/callback`;
  const state = Math.random().toString(36).slice(2);

  const codePromise = catchCallback(port, state);
  // Mark the rejection as handled — the awaiter below will still receive it.
  // Without this, Node's unhandled-rejection detector fires when the callback
  // server rejects before `await codePromise` has attached a handler.
  codePromise.catch(() => undefined);

  const authUrl = new URL(`${authBaseUrl}/oauth/authorize`);
  authUrl.searchParams.set('response_type', 'code');
  authUrl.searchParams.set('client_id', surface.clientId);
  authUrl.searchParams.set('redirect_uri', redirectUri);
  authUrl.searchParams.set('code_challenge', challenge);
  authUrl.searchParams.set('code_challenge_method', 'S256');
  authUrl.searchParams.set('scope', surface.scopes.join(' '));
  authUrl.searchParams.set('state', state);

  console.log(`\nOpening browser to authorize ${surface.label}…`);
  console.log(`If it doesn't open, visit:\n  ${authUrl.toString()}\n`);
  await open(authUrl.toString());

  const code = await codePromise;

  const body = new URLSearchParams({
    grant_type: 'authorization_code',
    code,
    redirect_uri: redirectUri,
    client_id: surface.clientId,
    code_verifier: verifier,
  });
  const resp = await fetch(`${authBaseUrl}/oauth/token`, {
    method: 'POST',
    headers: { 'content-type': 'application/x-www-form-urlencoded' },
    body,
  });
  if (!resp.ok) {
    throw new Error(`token exchange failed: ${resp.status} ${await resp.text()}`);
  }
  return (await resp.json()) as TokenSet;
}

function catchCallback(port: number, expectedState: string): Promise<string> {
  return new Promise((resolve, reject) => {
    const server = createServer((req: IncomingMessage, res: ServerResponse) => {
      try {
        const url = new URL(req.url ?? '/', `http://127.0.0.1:${port}`);
        if (url.pathname !== '/callback') {
          res.writeHead(404);
          res.end();
          return;
        }
        const code = url.searchParams.get('code');
        const state = url.searchParams.get('state');
        const error = url.searchParams.get('error');
        res.writeHead(200, { 'content-type': 'text/html' });
        if (error) {
          res.end(htmlResult(`Authorization failed: ${error}`, false));
          server.close();
          reject(new Error(error));
          return;
        }
        if (!code || state !== expectedState) {
          res.end(htmlResult('Invalid callback (missing code or state mismatch).', false));
          server.close();
          reject(new Error('invalid callback'));
          return;
        }
        res.end(htmlResult('Connected! You can close this tab.', true));
        server.close();
        resolve(code);
      } catch (e) {
        reject(e as Error);
      }
    });
    server.on('error', reject);
    server.listen(port, '127.0.0.1');
    setTimeout(() => {
      server.close();
      reject(new Error('OAuth callback timed out after 3 minutes'));
    }, 3 * 60 * 1000);
  });
}

function htmlResult(message: string, ok: boolean): string {
  const color = ok ? '#0a7' : '#c33';
  return `<!doctype html><html><body style="font:14px/1.5 -apple-system,sans-serif;text-align:center;margin-top:80px">
<h1 style="color:${color}">${message}</h1>
<p style="color:#666">BioMate connector installer</p></body></html>`;
}
