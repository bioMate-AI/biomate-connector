"""
Benchling Connector
===================
Integrates BioMate with Benchling's ELN/LIMS platform via the Benchling REST API.

Capabilities:
  PULL:  notebook entries, sample inventory, assay results → BioMate workflow inputs
  PUSH:  BioMate workflow outputs → Benchling result entries (assay data or custom entities)

Authentication:
  OAuth2 client credentials (recommended for server-to-server)
  OR personal API key (for development/testing)

Environment variables:
    BENCHLING_API_URL        https://<tenant>.benchling.com
    BENCHLING_API_KEY        Personal API key (starts with sk_...)
    BENCHLING_CLIENT_ID      OAuth2 client ID
    BENCHLING_CLIENT_SECRET  OAuth2 client secret

API reference: https://benchling.com/api/reference
"""

import logging
import os
from typing import Any, Dict, List, Optional

import requests

log = logging.getLogger(__name__)

BENCHLING_API_URL = os.environ.get("BENCHLING_API_URL", "")
BENCHLING_API_KEY = os.environ.get("BENCHLING_API_KEY", "")
BENCHLING_CLIENT_ID = os.environ.get("BENCHLING_CLIENT_ID", "")
BENCHLING_CLIENT_SECRET = os.environ.get("BENCHLING_CLIENT_SECRET", "")


