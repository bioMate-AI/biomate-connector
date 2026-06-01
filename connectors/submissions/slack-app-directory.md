# Slack App Directory — submission package (deferred until pilot wraps)

Target: https://api.slack.com/apps/{app-id}/general → "Submit to App Directory"

**Status:** Submission deferred — currently in private pilot with 3 labs. Public submission targeted ~2 weeks post-launch.

## App metadata

| Field | Value |
|---|---|
| App name | BioMate AI |
| Short description (140) | Run real bioinformatics workflows (nf-core, CryoSPARC, AlphaFold, ADMET) from Slack. AWS Batch backend, QC gates, methods reports. |
| Long description | See [`description-long.md`](./description-long.md) |
| Category | Productivity → Workflows |
| Pricing | Free for individual use; usage-based platform fee on biomate.ai |
| Install URL | https://biomate.ai/connectors/slack/install |
| Privacy policy | https://biomate.ai/legal/privacy |

## Slash commands

| Command | Description | Usage hint |
|---|---|---|
| /biomate | Run a BioMate workflow | /biomate <natural-language goal> |
| /biomate run | Start a workflow | /biomate run <prompt> |
| /biomate search | Search the catalog | /biomate search <query> |
| /biomate runs | List recent runs | /biomate runs [--mine] |
| /biomate recall | Recall prior runs/findings | /biomate recall <topic> |
| /biomate login | Connect your BioMate account | /biomate login |
| /biomate logout | Disconnect | /biomate logout |

## Bot scopes

- `commands` (slash commands)
- `chat:write` (post results)
- `chat:write.public` (post in channels without invite)
- `files:write` (upload methods PDFs)
- `users:read` (map Slack user → BioMate user)
- `users:read.email` (link by verified email if needed)

## Event subscriptions

- `app_mention` (for `@biomate <prompt>` in threads)
- `message.channels` (read replies in BioMate threads for follow-up turns)

## Pre-submission checklist

- [ ] Pilot wrapped with 3 labs, no open P0 bugs
- [ ] OAuth installation flow works on workspaces with strict approval
- [ ] Privacy policy section addresses Slack data handling
- [ ] Demo workspace prepared for Slack reviewers
- [ ] No race conditions when 5 users in same channel trigger workflows simultaneously
