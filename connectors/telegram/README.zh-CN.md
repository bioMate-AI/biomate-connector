# BioMate × Telegram

> 在 Telegram 里跑真正的生物信息流程 —— RNA-seq、CryoSPARC、ADMET、PBPK、AlphaFold —— 不用切应用、不用学命令行。

For English see [README.md](./README.md).

## 配置

### 1. 创建机器人

在 Telegram 上给 [@BotFather](https://t.me/BotFather) 发消息：

```
/newbot
```

按提示操作，复制它给你的 **bot token**（形如
`123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11`）。

### 2. 设置环境变量

```bash
export TELEGRAM_BOT_TOKEN="123456:ABC-DEF..."
export BIOMATE_API_URL="https://api.biomate.ai"      # 你的 BioMate API 地址
export BIOMATE_API_KEY="sk-..."                       # 可选，服务账号兜底密钥
export BIOMATE_DEEP_LINK_BASE="https://app.biomate.ai"
```

### 3. 启动机器人

```bash
python connectors/telegram/telegram_bot.py --port 8092
```

部署到一个公网 HTTPS 地址（nginx / Cloudflare Tunnel / 你的平台）。

### 4. 注册 webhook

```bash
curl "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setWebhook" \
     -d "url=https://<你的域名>/connect/telegram/webhook"
```

之后 Telegram 会把每条消息 POST 到你的机器人。

## 试一下

在 Telegram 里打开你的机器人，先绑定一次 BioMate 账号：

```
/bind sk-你的-biomate-api-key
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

机器人会回 markdown 摘要；识别出可运行的流程时，会附一个 **在 BioMate 中运行** 按钮，直接跳进应用。

## 命令

| 命令 | 作用 |
|---|---|
| `/start`、`/help` | 显示帮助 |
| `/bind <api-key>` | 绑定 BioMate 账号 |
| `/unbind` | 解除绑定 |
| `/clear` | 清除对话历史（开始新对话） |

## 环境变量

| 变量 | 必填 | 默认值 | 说明 |
|---|---|---|---|
| `TELEGRAM_BOT_TOKEN` | 是 | — | 来自 @BotFather |
| `BIOMATE_API_URL` | 是 | `http://localhost:5000` | BioMate API 地址 |
| `BIOMATE_API_KEY` | 否 | — | 用户未绑定时的服务账号兜底 |
| `BIOMATE_DEEP_LINK_BASE` | 否 | `https://app.biomate.ai` | 用于「运行」按钮的深链 |

## 说明

- Telegram 单条消息上限 4096 字符；过长回复会被截断，并附上 BioMate 应用链接查看完整结果。
- 绑定关系存在内存里（与参考连接器一致）；生产环境请替换为持久化存储。

## 许可

MIT。
