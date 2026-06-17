"""
Illumina BaseSpace Connector
=============================
OAuth2 integration to pull completed sequencing runs from Illumina BaseSpace
and pipe them into BioMate workflows (RNA-seq, WGS, amplicon, etc.).

Authentication flow:
    1. Direct user to /integrations/basespace/authorize
    2. BaseSpace redirects to /integrations/basespace/callback?code=<code>
    3. Exchange code for access_token (stored per user)
    4. Use access_token to list projects/runs/files

Environment variables:
    BASESPACE_CLIENT_ID      OAuth2 client ID (from developer.illumina.com)
    BASESPACE_CLIENT_SECRET  OAuth2 client secret
    BASESPACE_REDIRECT_URI   https://<your-domain>/integrations/basespace/callback
    BIOMATE_API_URL          BioMate API base URL

API reference: https://developer.basespace.illumina.com/docs/content/documentation/rest-api/api-reference
"""

import logging
import os
from typing import Any, Dict, List, Optional

import requests

log = logging.getLogger(__name__)

BASESPACE_API_BASE = "https://api.basespace.illumina.com"
BASESPACE_AUTH_BASE = "https://basespace.illumina.com"
BASESPACE_CLIENT_ID = os.environ.get("BASESPACE_CLIENT_ID", "")
BASESPACE_CLIENT_SECRET = os.environ.get("BASESPACE_CLIENT_SECRET", "")
BASESPACE_REDIRECT_URI = os.environ.get(
    "BASESPACE_REDIRECT_URI",
    "http://localhost:5000/integrations/basespace/callback",
)
BASESPACE_SCOPE = "READ WRITE"


# ──────────────────────────────────────────────────────────────────────────────
# OAuth2 flow helpers
# ──────────────────────────────────────────────────────────────────────────────

def get_authorization_url(state: str = "") -> str:
    """
    Build the BaseSpace OAuth2 authorization URL.
    Redirect users here to initiate the OAuth flow.

    state: CSRF token tied to the user's session
    """
    params = (
        f"response_type=code"
        f"&client_id={BASESPACE_CLIENT_ID}"
        f"&redirect_uri={BASESPACE_REDIRECT_URI}"
        f"&scope={BASESPACE_SCOPE.replace(' ', '%20')}"
        f"&state={state}"
    )
    return f"{BASESPACE_AUTH_BASE}/oauth/authorize?{params}"


def exchange_code_for_token(code: str) -> Dict[str, Any]:
    """
    Exchange an authorization code for an access token.
    Returns dict with access_token, token_type, scope, user_id.
    """
    r = requests.post(
        f"{BASESPACE_AUTH_BASE}/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": BASESPACE_CLIENT_ID,
            "client_secret": BASESPACE_CLIENT_SECRET,
            "redirect_uri": BASESPACE_REDIRECT_URI,
        },
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def refresh_access_token(refresh_token: str) -> Dict[str, Any]:
    """Refresh an expired access token."""
    r = requests.post(
        f"{BASESPACE_AUTH_BASE}/oauth/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": BASESPACE_CLIENT_ID,
            "client_secret": BASESPACE_CLIENT_SECRET,
        },
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


# ──────────────────────────────────────────────────────────────────────────────
# BaseSpace API client
# ──────────────────────────────────────────────────────────────────────────────