class BenchlingConnector:
    """
    Client for the Benchling REST API.

    Typical flow:
        1. Pull a notebook entry (experiment context)
        2. Extract sample references + assay results
        3. Map to BioMate workflow inputs
        4. Run workflow via BioMate API
        5. Push workflow outputs back as Benchling assay results
    """

    def __init__(
        self,
        api_url: Optional[str] = None,
        api_key: Optional[str] = None,
    ):
        self.api_url = (api_url or BENCHLING_API_URL).rstrip("/")
        if not self.api_url:
            raise ValueError(
                "BENCHLING_API_URL must be set (e.g. https://myorg.benchling.com)"
            )
        self.session = requests.Session()
        key = api_key or BENCHLING_API_KEY
        if key:
            # Benchling API key auth: key as username, empty password
            self.session.auth = (key, "")
        self.session.headers["Content-Type"] = "application/json"
        self.session.headers["User-Agent"] = "BioMate-Connector/1.0"

    def _get(self, path: str, params: Optional[Dict] = None) -> Any:
        r = self.session.get(f"{self.api_url}/api/v2{path}", params=params, timeout=20)
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, body: Dict) -> Any:
        r = self.session.post(f"{self.api_url}/api/v2{path}", json=body, timeout=20)
        r.raise_for_status()
        return r.json()

    def _patch(self, path: str, body: Dict) -> Any:
        r = self.session.patch(f"{self.api_url}/api/v2{path}", json=body, timeout=20)
        r.raise_for_status()
        return r.json()

    # ──────────────────────────────────────────────────────────────────────────
    # Notebook Entries (ELN)
    # ──────────────────────────────────────────────────────────────────────────

    def list_entries(
        self,
        project_id: Optional[str] = None,
        schema_id: Optional[str] = None,
        page_size: int = 50,
    ) -> List[Dict[str, Any]]:
        """List notebook entries, optionally filtered by project or schema."""
        params: Dict[str, Any] = {"pageSize": page_size}
        if project_id:
            params["projectId"] = project_id
        if schema_id:
            params["schemaId"] = schema_id
        return self._get("/entries", params=params).get("entries", [])

    def get_entry(self, entry_id: str) -> Dict[str, Any]:
        """Get a single notebook entry with full content."""
        return self._get(f"/entries/{entry_id}")

    def search_entries(self, query: str, page_size: int = 20) -> List[Dict[str, Any]]:
        """Full-text search across notebook entries."""
        params = {"q": query, "pageSize": page_size}
        return self._get("/entries", params=params).get("entries", [])

    def extract_entry_text(self, entry: Dict[str, Any]) -> str:
        """
        Extract plain text from a Benchling notebook entry's day notes.
        Handles both legacy text blocks and modern structured notes.
        """
        texts: List[str] = []
        days = entry.get("days", [])
        for day in days:
            for note in day.get("notes", []):
                # Text block
                if note.get("type") == "text" and note.get("text"):
                    texts.append(note["text"])
                # Table block — join cell values
                elif note.get("type") == "table":
                    for row in note.get("rows", []):
                        for cell in row:
                            if cell.get("text"):
                                texts.append(cell["text"])
        return "\n".join(texts)

    # ──────────────────────────────────────────────────────────────────────────
    # Samples / Inventory
    # ──────────────────────────────────────────────────────────────────────────

    def list_samples(
        self,
        schema_id: Optional[str] = None,
        page_size: int = 50,
    ) -> List[Dict[str, Any]]:
        """List sample/entity inventory. schema_id filters by sample type."""
        params: Dict[str, Any] = {"pageSize": page_size}
        if schema_id:
            params["schemaId"] = schema_id
        return self._get("/samples", params=params).get("samples", [])

    def get_sample(self, sample_id: str) -> Dict[str, Any]:
        """Get a single sample with all fields."""
        return self._get(f"/samples/{sample_id}")

    def search_samples(self, query: str, schema_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Search samples by name or barcode."""
        params: Dict[str, Any] = {"q": query}
        if schema_id:
            params["schemaId"] = schema_id
        return self._get("/samples", params=params).get("samples", [])

    def samples_to_workflow_inputs(self, samples: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Convert a list of Benchling samples to BioMate workflow input format.
        Extracts common fields: compound ID, SMILES, batch number, concentration.
        """
        compounds = []
        for s in samples:
            fields = s.get("fields", {})
            compound = {
                "benchling_id": s.get("id"),
                "name": s.get("name"),
                "smiles": fields.get("smiles", {}).get("value"),
                "batch": fields.get("batch_number", {}).get("value"),
                "concentration_mM": fields.get("concentration_mM", {}).get("value"),
                "plate_barcode": fields.get("plate_barcode", {}).get("value"),
                "well": fields.get("well_position", {}).get("value"),
            }
            compounds.append({k: v for k, v in compound.items() if v is not None})
        return {"compounds": compounds, "count": len(compounds)}

    # ──────────────────────────────────────────────────────────────────────────
    # Assay Results
    # ──────────────────────────────────────────────────────────────────────────

    def list_assay_results(
        self,
        schema_id: str,
        entry_id: Optional[str] = None,
        page_size: int = 100,
    ) -> List[Dict[str, Any]]:
        """
        List assay results for a given schema.
        schema_id: the Benchling assay result schema ID (required)
        entry_id:  filter to results from a specific notebook entry
        """
        params: Dict[str, Any] = {"schemaId": schema_id, "pageSize": page_size}
        if entry_id:
            params["entryId"] = entry_id
        return self._get("/assay-results", params=params).get("assayResults", [])

    def create_assay_result(
        self,
        schema_id: str,
        fields: Dict[str, Any],
        entry_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Create a new assay result row in Benchling.

        schema_id: the Benchling assay result schema ID
        fields:    dict of {field_name: value} — must match schema
        entry_id:  optionally link to a notebook entry
        """
        body: Dict[str, Any] = {
            "assayResults": [
                {
                    "schemaId": schema_id,
                    "fields": {k: {"value": v} for k, v in fields.items()},
                }
            ]
        }
        if entry_id:
            body["assayResults"][0]["entryId"] = entry_id
        return self._post("/assay-results:bulk-create", body)

    def bulk_create_assay_results(
        self,
        schema_id: str,
        rows: List[Dict[str, Any]],
        entry_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Bulk-create assay result rows from a list of field dicts.
        Efficiently uploads BioMate workflow outputs (e.g. 100 ADMET results) in one call.
        """
        results_payload = []
        for row in rows:
            payload: Dict[str, Any] = {
                "schemaId": schema_id,
                "fields": {k: {"value": v} for k, v in row.items()},
            }
            if entry_id:
                payload["entryId"] = entry_id
            results_payload.append(payload)
        body = {"assayResults": results_payload}
        response = self._post("/assay-results:bulk-create", body)
        log.info(f"Bulk-created {len(rows)} assay results in schema {schema_id}")
        return response

    # ──────────────────────────────────────────────────────────────────────────
    # Projects / folders
    # ──────────────────────────────────────────────────────────────────────────

    def list_projects(self) -> List[Dict[str, Any]]:
        """List all accessible Benchling projects."""
        return self._get("/projects").get("projects", [])

    def get_project(self, project_id: str) -> Dict[str, Any]:
        """Get project metadata."""
        return self._get(f"/projects/{project_id}")

    # ──────────────────────────────────────────────────────────────────────────
    # BioMate integration helpers
    # ──────────────────────────────────────────────────────────────────────────

    def pull_workflow_inputs(
        self,
        project_id: Optional[str] = None,
        entry_id: Optional[str] = None,
        sample_schema_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        High-level pull: gather notebook context + sample inventory from Benchling
        and return a dict ready for BioMate workflow parameter injection.
        """
        result: Dict[str, Any] = {}

        # Notebook context
        if entry_id:
            entry = self.get_entry(entry_id)
            result["notebook_text"] = self.extract_entry_text(entry)
            result["entry_name"] = entry.get("name")
            result["entry_id"] = entry_id

        # Sample inventory
        if sample_schema_id:
            samples = self.list_samples(schema_id=sample_schema_id)
            result["compounds"] = self.samples_to_workflow_inputs(samples)

        return result

    def push_workflow_outputs(
        self,
        assay_schema_id: str,
        result_rows: List[Dict[str, Any]],
        entry_id: Optional[str] = None,
        field_mapping: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """
        High-level push: upload BioMate workflow outputs to Benchling as assay results.

        result_rows:   list of dicts from BioMate output CSV (e.g. ranked_compounds.csv)
        field_mapping: rename BioMate fields to Benchling schema fields
                       e.g. {"composite_score": "BioMate_Score", "herg_ic50_um": "hERG_IC50"}
        """
        if field_mapping:
            result_rows = [
                {field_mapping.get(k, k): v for k, v in row.items()}
                for row in result_rows
            ]
        return self.bulk_create_assay_results(
            schema_id=assay_schema_id,
            rows=result_rows,
            entry_id=entry_id,
        )
