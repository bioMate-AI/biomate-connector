# BioMate Connector Test Guide
**Date:** 2026-06-02  
**Platforms:** Claude (MCP), WeChat Work, Slack, Doubao (Coze)

This guide lets you verify that each connector is working end-to-end against a live BioMate server. Run the tests in order — each platform requires a short one-time setup, then a single test query.

---

## Prerequisites (all platforms)

You need a running BioMate server and a valid API key before testing any connector.

**Check the server is up:**
```bash
curl -s http://localhost:5000/api/health
# → {"status": "ok", ...}
```

**Get your API key:**  
Log in to BioMate → Settings → API Keys → copy your key.

Set it in your shell for the commands below:
```bash
export BIOMATE_API_URL=http://localhost:5000       # or https://app.biomate.ai
export BIOMATE_API_KEY=your_key_here
```

**Verify the key works:**
```bash
curl -s -H "Authorization: Bearer $BIOMATE_API_KEY" \
  "$BIOMATE_API_URL/api/chat/stream" \
  -H "Content-Type: application/json" \
  -d '{"message":"What workflows do you have for ADMET screening?"}' \
  --no-buffer | head -20
# → lines beginning with "event: delta" and "data: {"text": ..."
```

---

## Platform 1 — Claude (MCP server)

### What it does
The MCP server exposes `biomate_session` (full 3-phase streaming) and `search_workflow` (quick lookup) as tools. Claude Code and Claude Desktop call them directly.

### One-time setup

**A. Claude Code (CLI)**

Add to `~/.claude/claude_code_config.json` (or run `claude mcp add`):
```json
{
  "mcpServers": {
    "biomate": {
      "command": "python3",
      "args": ["/path/to/biomate-connectors-v2/backend/lib/mcp/biomate_mcp_server.py"],
      "env": {
        "BIOMATE_API_URL": "http://localhost:5000",
        "BIOMATE_API_KEY": "your_key_here"
      }
    }
  }
}
```

Then restart Claude Code: `claude --restart` or quit and reopen.

**B. Claude Desktop**

Edit `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS):
```json
{
  "mcpServers": {
    "biomate": {
      "command": "python3",
      "args": ["/path/to/biomate-connectors-v2/backend/lib/mcp/biomate_mcp_server.py"],
      "env": {
        "BIOMATE_API_URL": "http://localhost:5000",
        "BIOMATE_API_KEY": "your_key_here"
      }
    }
  }
}
```

Restart Claude Desktop.

### Test commands

**Test 1 — tool list (confirms MCP server is connected):**  
In Claude Code or Claude Desktop, type:
```
/mcp
```
Expected: `biomate` appears in the list with tools `biomate_session` and `search_workflow`.

**Test 2 — quick workflow search:**
```
Use biomate to search for ADMET drug screening workflows
```
Expected: Claude calls `search_workflow`, returns a list of BioMate workflows.  
You should see the tool call appear in the Claude UI.

**Test 3 — full session (AI narration + workflow execution):**
```
Use BioMate to screen aspirin and caffeine for ADMET properties 
including hERG inhibition and CYP3A4 metabolism
```
Expected sequence:
1. Claude calls `biomate_session` with `stream: true`
2. You see streaming text appear (Phase 1 — AI narration)
3. Claude reports a workflow has been launched (Phase 2 — execution)
4. Progress events stream in (Phase 3 — SSE updates)
5. Final result includes `run_id` and `view_url`

**Test 4 — multi-turn (context continuity):**  
After Test 3, without starting a new chat:
```
What were the hERG results?
```
Expected: Claude's answer references the ADMET results from the previous turn.

### Troubleshooting

| Symptom | Fix |
|---|---|
| `biomate` not in `/mcp` list | Check the config file path; run `python3 biomate_mcp_server.py` manually to see errors |
| `biomate_session` returns `events=0` | Check `BIOMATE_API_KEY` is set correctly; verify `/api/chat/stream` works with `curl` |
| Timeout after Phase 1 | Normal if AWS Batch workflow takes >2 min; Phase 3 waits up to 60 min |

---

## Platform 2 — WeChat Work (企业微信)

### What it does
The WeChat Work bot receives messages, routes them through `/api/chat/stream`, and replies with the AI response + a workflow card. The bot replies within 5 s (immediate ACK) then sends the full answer asynchronously.

### One-time setup

**A. Register the app in WeChat Work admin console**
1. Go to https://work.weixin.qq.com/ → Your Company → Apps → Create App
2. Set "Receive Messages" API URL: `https://your-domain/integrations/wechat/message`
3. Note the **Token** and **EncodingAESKey**

