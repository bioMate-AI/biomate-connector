# BioMate × ChatGPT — Install

> ChatGPT integrates via a Custom GPT with OAuth 2.1 + PKCE Actions. There's no local install — the entire wiring lives in https://chatgpt.com/gpts/editor.

## One-tap install

Once published, the BioMate Custom GPT will live at:

**https://chatgpt.com/g/g-biomate**

Click **Start chat** → ChatGPT prompts for OAuth → done.

## Self-host setup (if creating your own GPT)

If you want to register your own GPT against BioMate (for org-specific scopes, etc.):

### 1. Create a GPT

Visit https://chatgpt.com/gpts/editor and click **+ Create**.

### 2. Configure Actions

In the **Configure** tab, scroll to **Actions** → **Create new action**.

**Import OpenAPI** from URL:

```
https://api.biomate.ai/connectors/chatgpt/openapi.json
```

Or paste the contents of [`openapi.json`](./openapi.json) in this directory.

### 3. Configure Authentication

Click the gear icon next to Actions → **Authentication** → **OAuth**:

| Field | Value |
|---|---|
| Authorization URL | `https://api.biomate.ai/oauth/authorize` |
| Token URL | `https://api.biomate.ai/oauth/token` |
| Client ID | `biomate-chatgpt` |
| Client Secret | *(leave blank — PKCE public client)* |
| Scope | `runs:read runs:write workflows:search memory:read memory:write files:upload reports:export billing:read` |
| Token Exchange Method | Default (POST) |

ChatGPT generates a redirect URI like `https://chat.openai.com/aip/g-xxxx/oauth/callback`. Add it to your BioMate OAuth client registration (already done for the official `biomate-chatgpt` client).

### 4. Test

In the GPT editor preview:

```
Screen aspirin for hERG and CYP3A4 inhibition.
```

ChatGPT should prompt for OAuth, then return the ADMET results.

## Configure tab — recommended instructions

Paste into the **Instructions** field of the GPT:

```
You are a bioinformatics research assistant powered by BioMate, an execution
engine that runs real workflows (RNA-seq/WGS, CryoSPARC, AlphaFold, ADMET, PBPK,
etc.) on BioMate cloud and streams results.

When the user asks for analysis:
  1. Use `search_workflow` to find a matching pipeline.
  2. Use `get_workflow_spec` to discover required parameters.
  3. Use `run_workflow` with `stream=true` to start the run.
  4. Poll `get_run` while it's running; surface phase/step transitions.
  5. Use `preview_file` for output thumbnails, `export_report` for PDFs.

For repeat users, call `recall_memory` first — past runs and findings often
make the next prompt 5x faster.

Never invent workflow IDs or parameters. If you don't know, search first.
```

## Try it

```
Run RNA-seq differential expression on s3://biomate-demo/rnaseq/ — treated vs control,
GRCh38. Show the top 20 DE genes when done.
```

```
Look up UniProt P04637 and summarize the top mutations.
```

```
Generate an IND §2.6.1 narrative from the latest ADMET + PBPK runs in my account.
```

## Tools available

Same 14-tool surface as Claude Code — see [`../claude-code/README.md`](../claude-code/README.md). ChatGPT renders markdown summaries inline; on GPT-4o vision, thumbnails of QC and finding cards render as well.

## License

MIT.
