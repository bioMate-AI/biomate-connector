import { describe, expect, it } from 'vitest';
import { createHash } from 'crypto';
import { generatePKCE } from '../pkce.js';

describe('PKCE', () => {
  it('verifier length is in [43, 128]', () => {
    for (let i = 0; i < 20; i++) {
      const p = generatePKCE();
      expect(p.verifier.length).toBeGreaterThanOrEqual(43);
      expect(p.verifier.length).toBeLessThanOrEqual(128);
    }
  });

  it('challenge equals base64url(sha256(verifier))', () => {
    const p = generatePKCE();
    const computed = createHash('sha256').update(p.verifier).digest('base64')
      .replace(/=/g, '').replace(/\+/g, '-').replace(/\//g, '_');
    expect(p.challenge).toBe(computed);
  });

  it('uses S256 method', () => {
    expect(generatePKCE().method).toBe('S256');
  });

  it('verifier and challenge are URL-safe (no +,/,=)', () => {
    const p = generatePKCE();
    expect(p.verifier).not.toMatch(/[+/=]/);
    expect(p.challenge).not.toMatch(/[+/=]/);
  });
});