**B. Set environment variables and start the bot server:**
```bash
export WECHAT_CORP_ID=your_corp_id
export WECHAT_CORP_SECRET=your_corp_secret
export WECHAT_TOKEN=your_token
export WECHAT_ENCODING_AES_KEY=your_aes_key
export WECHAT_AGENT_ID=your_agent_id
export BIOMATE_API_URL=http://localhost:5000
export BIOMATE_API_KEY=your_key_here
export BIOMATE_DEEP_LINK_BASE=https://app.biomate.ai

python3 backend/lib/integrations/wechat_bot.py --port 8091
```

**C. Expose port 8091 with ngrok (for local testing):**
```bash
ngrok http 8091
# Copy the https URL → paste into WeChat Work admin console as API URL
```

### Test commands

Open WeChat Work on your phone or desktop → find the BioMate app.

**Test 1 — help:**
```
帮助
```
Expected: Help text listing available commands and link to BioMate.

**Test 2 — bind API key:**
```
bind your_api_key_here
```
Expected: `✅ BioMate账号绑定成功！`

**Test 3 — scientific query:**
```
对阿司匹林和布洛芬进行ADMET筛选
```
Expected:
1. Immediate reply: `🤖 BioMate正在分析：对阿司匹林和布洛芬进行ADMET筛选…`
2. Within ~30 s: second message with the AI analysis response
3. If a workflow is found: a workflow card with a "在BioMate中运行" button

**Test 4 — English query also works:**
```
Run RNA-seq differential expression analysis
```
Expected: same async reply pattern in English.

**Test 5 — multi-turn (follow-up):**
After Test 3:
```
Which compound has better oral bioavailability?
```
Expected: Answer referencing the prior ADMET results (conversation context maintained).

**Test 6 — clear history:**
```
clear
```
Expected: `✅ 对话历史已清除，开始新对话。`

### Troubleshooting

| Symptom | Fix |
|---|---|
| No reply from bot | Check ngrok URL matches WeChat admin console; check `WECHAT_TOKEN` matches |
| "BioMate AI engine不可用" | `BIOMATE_API_KEY` not set or BioMate server is down |
| Workflow card not sent | `WECHAT_AGENT_ID` not set; WeChat API token may have expired |
| Bot replies but WeChat shows "该消息已过期" | WeChat's 5 s response window passed — check server latency |

---

## Platform 3 — Slack

### What it does
The `/biomate` slash command sends queries through `/api/chat/stream` and posts the result as a Block Kit message with a "Run in BioMate" button.

### One-time setup

**A. Create a Slack app**
1. Go to https://api.slack.com/apps → Create New App → From scratch
2. App name: **BioMate** | Workspace: your workspace
3. Under "Slash Commands" → Create New Command:
   - Command: `/biomate`
   - Request URL: `https://your-domain/integrations/slack/command`
   - Short description: `Ask BioMate's scientific AI`
4. Under "OAuth & Permissions" → Bot Token Scopes: add `commands`, `chat:write`
5. Install app to workspace → copy **Bot User OAuth Token** (`xoxb-...`)
6. Under "Basic Information" → copy **Signing Secret**

**B. Start the Slack bot server:**
```bash
export SLACK_BOT_TOKEN=xoxb-your-token
export SLACK_SIGNING_SECRET=your-signing-secret
export BIOMATE_API_URL=http://localhost:5000
export BIOMATE_API_KEY=your_key_here
export BIOMATE_DEEP_LINK_BASE=https://app.biomate.ai

python3 backend/lib/integrations/slack_bot.py --port 8090
```

