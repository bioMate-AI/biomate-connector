"""PKCE (RFC 7636) verification.

Connectors must use S256. The `plain` method is rejected — OAuth 2.1 forbids it.
"""

from __future__ import annotations

import base64
import hashlib


def verify_pkce(code_verifier: str, code_challenge: str, method: str = "S256") -> bool:
    """Verify a PKCE code_verifier against the stored code_challenge.

    OAuth 2.1 requires the S256 method; we hard-reject `plain`.
    """
    if method != "S256":
        return False
    if not (43 <= len(code_verifier) <= 128):
        return False
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    computed = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return computed == code_challenge
