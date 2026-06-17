# BioMate × Slack

> Run real bioinformatics from your lab's Slack workspace. `/biomate run …` kicks off a workflow on BioMate cloud; the bot threads progress and posts the final report.

## Install

Click **Add to Slack** at https://biomate.ai/connectors/slack/install — it triggers Slack's standard OAuth flow and asks for the channel scopes BioMate needs (`chat:write`, `commands`, `files:write`).

After install, each user runs `/biomate login` in any channel where the app is present. That posts a one-tap OAuth link tying the Slack user to their BioMate account.

## Try it

```
/biomate run RNA-seq pipeline on s3://biomate-demo/rnaseq/, treated vs control, GRCh38
```

```
/biomate screen aspirin caffeine ibuprofen for hERG and CYP3A4
```

```
/biomate recall — what did Sarah's CRISPR screen find last quarter?
```

The bot replies in-thread with phase/step updates as the workflow runs, and posts the final methods report as a PDF attachment.

## How it differs from the CLI surfaces

- **Multi-user**: each Slack user maps to a BioMate user; the workflow appears in their account, not the workspace owner's.
- **Channel-scoped**: workflows tagged with the channel name; findings searchable per channel.
- **No streaming**: Slack doesn't support MCP `notifications/progress`. The bot polls and edits the thread message in place every 30 seconds.

## Tools available

The Slack bot exposes the same 14-tool BioMate surface — see [`../claude-code/README.md`](../claude-code/README.md). Plus four Slack-specific slash subcommands:

| Command | Maps to |
|---|---|
| `/biomate run <prompt>` | `biomate_session` (agentic) |
| `/biomate search <query>` | `search_workflow` |
| `/biomate runs` | `list_runs` filtered to the current channel |
| `/biomate recall <topic>` | `recall_memory` |

## Disconnect

```
/biomate logout
```

Or revoke from the workspace at https://biomate.ai/account/connectors → Slack.

## License

MIT.
