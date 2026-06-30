# BioMate Connect — Test Plan & Usage Test Flow (Tester Guide)

**Date:** 2026-06-30 · **Test backend:** `https://test.stage-public.biomate.ai`
· 中文版：[20260630_TESTER_GUIDE.md](./20260630_TESTER_GUIDE.md)

This guide serves three kinds of testers — **non-technical (beginner),
engineer, expert** — each with its own clear path. Just follow your track; you
don't need to read the whole thing first.

> ⚠️ **Secret safety**: this doc uses placeholders like `<BIOMATE_API_KEY>`. The
> real key and Slack tokens are sent to you separately by the project owner —
> never put them in any shared doc, screenshot, or chat log.

---

## 0. Live entry points (as of 2026-06-30, all deployed and self-checked)

| Entry point (surface) | Audience | How to access | Install needed | Auth |
|---|---|---|---|---|
| **BioMate web app** | beginner / everyone | open `https://test.stage-public.biomate.ai` and log in | no | account login |
| **Slack** | beginner / everyone | type `/biomate …` in the `Biomate` workspace | no (already installed) | shared key (you don't manage it) |
| **Feishu / Lark** | beginner / everyone | DM the BioMate bot in Feishu | no (already installed) | per-user `bind <key>` |
| **ChatGPT custom GPT** | engineer / expert | build a Custom GPT on chatgpt.com and import the spec | yes (build a GPT) | Bearer key |
| **Claude Code (MCP)** | engineer / expert | drop a `.mcp.json` in the project, restart Claude Code | yes (config file) | local key |

> All 5 entry points sit on the **same** BioMate backend and the **same 17
> tools**. The only difference is *where* you use it and *how* it connects. So
> beginners verify "it works", engineers verify "it installs", experts verify
> "it's correct + consistent across surfaces".

---

## 1. Automated test plan (for engineers / CI)

Repo: `github.com/bioMate-AI/biomate-connector`. Full layer details in
[20260621_TEST_PLAN.md](./20260621_TEST_PLAN.md); API index in
[20260621_CONNECTOR_API_INDEX.md](./20260621_CONNECTOR_API_INDEX.md). Summary:

| Layer | Covers | Runtime | Command |
|---|---|---|---|
| **L1 unit** | per-surface adapters, tool manifest, OAuth | <30s | below |
| **L3 sandbox** | OpenAPI/JSON-Schema, SDK shapes | <1min | below |
| **L4 live routing** | real LLM picks the right tool (17/17 verified) | ~3min, ~$0.2 | below |
| **MCP E2E** | MCP server vs real backend, tools/list + tools/call | ~1min | below |

### Offline suites (should be green on every commit)

```bash
cd biomate-connector
PYTHONPATH=. python -m pytest \
  tests/test_{slack_bot,feishu_bot,telegram_bot,wechat_open_claw,chatgpt_connector,coze_plugin,tools_manifest}.py -q
# Expect: all green (a few ChatGPT live cases skip without a key)
```

> **Local env gotcha**: the OAuth suite needs `PyJWT`, the sandbox suite needs
> `jsonschema>=4.18`; anaconda's bundled versions are too old — install them.
> (Doesn't affect the surface suites above.)

### L4 live routing (needs a real key)

```bash
ANTHROPIC_API_KEY=sk-ant-… ANTHROPIC_BASE_URL= \
  PYTHONPATH=. python -m pytest tests/test_connector_live.py -k claude -v
# Expect: 17/17 tools route correctly
```

### MCP server against the real backend

```bash
BIOMATE_API_URL=https://test.stage-public.biomate.ai \
BIOMATE_API_KEY=<BIOMATE_API_KEY> \
  PYTHONPATH=. python -m pytest tests/test_mcp_e2e.py -v -s
# Expect: tools/list returns 17 tools; tools/call returns real data
```

---

## 2. Manual test flows — by audience

> Each scenario gives **what to do + expected result + pass criteria**. Treat
> "pass criteria" as a checkbox: ✅ if it passes, ❌ + screenshot if not. Report
> template in §3.

### 2A. Beginner (non-technical) — zero config, just use it

**Goal**: can an ordinary person use it smoothly and understand the results?
Pick **web app** or **Slack** (both is better).

#### Route 1: Web app (most natural)

| # | What you do | Expected | Pass? |
|---|---|---|---|
| 1 | open `https://test.stage-public.biomate.ai`, log in with the account you were given | chat interface loads | logs in, no error |
| 2 | type: `Screen aspirin for hERG and CYP3A4 safety` | reply starts within seconds: analysis + a runnable workflow card | clear text, has a result, has a "run" button |
| 3 | click the workflow card / "Confirm and run" | workflow panel opens, params pre-filled | navigates, shows params |
| 4 | type: `Look up UniProt P04637` | returns p53 protein info | real result, not an error |
| 5 | type: `What do these results mean?` (follow-up) | plain-language explanation of the previous step | coherent, keeps context |

#### Route 2: Slack (already installed, just type)

In the `Biomate` Slack workspace, any channel or a DM to the BioMate bot:

| # | Input | Expected | Pass? |
|---|---|---|---|
| 1 | `/biomate help` | help text appears | has help |
| 2 | `/biomate Screen aspirin CC(=O)Oc1ccccc1C(=O)O for hERG and CYP3A4` | "analyzing…" within 3s, then result + **Run in BioMate** button | **no garbled text**, has result + button |
| 3 | click **Run in BioMate** | browser opens the BioMate panel, workflow loaded | navigates |
| 4 | `/biomate Look up UniProt P04637` | returns p53 info | real result |

**A beginner only judges three things**: ① does it work ② is it understandable
③ is the text/layout normal (**any garbled characters?**).

---

### 2B. Engineer — wire up a surface + run the suites

**Goal**: it installs, connects, and the tools fire.

#### Task 1: connect BioMate into Claude Code (MCP)

1. In the `biomate-connector` project root, add a `.mcp.json` (**don't commit it
   — it has a key**):

   ```json
   {
     "mcpServers": {
       "biomate": {
         "command": "python",
         "args": ["<absolute-path>/mcp/biomate_mcp_server.py"],
         "env": {
           "BIOMATE_API_URL": "https://test.stage-public.biomate.ai",
           "BIOMATE_API_KEY": "<BIOMATE_API_KEY>"
         }
       }
     }
   }
   ```
   > The `python` in `command` must have `requests` installed (e.g. anaconda's).

2. **Restart Claude Code**, and **trust** the `biomate` MCP server when prompted.
3. Type `/mcp` — **expect: `biomate` connected, 17 tools**.
4. Have Claude use the tools: `use biomate to look up UniProt P04637` /
   `use biomate to screen aspirin for hERG`.

| Check | Pass? |
|---|---|
| `/mcp` shows biomate + 17 tools | ✅/❌ |
| read-only query (query_database) returns real data | ✅/❌ |
| natural language triggers biomate_session and returns a workflow | ✅/❌ |

#### Task 2: run the automated suites

Run the **offline surface suites + MCP E2E** from §1; record whether all green.

#### Task 3 (optional): stand up a surface locally

Follow the step-by-step in `connectors/slack/README.md` or
`connectors/feishu/README.md` to run a bot locally with Docker; verify the deploy
docs are accurate and the health check returns 200.

---

### 2C. Expert — scientific correctness + edges + cross-surface consistency

**Goal**: it's correct, handles errors, and is consistent across surfaces.

#### 1. Scientific correctness
- `Screen terfenadine for hERG risk` → should flag **high hERG risk** (a known
  cardiotoxic drug).
- `RNA-seq differential expression, treated vs control, GRCh38` → should route to
  an RNA-seq DE workflow.
- `Resolve accession GSE183947` → should route to a GEO data-fetch workflow.
- Judge: is the routed workflow **scientifically correct**, are the params sane?

#### 2. Edge / error cases
| Scenario | Expected |
|---|---|
| Invalid SMILES: `Screen XYZ123 for ADMET` | graceful error or asks to clarify, doesn't crash |
| Very long input (paste a big block) | no silent truncation / handled sanely |
| Cancel a run: start one, then `cancel that run` | calls cancel, status → CANCELLED |
| Mandarin prompt | replies in Chinese, **no mojibake** (key regression check) |
| Failure diagnosis: `my run failed, explain the error` | calls explain_error, gives root cause |

#### 3. Cross-surface consistency (same question, multiple entry points)
Ask the **same question** (e.g. `Look up UniProt P04637`) on **web / Slack /
ChatGPT GPT / Claude Code**:
- Judge: core results are **consistent** (same backend, same tools); only the
  layout/look differs by platform.

#### 4. Security (dev checks)
- Slack: replay a webhook with a wrong signature → expect **403**.
- Feishu: open an expired `/connect/feishu/go` link → expect an "expired" message
  and fallback to the app root.

---

## 3. Test record template (each tester fills in)

Copy the table, one row per scenario:

| Tester | Audience | Surface | Scenario# | Input | Expected | Actual | Pass? | Screenshot/notes |
|---|---|---|---|---|---|---|---|---|
| Alice | beginner | Slack | 2A-2 | /biomate Screen aspirin… | text+button | … | ✅/❌ | … |

**When reporting a bug, include**: ① which surface ② the full input ③ a full
screenshot of the reply ④ the timestamp. (With a timestamp we can pull the
matching backend logs.)

---

## 4. Known situations / NOT "connector bugs"

- **Workflow catalog**: the test backend's workflow catalog data may be
  incomplete; a workflow not being found / not producing a result is a **backend
  data state**, not a connector issue. Just note it in the report.
- **ChatGPT GPT**: requires ChatGPT Plus + building your own Custom GPT (import
  `connectors/chatgpt/openapi.test.json`, set auth to Bearer + the key you were
  given). An engineer/expert task.
- **Claude Code / Cursor / Codex / Desktop**: these four share the same MCP, so
  their **routing behaviour is identical** — no need to repeat the scientific
  scenarios on each; just verify "installs + connects" per client.
- **Auth model differences**: Slack is **single-tenant shared key** (no personal
  login); Feishu is **per-user `bind`**; web is account login. Tell beginners up
  front to avoid the "why doesn't Slack need a login?" confusion.

---

## 5. One-page cheat sheet (enough to hand a beginner)

> Open the **Biomate** Slack workspace and type any of the following; check that
> you get a **clear, non-garbled reply within seconds**:
>
> 1. `/biomate help`
> 2. `/biomate Screen aspirin CC(=O)Oc1ccccc1C(=O)O for hERG and CYP3A4`
> 3. `/biomate Look up UniProt P04637`
>
> Or open `https://test.stage-public.biomate.ai`, log in, and just ask in plain
> language. Anything you can't understand, any error, garble, or unclickable
> button — **screenshot + write down what you typed** and send it to us.

(See also the standalone [BEGINNER_CHEATSHEET.md](./BEGINNER_CHEATSHEET.md).)
