# BioMate Connect — 测试方案 & 使用测试流程（Tester Guide）

**日期:** 2026-06-30 · **测试后端:** `https://test.stage-public.biomate.ai`
· English: [20260630_TESTER_GUIDE.en.md](./20260630_TESTER_GUIDE.en.md)
· 小白速查单页: [BEGINNER_CHEATSHEET.md](./BEGINNER_CHEATSHEET.md)

这份文档面向三类测试人员 —— **小白（不懂技术）、工程师、专家** —— 每类有自己的
一条清晰路径。照着做即可，不需要先读懂全部内容。

> ⚠️ **密钥安全**：本文用 `<BIOMATE_API_KEY>` 这样的占位符。真实 key、Slack token
> 由项目负责人单独发给你，**不要写进任何公开文档、截图、聊天记录**。

---

## 0. 当前可测的入口（截至 2026-06-30，均已上线并自检通过）

| 入口（Surface） | 适合人群 | 怎么进 | 是否要安装 | 认证方式 |
|---|---|---|---|---|
| **网页版 BioMate** | 小白 / 所有人 | 浏览器打开 `https://test.stage-public.biomate.ai` 登录 | 否 | 账号登录 |
| **Slack** | 小白 / 所有人 | Slack 工作区 `Biomate` 里输入 `/biomate …` | 否（已装好） | 共享 key（你无需管） |
| **飞书 / Feishu** | 小白 / 所有人 | 飞书里给 BioMate 机器人发消息 | 否（已装好） | 各自 `bind <key>` |
| **ChatGPT 自定义 GPT** | 工程师 / 专家 | 在 chatgpt.com 建一个 Custom GPT 导入 spec | 是（建 GPT） | Bearer key |
| **Claude Code（MCP）** | 工程师 / 专家 | 项目里放 `.mcp.json` 后重启 Claude Code | 是（配置文件） | 本地 key |

> 这 5 个入口背后是**同一个** BioMate 后端和**同一套 17 个工具**。差别只在“在哪用”
> 和“怎么连”。所以小白验“能用”，工程师验“能装”，专家验“算得对 + 各端一致”。

---

## 1. 自动化测试方案（给工程师 / CI）

代码仓库：`github.com/bioMate-AI/biomate-connector`。完整分层细节见
[20260621_TEST_PLAN.md](./20260621_TEST_PLAN.md)；接口清单见
[20260621_CONNECTOR_API_INDEX.md](./20260621_CONNECTOR_API_INDEX.md)。摘要：

| 层 | 覆盖 | 跑一次 | 命令 |
|---|---|---|---|
| **L1 单元** | 各 surface 适配器、工具 manifest、OAuth | <30s | 见下 |
| **L3 沙盒** | OpenAPI/JSON-Schema、SDK 形状 | <1min | 同下 |
| **L4 实时路由** | 真 LLM 选对工具（17/17 已验证） | ~3min、约 $0.2 | 见下 |
| **MCP E2E** | MCP server 连真后端、tools/list + tools/call | ~1min | 见下 |

### 离线套件（每次提交都该绿）

```bash
cd biomate-connector
# 各 surface 适配器 + manifest（最关心的）
PYTHONPATH=. python -m pytest \
  tests/test_{slack_bot,feishu_bot,telegram_bot,wechat_open_claw,chatgpt_connector,coze_plugin,tools_manifest}.py -q
# 期望：全绿（ChatGPT 有几个 live 用例在没 key 时 skip）
```

> **本机环境坑**：OAuth 套件需要 `PyJWT`、沙盒套件需要 `jsonschema>=4.18`，
> anaconda 自带版本偏旧会报错——装上即可（不影响上面这条 surface 套件）。

### L4 实时路由（需真 key）

```bash
ANTHROPIC_API_KEY=sk-ant-… ANTHROPIC_BASE_URL= \
  PYTHONPATH=. python -m pytest tests/test_connector_live.py -k claude -v
# 期望：17 个工具 17/17 路由正确
```

### MCP server 连真后端

