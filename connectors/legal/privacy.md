# BioMate Connector — Privacy Policy

**Publish at:** `https://biomate.ai/legal/privacy`
**Status:** DRAFT — requires legal review before publishing.
**Effective date:** _TBD_
**Last updated:** 2026-07-02

> This is the privacy policy that governs the **BioMate connector** and the MCP
> server / API it talks to. It is referenced by the Claude Directory listing,
> the connector `README.md`, and the generated `mcp/tools_manifest.json`
> (`server.privacy_policy_url`) and `connectors/chatgpt/openapi.json`
> (`info.x-privacy-policy`). Keep all four in sync.

---

## 1. Who we are

BioMate AI ("BioMate", "we", "us") operates the BioMate scientific-workflow
platform and the connectors that let AI assistants (Claude, and others) run
BioMate workflows on your behalf. This policy explains what data the connector
sends to BioMate, how BioMate uses and stores it, who it is shared with, and
how long it is kept.

**Data controller:** BioMate AI
**Contact:** [support@biomate.ai](mailto:support@biomate.ai)

## 2. What data we collect

When you use the BioMate connector, we process the following categories of data:

| Category | Examples | Source |
|---|---|---|
| **Account & identity** | Email address, BioMate user ID, organization ID, plan tier | Your BioMate account |
| **Authentication** | OAuth 2.1 access/refresh tokens, granted scopes, API keys (hashed at rest) | OAuth flow / Settings → API Keys |
| **Request content** | The natural-language goals, prompts, and parameters you pass to `biomate_session` / `run_workflow`; workflow IDs; accessions | Sent by the AI assistant on your instruction |
| **Scientific input data** | Files you upload (e.g. FASTQ, MRC, SMILES, sequences), S3 paths, database accessions | Uploaded by you or fetched at your request |
| **Run & output data** | Run status, logs, QC results, structured findings, output files, generated reports | Produced by BioMate cloud on your runs |
| **Usage & billing metering** | LLM token counts, vCPU-hours, memory-hours, GPU-hours, storage-hours, timestamps | Generated during execution |
| **Technical metadata** | IP address, request timestamps, user-agent of the calling surface, error/diagnostic logs | Automatically at request time |

We do **not** intentionally collect special-category personal data. Do not
upload identifiable human-subject data unless your account is configured for it
and you have the legal basis to do so.

## 3. How we use your data

We use the data above only to:

- Authenticate you and authorize the scopes you granted to the connector;
- Select, configure, and execute the workflows you request;
- Stream progress and return results, findings, and reports to your AI assistant;
- Meter usage and enforce plan quotas and billing;
- Diagnose failures, provide support, secure the platform, and prevent abuse;
- Improve reliability and workflow routing in aggregate.

We do **not** sell your data. We do **not** use your scientific input data or
request content to train foundation models.

## 4. Third parties we share data with (sub-processors)

To run workflows we route data to the following processors, only as needed to
provide the service:

| Sub-processor | Purpose | Data shared |
|---|---|---|
| **Amazon Web Services (AWS)** | Compute (Batch/GPU), storage (S3), cache (ElastiCache) | Input files, run data, output files, metering counters |
| **LLM providers** (e.g. Anthropic, OpenAI, Google) | AI assistant reasoning, parameter extraction, report narrative | The request content / prompts needed for the specific AI step |
| **Lago** (usage metering) | Subscription and usage billing | User ID, aggregated usage counters (tokens, vCPU/GPU/storage hours) |

Sub-processors are bound by contract to use the data only to provide their
service to BioMate. We share data with law enforcement only where legally
required.

## 5. Where data is stored & security

- Scientific data and outputs are stored in BioMate's AWS S3 workspace.
- OAuth refresh tokens are hashed at rest (HMAC-SHA256) and rotated on every
  use; access tokens are short-lived (30 minutes).
- Authentication uses OAuth 2.1 + PKCE — no shared secrets or passwords are
  stored by the connector.
- Scope grants are per-surface and individually revocable at
  [biomate.ai/account/connectors](https://biomate.ai/account/connectors).

## 6. Data retention

- **Run data & outputs:** retained while your account is active and for as long
  as needed to provide history and reproducibility, unless you delete them.
- **Uploaded inputs:** retained in your workspace until you delete them or close
  your account.
- **Authentication tokens:** deleted on revocation, logout, or account closure.
- **Billing/metering records:** retained as required for accounting and legal
  obligations.
- On account deletion, we delete or anonymize your personal data within a
  reasonable period, subject to legal retention requirements.

## 7. Your rights

Depending on your jurisdiction, you may have the right to access, correct,
export, or delete your personal data, and to withdraw consent by revoking the
connector's access. To exercise these rights, revoke access at
[biomate.ai/account/connectors](https://biomate.ai/account/connectors) or email
[support@biomate.ai](mailto:support@biomate.ai).

## 8. Children's privacy

BioMate is not directed to children under 16 and we do not knowingly collect
their personal data.

## 9. Changes to this policy

We will update this page and the "Last updated" date when this policy changes.
Material changes will be communicated through the product or by email.

## 10. Contact

Questions or requests: [support@biomate.ai](mailto:support@biomate.ai) ·
[biomate.ai/support](https://biomate.ai/support)
