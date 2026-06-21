# BioMate × Telegram

> Drive real bioinformatics pipelines from Telegram — RNA-seq, CryoSPARC, ADMET, PBPK, AlphaFold — without leaving the chat.

中文用户请见 [README.zh-CN.md](./README.zh-CN.md).

## Setup

### 1. Create a bot

Message [@BotFather](https://t.me/BotFather) on Telegram:

```
/newbot
```

Follow the prompts and copy the **bot token** it gives you (looks like
`123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11`).

### 2. Configure environment

```bash
export TELEGRAM_BOT_TOKEN="123456:ABC-DEF..."
export BIOMATE_API_URL="https://api.biomate.ai"      # your BioMate API base
export BIOMATE_API_KEY="sk-..."                       # optional service-account fallback
export BIOMATE_DEEP_LINK_BASE="https://app.biomate.ai"
```

### 3. Run the bot

```bash
python connectors/telegram/telegram_bot.py --port 8092
```

Put it behind a public HTTPS URL (nginx / Cloudflare Tunnel / your platform).

### 4. Register the webhook

```bash
curl "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setWebhook" \
     -d "url=https://<your-domain>/connect/telegram/webhook"
```

Telegram will now POST every message to your bot.

## Use it

Open your bot in Telegram and bind your BioMate account once:

```
/bind sk-your-biomate-api-key
```

Then just ask:

```
Screen aspirin and caffeine for hERG and CYP3A4 inhibition
```

```
RNA-seq differential expression, treated vs control, GRCh38
```

```
Look up UniProt P04637
```

The bot replies with a markdown summary and, when a runnable workflow is
identified, a **Run in BioMate** button that deep-links into the app.

## Commands

| Command | What it does |
|---|---|
| `/start`, `/help` | Show help |
| `/bind <api-key>` | Link your BioMate account |
| `/unbind` | Remove the binding |
| `/clear` | Clear conversation history (start a new thread) |

## Environment variables

| Var | Required | Default | Notes |
|---|---|---|---|
| `TELEGRAM_BOT_TOKEN` | yes | — | From @BotFather |
| `BIOMATE_API_URL` | yes | `http://localhost:5000` | BioMate API base |
| `BIOMATE_API_KEY` | no | — | Service-account fallback if a user hasn't bound |
| `BIOMATE_DEEP_LINK_BASE` | no | `https://app.biomate.ai` | Used for the Run button |

## Notes

- Telegram caps a single message at 4096 chars; long replies are truncated with
  a link to view full results in BioMate.
- The binding store is in-memory (matches the reference connectors); replace
  with a persistent store for production.

## License

MIT.
