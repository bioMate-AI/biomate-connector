"""Redirect URI policy for client registration (DCR and manual).

OAuth 2.1 §2.3.1 / MCP authorization: redirect URIs must be https, or http
on a loopback host for native apps that listen locally (the BioMate
installer uses ``http://127.0.0.1:<port>/callback``). Custom schemes are not
accepted: no supported BioMate client registers one.
"""

from __future__ import annotations

from urllib.parse import urlsplit

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def redirect_uri_problem(uri: object) -> str | None:
    """Return why ``uri`` may not be registered, or None if it is acceptable."""
    if not isinstance(uri, str) or not uri:
        return "redirect URI must be a non-empty string"
    try:
        parts = urlsplit(uri)
        parts.port  # raises ValueError on a malformed port
    except ValueError:
        return f"malformed redirect URI: {uri!r}"
    if parts.fragment or not parts.hostname:
        return f"redirect URI must be absolute without a fragment: {uri!r}"
    if parts.scheme == "https":
        return None
    if parts.scheme == "http" and parts.hostname in LOOPBACK_HOSTS:
        return None
    return f"redirect URI must be https (http only on loopback): {uri!r}"


def validate_redirect_uri(uri: object) -> None:
    problem = redirect_uri_problem(uri)
    if problem:
        raise ValueError(problem)