```bash
BIOMATE_API_URL=https://test.stage-public.biomate.ai \
BIOMATE_API_KEY=<BIOMATE_API_KEY> \
  PYTHONPATH=. python -m pytest tests/test_mcp_e2e.py -v -s
# 期望：tools/list 返回 17 个工具；tools/call 返回真实数据
```

---

## 2. 人工测试流程 —— 按人群

> 每个场景给了**要做什么 + 期望结果 + 通过判定**。把“通过判定”那一栏当复选框，
> 通过打 ✅、不通过打 ❌ 并截图。报告模板见第 3 节。

### 2A. 小白（不懂技术）—— 零配置，直接用

**目标**：验证“一个普通人能不能顺畅地用起来、看得懂结果”。挑 **网页版** 或 **Slack**
任一即可（两个都试更好）。

#### 路线一：网页版（最自然）

| # | 你做什么 | 期望结果 | 通过判定 |
|---|---|---|---|
| 1 | 浏览器打开 `https://test.stage-public.biomate.ai`，用发给你的账号登录 | 进入聊天界面 | 能登录、不报错 |
| 2 | 输入：`筛选阿司匹林的 hERG 和 CYP3A4 安全性` | 几秒内开始回复，给出分析 + 一个可运行的 workflow 卡片 | 文字清楚、有结果、有“运行”按钮 |
| 3 | 点那个 workflow 卡片 / “Confirm and run” | 进入 workflow 面板，参数已填好 | 能跳转、能看到参数 |
| 4 | 输入：`查询 UniProt P04637` | 返回 p53 蛋白的信息 | 有真实结果、不是报错 |
| 5 | 输入：`这些结果是什么意思？`（追问） | 用大白话解释上一步结果 | 能理解、上下文连贯 |

#### 路线二：Slack（已装好，直接打命令）

在 Slack 工作区 `Biomate`，任意频道或私聊 BioMate 机器人：

| # | 输入 | 期望结果 | 通过判定 |
|---|---|---|---|
| 1 | `/biomate help` | 弹出帮助说明 | 有帮助文字 |
| 2 | `/biomate Screen aspirin CC(=O)Oc1ccccc1C(=O)O for hERG and CYP3A4` | 3 秒内“正在分析”，随后给出结果 + **Run in BioMate** 按钮 | **文字不乱码**、有结果、有按钮 |
| 3 | 点 **Run in BioMate** 按钮 | 浏览器打开 BioMate 面板，workflow 已加载 | 能跳转 |
| 4 | `/biomate 查询 UniProt P04637` | 返回 p53 信息 | 有真实结果 |

**小白只需判断三件事**：① 能不能用起来 ② 看不看得懂 ③ 文字/排版正不正常（**有没有乱码**）。

---

### 2B. 工程师 —— 接入一个 surface + 跑自动化

**目标**：验证“装得上、连得通、工具能调”。

#### 任务一：把 BioMate 接进 Claude Code（MCP）

1. 在 `biomate-connector` 项目根目录放一个 `.mcp.json`（**别提交，含 key**）：

   ```json
   {
     "mcpServers": {
       "biomate": {
         "command": "python",
         "args": ["<绝对路径>/mcp/biomate_mcp_server.py"],
         "env": {
           "BIOMATE_API_URL": "https://test.stage-public.biomate.ai",
           "BIOMATE_API_KEY": "<BIOMATE_API_KEY>"
         }
       }
     }
   }
   ```
   > `command` 用的 python 必须装了 `requests`（如 anaconda 的 python）。

2. **重启 Claude Code**，弹窗里**信任** `biomate` 这个 MCP server。
3. 输入 `/mcp` —— **期望：`biomate` 已连接、17 个工具**。
4. 让 Claude 用工具：`用 biomate 查 UniProt P04637` / `用 biomate 筛选 aspirin 的 hERG`。

| 检查点 | 通过判定 |
|---|---|
| `/mcp` 显示 biomate + 17 工具 | ✅/❌ |
| 只读查询（query_database）返回真实数据 | ✅/❌ |
| 自然语言能触发 biomate_session 并给出 workflow | ✅/❌ |

#### 任务二：跑自动化套件

