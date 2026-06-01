# BioMate · ChatGPT GPT Action

This directory holds the OpenAPI 3.1 spec consumed by ChatGPT's GPT builder ("Configure → Actions") to expose BioMate's 14 connector tools inside a custom GPT.

## Files

- `openapi.json` — **generated**. Do not hand-edit. Run `python -m backend.lib.mcp.tools_manifest` to regenerate from the canonical Python manifest. CI (`backend/tests/test_tools_manifest.py`) fails if the committed copy drifts.
- `gpt_config.md` — paste-ready GPT instructions, conversation starters, and capability description for the GPT builder form.

## How to ship a new GPT

1. Sign in to <https://chatgpt.com/gpts/editor> as the `support@biomate.ai` account.
2. Click **Create** → **Configure** tab.
3. Copy the contents of `gpt_config.md` into the **Name**, **Description**, **Instructions**, and **Conversation starters** fields.
4. Under **Actions**, click **Create new action** → **Import from URL** and paste:
   ```
   https://api.biomate.ai/connectors/chatgpt/openapi.json
   ```
   (or upload the local `openapi.json` directly during testing).
5. Authentication: choose **OAuth**. Use these values (matches `INSTALL.md` and the OAuth 2.1 + PKCE public-client model — there is **no client secret**):
   - **Client ID**: `biomate-chatgpt`
   - **Client Secret**: *(leave blank — PKCE public client)*
   - **Authorization URL**: `https://api.biomate.ai/oauth/authorize`
   - **Token URL**: `https://api.biomate.ai/oauth/token`
   - **Scope**: `runs:read runs:write workflows:search memory:read memory:write files:upload reports:export billing:read`
   - **Token Exchange Method**: Default (POST)
6. **Privacy policy URL**: `https://biomate.ai/legal/privacy` (required for store submission).
7. Test in the right pane: ask *"Run ADMET screening on aspirin SMILES."* → expect the GPT to call `biomate_session` and stream back a deep link.
8. **Publish** → choose **Public** for the store, **Anyone with the link** for a soft launch.

## Streaming caveat

ChatGPT GPT Actions do not yet support server-sent progress notifications inside the Action transport. The GPT model loops by calling `get_run` (a separate tool in this manifest) every few seconds until `status == "completed"`. The OpenAPI spec marks streaming tools with `x-streaming: true` for future use; the model's behavior is driven entirely by the instructions in `gpt_config.md`.

## When to regenerate

Whenever `backend/lib/mcp/tools_manifest.py` changes. The drift test in CI is the safety net but the local workflow is:

```bash
python -m backend.lib.mcp.tools_manifest
git add backend/lib/mcp/tools_manifest.json connectors/chatgpt/openapi.json
```
