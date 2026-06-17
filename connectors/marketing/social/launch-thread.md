# Launch day social — drafts

## X / Twitter thread (8 posts)

**1/** Run real bioinformatics from Claude Code, Cursor, ChatGPT, Codex, and WeChat.

Today we're launching BioMate Connectors — one OAuth, six surfaces. RNA-seq/WGS, CryoSPARC, AlphaFold, ADMET, PBPK — all from your favorite AI assistant.

🧵 ↓

[Attach: 60s montage video]

**2/** The problem we set out to solve:

AI assistants today can *write code* about bioinformatics.

They can't run it.

They can't pick the right workflow from 2,455 indexed pipelines, fill the params, submit to BioMate cloud, stream phase + QC + finding events back, and hand you a methods PDF.

BioMate can. Now from anywhere.

**3/** Install in 30 seconds:

```
npx @biomate/connect claude-code
```

Substitute `cursor`, `codex`, `claude-desktop`, `open-claw`, or one-click the ChatGPT GPT.

OAuth 2.1 + PKCE. Per-surface scopes. Revocable individually.

**4/** What you can actually ask:

"Screen aspirin and caffeine for hERG and CYP3A4."
→ ADMET pipeline runs on Batch, QC gate, methods PDF. 35s. ~$0.05.

[Attach: Demo 1 video, 60s]

**5/** "Run RNA-seq pipeline DE: treated vs control, GRCh38."
→ STAR + salmon + DESeq2 on 6 samples. ~5 min. ~$0.40. Top-20 DE table inline.

[Attach: Demo 2 video, 75s]

**6/** "Refine this CryoSPARC particle stack."
→ Homo refinement, GPU on Batch, FSC 0.143 at 3.2 Å.

[Attach: Demo 3 video, 90s]

**7/** The technical bit:

- 14-tool MCP surface, single source of truth in tools_manifest.py
- One agentic streaming tool (biomate_session)
- ChatGPT OpenAPI generated from the same manifest
- CI drift test fails the build if surfaces diverge
- Open source: github.com/bioMate-AI/biomate-connectors

**8/** This is the start. Slack app is in pilot with 3 labs (public in ~2 weeks). We're prioritizing whatever surface our researchers ask for next.

Try it: npx @biomate/connect <your favorite>
Read more: biomate.ai/connectors
Find a bug: file an issue, we'll fix it.

---

## LinkedIn (single post, 1500 chars max)

**Run real bioinformatics from Claude, Cursor, ChatGPT, Codex — without leaving your editor.**

Today we're launching BioMate Connectors: six integrations that let any AI assistant run real pipelines — RNA-seq/WGS, CryoSPARC, AlphaFold, ADMET, PBPK — on the same BioMate cloud backend that powers biomate.ai.

One OAuth flow. Same MCP tool surface across surfaces. Six places to call it from:

→ Claude Code
→ Claude Desktop
→ Cursor
→ OpenAI Codex CLI
→ ChatGPT (Custom GPT)
→ WeChat (Open Claw)

Slack is in pilot with 3 labs.

The difference from a generic "AI for science" tool: BioMate doesn't write you a pipeline config and stop. It picks the right workflow from 2,455 indexed pipelines, fills the parameters, submits to the execution engine, streams progress back, runs QC gates, and produces an IND/CRO-ready methods PDF.

Install in 30 seconds:
`npx @biomate/connect claude-code`

Read more: biomate.ai/connectors
Code: github.com/bioMate-AI/biomate-connectors

---

## Hacker News (Show HN — single post)

**Title:** Show HN: BioMate Connectors – Run real bioinformatics from Claude, Cursor, ChatGPT

**Text:**

Hey HN — we just shipped a set of MCP-based connectors that let you run real bioinformatics workflows (RNA-seq/WGS, CryoSPARC, AlphaFold, ADMET, PBPK) from Claude Code, Claude Desktop, Cursor, Codex CLI, ChatGPT (Custom GPT Action), and WeChat.

We built BioMate over the last 14 months as an execution engine — 2,455 indexed workflows across 34 biological domains, running on BioMate cloud with GPU queues, auto-loop QC gates, and FDA-formatted methods report generation. Until today the only way to use it was biomate.ai. Today we're opening it up to anywhere you already work.

A few things that turned out tricky and we'd love feedback on:

1. **Single source of truth for tool schemas.** OpenAPI for ChatGPT, MCP JSON for Claude/Cursor/Codex, a custom format for Open Claw. Drift is the enemy. We made the Python manifest in `backend/lib/mcp/tools_manifest.py` canonical and generate everything else from it, with a CI drift test that fails if they diverge.

2. **Streaming over MCP for hosts that support it.** `biomate_session` returns `notifications/progress` with `summary_md`, `view_url`, `thumbnail_png_b64`, and a structured `delta`. For surfaces that don't support progress (Codex, Slack, WeChat) we expose a paired `poll_run` that returns the same payloads — same backend code path.

3. **OAuth 2.1 + PKCE for public clients.** Per-surface scopes, individually revocable. Refresh tokens hashed at rest (HMAC-SHA256), rotated on use. Access tokens are 30-minute HS256 JWTs. Built standalone in ~600 lines of Python with 7 passing tests.

4. **Resisting the temptation to ship 50 tools.** We did the audit; 96% of our REST endpoints were "exposed" but unusable from chat surfaces. Settled on 14 tools across 3 tiers: one agentic (`biomate_session`), six workflow primitives, seven outputs/analysis/reporting.

Repo: https://github.com/bioMate-AI/biomate-connectors
Architecture doc: https://github.com/bioMate-AI/biomate-connectors/blob/main/docs/20260513_CONNECTOR_ARCHITECTURE_V2.md
Try: `npx @biomate/connect claude-code`

Happy to answer questions about anything — especially the hard parts of mapping a workflow-execution backend to chat surfaces with varying levels of rich rendering.

---

## WeChat / 知乎 (Chinese launch — for Open Claw)

**标题：** BioMate 微信生信助手上线 —— 在微信里跑真的 RNA-seq/WGS, CryoSPARC / ADMET 流程

**正文：**

今天 BioMate AI 正式上线**微信连接器（Open Claw）**。中国的研究员现在不用切换应用，直接在微信里就能跑真正的生物信息学流程。

**能做什么？**

- ADMET 筛选（hERG, CYP3A4 等）
- RNA-seq pipeline 差异表达分析
- WGS variant-calling pipeline 全基因组变异检测
- CryoSPARC 颗粒重构
- AlphaFold / ESMFold 蛋白结构预测
- PBPK 模拟、BOIN 临床剂量爬升
- IND §2.6.1 临床前药理学叙述自动生成

后台跑在 BioMate cloud（含 GPU 队列），自动质控门，输出 FDA 格式的方法学报告 PDF（中文模板可选）。

**怎么开始？**

1. 在终端跑: `npx @biomate/connect open-claw`
2. 关注微信公众号「BioMate AI 生物伙伴」
3. 发送 `/connect <code>` 完成绑定
4. 试一下：

```
筛选 aspirin 的 hERG 抑制和 CYP3A4 代谢
对 s3://biomate-demo/rnaseq/ 跑 RNA-seq pipeline, treated vs control, GRCh38
```

**和其他 AI 助手什么区别？**

通用 AI 助手只能告诉你*怎么*跑 ADMET 筛选 —— 写一段 pipeline config，然后停在那里。

BioMate **直接帮你跑**。从 2,455 个索引流程里挑出对应的一个，自动填参数，提交到 BioMate cloud，实时流式返回 phase + QC + finding 事件，最后给你一份方法学报告 PDF。

**安全？**

OAuth 2.1 + PKCE。每个连接独立授权、独立撤销。WeChat 消息符合腾讯 HMAC 验签规范。Token 在数据库里加密存储（HMAC-SHA256 哈希）。

完整文档: https://github.com/bioMate-AI/biomate-connectors/tree/main/connectors/open-claw

—— BioMate AI 团队