按第 1 节的命令跑 **离线 surface 套件 + MCP E2E**，记录是否全绿。

#### 任务三（可选）：本地起一个 surface

照 `connectors/slack/README.md` 或 `connectors/feishu/README.md` 的 step-by-step，
本地用 Docker 起一个 bot，验证部署文档是否准确、健康检查是否 200。

---

### 2C. 专家 —— 科学正确性 + 边界 + 跨端一致

**目标**：验证“算得对、扛得住异常、各端结果一致”。

#### 1. 科学正确性
- `筛选特非那定 (terfenadine) 的 hERG 风险` → 应识别出 **hERG 高风险**（已知心脏毒性药）。
- `RNA-seq 差异表达，treated vs control，GRCh38` → 应路由到 RNA-seq DE 类 workflow。
- `解析 accession GSE183947` → 应路由到 GEO 数据获取 workflow。
- 判定：路由的 workflow 是否**科学上正确**、参数是否合理。

#### 2. 边界 / 异常
| 场景 | 期望 |
|---|---|
| 非法 SMILES：`筛选 XYZ123 的 ADMET` | 优雅报错或要求澄清，不崩 |
| 超长输入（贴一大段文字） | 不截断丢内容 / 有合理处理 |
| 取消运行：开一个 run 后 `取消那个 run` | 调 cancel，状态变 CANCELLED |
| 中文 prompt | 中文回复、**无乱码**（重点回归项） |
| 失败诊断：`我的 run 失败了，解释下错误` | 调 explain_error，给根因 |

#### 3. 跨端一致性（同一问题，多个入口）
对**同一个问题**（如 `查询 UniProt P04637`）分别在 **网页 / Slack / ChatGPT GPT /
Claude Code** 各问一遍：
- 判定：核心结果**一致**（同一后端、同一工具），只是排版/外观因平台不同。

#### 4. 安全（dev 验证）
- Slack：用错误签名重放 webhook → 期望 **403**。
- 飞书：用过期的 `/connect/feishu/go` 链接 → 期望提示过期、回落首页。

---

## 3. 测试记录模板（每位测试者填）

复制下表，每个场景一行：

| 测试者 | 人群 | 入口 | 场景# | 输入 | 期望 | 实际 | 通过? | 截图/备注 |
|---|---|---|---|---|---|---|---|---|
| 张三 | 小白 | Slack | 2A-2 | /biomate Screen aspirin… | 文字+按钮 | … | ✅/❌ | … |

**报 bug 时请附**：① 哪个入口 ② 完整输入 ③ 完整回复截图 ④ 时间点。
（有时间点我们能去后端拉对应日志定位。）

---

## 4. 已知情况 / 不是“连接器 bug”的问题

- **workflow 目录**：test 后端的 workflow 目录数据可能不全；某些 workflow 找不到/跑不出
  结果属于**后端数据状态**，不是连接器问题。报告时注明即可。
- **ChatGPT GPT**：需要 ChatGPT Plus + 自己建 Custom GPT（导入 `connectors/chatgpt/openapi.test.json`，
  认证填 Bearer + 你拿到的 key）。属于工程师/专家任务。
- **Claude Code / Cursor / Codex / Desktop**：四个是同一套 MCP，**路由行为完全一致**——
  不必每个都重复测科学场景，各自验“装得上 + 能连”即可。
- **认证模型差异**：Slack 是**单租户共享 key**（无个人登录）；飞书是**个人 `bind`**；
  网页是账号登录。给小白时说清楚，避免“为什么 Slack 不用登录”的困惑。

---

## 5. 一页速查（发给小白就够）

> 打开 Slack 工作区 **Biomate**，输入下面任意一条，看是否**几秒内有清晰、不乱码的回复**：
>
> 1. `/biomate help`
> 2. `/biomate Screen aspirin CC(=O)Oc1ccccc1C(=O)O for hERG and CYP3A4`
> 3. `/biomate 查询 UniProt P04637`
>
> 或打开 `https://test.stage-public.biomate.ai` 登录后直接用中文提问。
> 有任何看不懂、报错、乱码、点不动的地方 —— **截图 + 写下你输入了什么**，发给我们。
