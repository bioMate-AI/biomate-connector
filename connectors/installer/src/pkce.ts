import { createHash, randomBytes } from 'crypto';

export interface PKCEPair {
  verifier: string;
  challenge: string;
  method: 'S256';
}

export function generatePKCE(): PKCEPair {
  const verifier = base64url(randomBytes(48));
  const challenge = base64url(createHash('sha256').update(verifier).digest());
  return { verifier, challenge, method: 'S256' };
}

function base64url(buf: Buffer): string {
  return buf
    .toString('base64')
    .replace(/=/g, '')
    .replace(/\+/g, '-')
    .replace(/\//g, '_');
}