class BaseSpaceConnector:
    """
    Client for the Illumina BaseSpace Sequence Hub API v1pre3.

    All methods require a valid access_token obtained via the OAuth flow.
    """

    API_VERSION = "v1pre3"

    def __init__(self, access_token: str):
        self.access_token = access_token
        self.session = requests.Session()
        self.session.headers["x-access-token"] = access_token
        self.session.headers["User-Agent"] = "BioMate-Connector/1.0"

    def _get(self, path: str, params: Optional[Dict] = None) -> Any:
        url = f"{BASESPACE_API_BASE}/{self.API_VERSION}{path}"
        r = self.session.get(url, params=params or {}, timeout=20)
        r.raise_for_status()
        return r.json().get("Response", r.json())

    # ── User ──────────────────────────────────────────────────────────────────

    def get_current_user(self) -> Dict[str, Any]:
        """Return the authenticated user's profile (id, name, email)."""
        return self._get("/users/current")

    # ── Projects ──────────────────────────────────────────────────────────────

    def list_projects(self, limit: int = 20) -> List[Dict[str, Any]]:
        """List the user's BaseSpace projects."""
        return self._get("/users/current/projects", {"Limit": limit}).get("Items", [])

    def get_project(self, project_id: str) -> Dict[str, Any]:
        return self._get(f"/projects/{project_id}")

    # ── Runs ──────────────────────────────────────────────────────────────────

    def list_runs(self, limit: int = 20, status: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        List the user's sequencing runs.
        status filter: 'Complete', 'Running', 'Failed', 'NeedsAttention'
        """
        params: Dict[str, Any] = {"Limit": limit, "SortBy": "DateCreated", "SortDir": "Desc"}
        if status:
            params["Status"] = status
        return self._get("/users/current/runs", params).get("Items", [])

    def get_run(self, run_id: str) -> Dict[str, Any]:
        """Get a single run with full metadata including sample sheet."""
        return self._get(f"/runs/{run_id}")

    def list_run_samples(self, run_id: str) -> List[Dict[str, Any]]:
        """List samples associated with a run."""
        return self._get(f"/runs/{run_id}/samples").get("Items", [])

    # ── Samples / FASTQ files ─────────────────────────────────────────────────

    def list_sample_files(self, sample_id: str) -> List[Dict[str, Any]]:
        """
        List files for a sample. FASTQ files have Name ending in .fastq.gz.
        Each file item: {Id, Name, Size, HrefContent, Path}
        """
        return self._get(f"/samples/{sample_id}/files").get("Items", [])

    def get_fastq_files(self, sample_id: str) -> List[Dict[str, Any]]:
        """Return only FASTQ files for a sample."""
        files = self.list_sample_files(sample_id)
        return [f for f in files if f.get("Name", "").endswith(".fastq.gz")]

    def get_file_download_url(self, file_id: str) -> str:
        """
        Get a pre-signed download URL for a file.
        Returns the direct HTTPS URL (valid for ~1 hour).
        """
        info = self._get(f"/files/{file_id}")
        return info.get("HrefContent", "")

    # ── Workflow detection ────────────────────────────────────────────────────

    def detect_workflow_from_run(self, run: Dict[str, Any]) -> str:
        """
        Auto-detect the appropriate BioMate workflow from a run's metadata.
        Uses experiment type from the sample sheet or run attributes.

        Returns workflow_id string:
          'wgs_germline_gatk'    — WGS/WES
          'rnaseq_differential'  — RNA-seq
          'amplicon_variant_call'— Amplicon
          'metagenomics_16s'     — 16S/amplicon metagenomics
          'generic_fastq'        — Unknown/catch-all
        """
        exp_type = run.get("ExperimentType", "").lower()
        platform = run.get("PlatformName", "").lower()

        if any(k in exp_type for k in ("wholegenome", "whole genome", "wgs", "wes")):
            return "wgs_germline_gatk"
        if any(k in exp_type for k in ("rna", "transcriptome", "rnaseq")):
            return "rnaseq_differential"
        if any(k in exp_type for k in ("amplicon", "16s", "18s", "its", "metagenom")):
            return "metagenomics_16s"
        return "generic_fastq"

    def runs_to_biomate_inputs(self, runs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Convert BaseSpace run list to BioMate session import format.
        Each entry maps to a BioMate workflow suggestion with pre-filled params.
        """
        result = []
        for run in runs:
            samples = self.list_run_samples(run["Id"])
            workflow_id = self.detect_workflow_from_run(run)
            entry = {
                "basespace_run_id": run["Id"],
                "run_name": run.get("Name"),
                "experiment_type": run.get("ExperimentType"),
                "platform": run.get("PlatformName"),
                "date_created": run.get("DateCreated"),
                "sample_count": len(samples),
                "suggested_workflow": workflow_id,
                "sample_ids": [s["Id"] for s in samples[:10]],  # first 10
            }
            result.append(entry)
        return result


# ──────────────────────────────────────────────────────────────────────────────
# Flask routes for OAuth callback (integrate into main app)
# ──────────────────────────────────────────────────────────────────────────────

def register_basespace_routes(app: Any, token_store: Any) -> None:
    """
    Register BaseSpace OAuth2 routes on an existing Flask/Express app.

    token_store: object with get(user_id) → token and set(user_id, token)
                 Use session, Redis, or database-backed store.
    """
    from flask import request, redirect, session, jsonify

    @app.route("/integrations/basespace/authorize")
    def basespace_authorize():
        """Redirect user to BaseSpace OAuth authorization page."""
        import secrets
        state = secrets.token_urlsafe(16)
        session["basespace_state"] = state
        return redirect(get_authorization_url(state=state))

    @app.route("/integrations/basespace/callback")
    def basespace_callback():
        """Handle BaseSpace OAuth callback. Exchange code for token."""
        code = request.args.get("code")
        state = request.args.get("state")
        if state != session.get("basespace_state"):
            return jsonify({"error": "CSRF state mismatch"}), 400
        if not code:
            return jsonify({"error": "No authorization code"}), 400
        try:
            token_data = exchange_code_for_token(code)
            user_id = session.get("user_id")
            if user_id:
                token_store.set(user_id, token_data)
            return redirect("/app?basespace_connected=true")
        except Exception as exc:
            log.error(f"BaseSpace OAuth error: {exc}")
            return jsonify({"error": str(exc)}), 500

    @app.route("/api/basespace/runs")
    def basespace_list_runs():
        """List the authenticated user's BaseSpace runs."""
        user_id = session.get("user_id")
        token_data = token_store.get(user_id) if user_id else None
        if not token_data:
            return jsonify({"error": "Not connected to BaseSpace"}), 401
        try:
            connector = BaseSpaceConnector(token_data["access_token"])
            runs = connector.list_runs(limit=20, status="Complete")
            return jsonify({"runs": connector.runs_to_biomate_inputs(runs)})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500
