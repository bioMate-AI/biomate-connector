# Anthropic MCP servers — submission package

Target repo: https://github.com/modelcontextprotocol/servers

## Action

Open a PR adding BioMate to the README's "Third-party servers" table.

## PR title

`Add BioMate — bioinformatics workflow execution (nf-core, CryoSPARC, AlphaFold, ADMET)`

## PR body

> Adds BioMate AI to the third-party MCP server list.
>
> **What it does:** BioMate is an MCP server that runs real bioinformatics workflows on AWS Batch. 2,455 indexed pipelines including all of nf-core, CryoSPARC, AlphaFold/ESMFold/OpenFold, OpenMM, GROMACS, AutoDock Vina, the Bioconductor ecosystem, and ~60 custom drug-discovery workflows (PBPK, BOIN, ADMET, IND §2.6.1).
>
> **Tools (14 across 3 tiers):** one agentic streaming tool (`biomate_session`), six workflow primitives (`search_workflow`, `get_workflow_spec`, `run_workflow`, `cancel_run`, `list_runs`, `get_run`), seven outputs/analysis/reporting tools.
>
> **Auth:** OAuth 2.1 + PKCE, scope-per-surface tokens.
>
> **Install:** `npx @biomate/connect claude-code` (also Claude Desktop, Cursor, Codex).
>
> **Repo:** https://github.com/bioMate-AI/biomate-connectors
> **Docs:** https://biomate.ai/connectors
> **License:** MIT (connectors); platform usage governed by biomate.ai/terms

## Table row to add (alphabetical)

```markdown
| [BioMate](https://github.com/bioMate-AI/biomate-connectors) | Run real bioinformatics workflows (nf-core, CryoSPARC, AlphaFold, ADMET, PBPK) on AWS Batch from chat. 2,455 indexed pipelines, OAuth 2.1, streaming progress events. | `npx @biomate/connect claude-code` |
```

## Pre-submission checklist

- [ ] Repo public at github.com/bioMate-AI/biomate-connectors
- [ ] Top-level README has a 1-screen install + demo prompt
- [ ] `npx @biomate/connect claude-code` works on a fresh machine (test on a fresh VM before submission)
- [ ] MCP server has been smoke-tested against Claude Desktop + Cursor
- [ ] License headers consistent (MIT)

## After PR is open

- Drop a link in Anthropic's MCP Discord #third-party-servers channel
- Cross-reference in our launch blog post
