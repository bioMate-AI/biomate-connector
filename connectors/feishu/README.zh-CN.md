# BioMate × 飞书 / Lark

> 在飞书 / Lark 里跑真正的生物信息流程 —— RNA-seq、CryoSPARC、ADMET、PBPK、AlphaFold —— 不用切应用、不用学命令行。

For English see [README.md](./README.md).

## 配置

### 1. 创建应用

打开飞书 / Lark 开放平台：

- 飞书（中国）：https://open.feishu.cn/
- Lark（国际）：https://open.larksuite.com/

创建一个**自建应用**，然后：

1. **开启机器人能力**（应用功能 → 机器人）。
2. **权限范围**：添加 `im:message` 和 `im:message:send_as_bot`。
3. **事件订阅**：把请求地址设为
   `https://<你的域名>/connect/feishu/webhook`，并订阅
   **`im.message.receive_v1`**。
4. 复制应用的 **App ID**、**App Secret** 和 **Verification Token（验证令牌）**。

> **Encrypt Key（加密密钥）：** 本连接器**未**实现加密模式解密。请**关闭**
> Encrypt Key，或在 `handle_event` 之前自行加一段 WBizMsgCrypt 等价的解密步骤。

### 2. 设置环境变量

```bash
export FEISHU_APP_ID="cli_..."
export FEISHU_APP_SECRET="..."
export FEISHU_VERIFY_TOKEN="..."
export FEISHU_BASE="https://open.feishu.cn"          # Lark: https://open.larksuite.com
export BIOMATE_API_URL="https://api.biomate.ai"
export BIOMATE_API_KEY="sk-..."                       # 可选，服务账号兜底密钥
export BIOMATE_DEEP_LINK_BASE="https://app.biomate.ai"
```

### 3. 启动机器人

```bash
python connectors/feishu/feishu_bot.py --port 8093
```

部署到一个公网 HTTPS 地址。在开放平台保存事件请求地址时，飞书会发来一个
`url_verification` challenge —— 机器人会自动回显。

## 试一下

在飞书里私聊机器人（或在群里 @ 它），先绑定一次账号：

```
bind sk-你的-biomate-api-key
```

然后直接问：

```
筛选 aspirin 和 caffeine 的 hERG 和 CYP3A4 抑制
```

```
RNA-seq 差异表达分析，treated vs control，GRCh38
```

```
查询 UniProt P04637
```

机器人会回文字摘要；识别出可运行的流程时，会附一张带 **在 BioMate 中运行**
按钮的交互卡片。

## 命令

| 命令 | 作用 |
|---|---|
| `help`、`帮助` | 显示帮助 |
| `bind <api-key>` | 绑定 BioMate 账号 |
| `unbind` | 解除绑定 |
| `clear`、`清除` | 清除对话历史 |

## 环境变量

| 变量 | 必填 | 默认值 | 说明 |
|---|---|---|---|
| `FEISHU_APP_ID` | 是 | — | 开放平台应用 App ID |
| `FEISHU_APP_SECRET` | 是 | — | App Secret |
| `FEISHU_VERIFY_TOKEN` | 是 | — | 验证令牌；校验入站事件 |
| `FEISHU_BASE` | 否 | `https://open.feishu.cn` | Lark 用 `https://open.larksuite.com` |
| `BIOMATE_API_URL` | 是 | `http://localhost:5000` | BioMate API 地址 |
| `BIOMATE_API_KEY` | 否 | — | 用户未绑定时的服务账号兜底 |
| `BIOMATE_DEEP_LINK_BASE` | 否 | `https://app.biomate.ai` | 用于「运行」按钮的深链 |

## 说明

- 飞书在非 2xx 时会重试事件，所以 webhook 会立刻返回 200 并异步回复；重复的
  `message_id` 会被去重。
- 绑定关系存在内存里（与参考连接器一致）；生产环境请替换为持久化存储。

## 许可

MIT。