**C. Expose with ngrok:**
```bash
ngrok http 8090
# Paste the https URL as the slash command Request URL in the Slack app console
```

### Test commands

In any Slack channel where the BioMate app is installed:

**Test 1 — basic query:**
```
/biomate Screen aspirin for ADMET properties
```
Expected: Slack bot posts a Block Kit message with:
- AI response text
- Workflow name (e.g. "Admet Screening")
- "Run in BioMate" button linking to `https://app.biomate.ai?workflow=admet_screening`

**Test 2 — RNA-seq query:**
```
/biomate Differential expression analysis on RNA-seq data from human liver tissue
```
Expected: Response mentioning nf-core/rnaseq or DESeq2 workflow.

**Test 3 — general question (no workflow):**
```
/biomate What is the difference between SMILES and InChI?
```
Expected: Text answer only, no workflow card (no "Run in BioMate" button).

**Test 4 — multi-turn (DM the bot):**  
Open a DM with the BioMate bot:
```
/biomate Explain ADMET screening
/biomate What about hERG specifically?
```
Expected: The second reply references context from the first.

### Troubleshooting

| Symptom | Fix |
|---|---|
| `dispatch_failed` in Slack | ngrok URL changed; update Request URL in Slack app console |
| `invalid_auth` | `SLACK_BOT_TOKEN` wrong or app not installed to workspace |
| Bot replies "request verification failed" | `SLACK_SIGNING_SECRET` mismatch |
| No "Run in BioMate" button | BioMate didn't detect a workflow — try a more specific query with a molecule name |

---

## Platform 4 — Doubao via Coze (豆包 / 扣子)

### What it does
A Coze plugin exposes BioMate's AI as an HTTP tool. A Coze Bot uses the plugin to answer scientific questions and posts "Run in BioMate" links. The bot can be published to the Doubao app (iOS/Android), Feishu/Lark, WeChat (separate from WeChat Work), and other channels.

### One-time setup

**A. Deploy the Coze plugin server**
```bash
export COZE_PLUGIN_SECRET=choose_a_random_secret_here
export BIOMATE_API_URL=http://localhost:5000
export BIOMATE_API_KEY=your_key_here
export BIOMATE_DEEP_LINK_BASE=https://app.biomate.ai

python3 backend/lib/integrations/coze_plugin.py --port 8092
```

With ngrok:
```bash
ngrok http 8092
# Note the https URL, e.g. https://abc123.ngrok.io
```

**B. Register the plugin in Coze console**

For international: https://www.coze.com/open/plugin  
For China: https://www.coze.cn/open/plugin

1. Click "Create Plugin"
2. **Plugin name:** BioMate Scientific AI
3. **Server URL:** `https://abc123.ngrok.io/coze-plugin`
4. **Auth type:** Service Level
   - Header name: `X-BioMate-Plugin-Key`
   - Header value: the `COZE_PLUGIN_SECRET` you set above
5. Click "Import from URL" and paste:
   `https://abc123.ngrok.io/coze-plugin/openapi.yaml`  
   — or upload `connectors/coze/openapi.yaml` directly after editing `servers[0].url`
6. Coze will validate the server (calls `GET /coze-plugin/health`) → should show green checkmark
7. Save the plugin

**C. Create a Bot**
1. Coze console → "Create Bot"
2. **Bot name:** BioMate
3. **System prompt** (paste this):
   ```
   You are BioMate, an AI scientific assistant specializing in life science 
   computational workflows. When users ask biology, drug discovery, genomics, 
   proteomics, or clinical research questions, use the biomate_query tool to 
   get an answer and a workflow recommendation. Always include the "Run in 
   BioMate" link when one is provided.
   ```
4. Under "Plugins" → Add Plugin → select **BioMate Scientific AI**
5. Click "Publish" → select "Doubao" channel (and any others)

### Test commands

Open the Doubao app (iOS/Android) or the Coze web playground, find your BioMate bot.

**Test 1 — ADMET screening:**
```
Screen aspirin and caffeine for ADMET properties including hERG and CYP3A4
```
Expected:
- Bot calls `biomate_query` (you may see "Calling tool..." indicator)
- Returns AI answer about ADMET workflow
- Includes "Run in BioMate" link: `https://app.biomate.ai?workflow=admet_screening`

**Test 2 — RNA-seq:**
```
I have RNA-seq data from 10 tumor and 10 normal samples. How do I find 
differentially expressed genes?
```
Expected: Explanation of nf-core/rnaseq or DESeq2 workflow + run link.

**Test 3 — CryoEM:**
```
Run single-particle analysis on my CryoEM MRC files
```
Expected: Mentions cryoSPARC or RELION workflow.

**Test 4 — multi-turn (session continuity):**
```
Turn 1: What is PBPK modeling?
Turn 2: Show me a workflow to run it for a small molecule drug
```
Expected: Turn 2 uses context from Turn 1 (session_id is preserved by the plugin).

**Test 5 — general question (no workflow card):**
```
What does the p-value mean in a DESeq2 result?
```
Expected: Text explanation, no workflow link (no matching workflow).

### Verify the plugin server received the calls

Check the plugin server logs:
```
POST /coze-plugin/query  200  0.8s
POST /coze-plugin/query  200  1.2s
```

You can also test the plugin HTTP API directly:
```bash
curl -s -X POST https://abc123.ngrok.io/coze-plugin/query \
  -H "Content-Type: application/json" \
  -H "X-BioMate-Plugin-Key: choose_a_random_secret_here" \
  -d '{"query": "Screen aspirin for ADMET properties"}' | python3 -m json.tool
```
Expected output:
```json
{
  "answer": "BioMate has identified the admet_screening workflow...",
  "workflow_name": "admet_screening",
  "view_url": "https://app.biomate.ai?workflow=admet_screening",
  "session_id": "550e8400-..."
}
```

### Troubleshooting

| Symptom | Fix |
|---|---|
| Coze console shows "server validation failed" | ngrok URL is wrong or plugin server not running; check `GET /coze-plugin/health` returns 200 |
| Bot doesn't call the plugin | Check the system prompt tells the bot to use `biomate_query`; check the plugin is added to the bot |
| Plugin returns 401 | `X-BioMate-Plugin-Key` header value in Coze console doesn't match `COZE_PLUGIN_SECRET` env var |
| Plugin returns empty `answer` | BioMate server isn't returning `delta` events; verify `curl` test against `/api/chat/stream` works |
| Bot calls plugin but ignores the result | Coze LLM sometimes reformulates — check "Plugin responses" in bot debug panel |
| ngrok tunnel expired | Free ngrok tunnels expire after 2 h; restart ngrok and update the plugin server URL in Coze console |

---

## Quick Reference: Test Queries by Use Case

These queries work on all four platforms:

| Use case | Test query |
|---|---|
| ADMET drug screening | `Screen aspirin (CC(=O)Oc1ccccc1C(=O)O) for hERG and CYP3A4` |
| RNA-seq | `Differential expression analysis on RNA-seq from human liver` |
| WGS variant calling | `Whole genome sequencing variant calling on GRCh38` |
| CryoEM | `Single-particle analysis on my CryoEM MRC files` |
| Protein structure | `Predict the 3D structure of this sequence: MKTIIALSYIFCLVFA` |
| Clinical trial design | `Design a Phase I dose-escalation trial for a small molecule` |
| PBPK modeling | `PBPK pharmacokinetic modeling for a lipophilic oral drug` |
| General question | `What is the difference between DESeq2 and edgeR?` |

---

## Running Unit Tests Locally

All connector unit tests use mock SSE servers — no real BioMate server needed.

```bash
cd /path/to/biomate-connectors-v2

# WeChat (11 tests)
python3 -m unittest backend.tests.test_wechat_open_claw -v

# Slack (33 tests)
python3 -m unittest tests.test_slack_bot -v

# Coze plugin (18 tests)
python3 -m unittest backend.tests.test_coze_plugin -v

# MCP E2E (requires live BioMate server + API key)
BIOMATE_API_URL=http://localhost:5000 \
BIOMATE_API_KEY=your_key_here \
python3 tests/test_mcp_e2e.py --search-only
```

Expected totals: **62 unit tests pass** (11 + 33 + 18).
