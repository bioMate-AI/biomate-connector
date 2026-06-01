# BioMate × Open Claw (WeChat)

> Open Claw is BioMate's hosted webhook bridge for WeChat — so Chinese researchers can run real bioinformatics from inside the chat app they already live in.

## Install (one click)

```bash
npx @biomate/connect open-claw
```

This links your BioMate account to the Open Claw service; there's nothing to install locally. After linking:

1. In WeChat, search for the official account **BioMate AI (生物伙伴)** or scan the QR code at https://biomate.ai/wechat
2. Follow the account
3. Send `/connect` and paste the code shown in your terminal after `@biomate/connect open-claw` ran

## Try it (in WeChat)

```
筛选 aspirin 和 caffeine 的 hERG 和 CYP3A4 抑制
```

```
对 s3://biomate-demo/rnaseq/ 跑 nf-core/rnaseq, treated vs control, GRCh38
```

```
查询 UniProt P04637
```

Open Claw responds with a markdown summary and a deep link to the live run on biomate.ai. For long-running pipelines you'll get a push notification when a phase finishes or a QC gate fires.

## How it works

```
   WeChat  ─→  Tencent Cloud  ─→  Open Claw webhook  ─→  BioMate API
                                          │
                                          ↓
                                  per-user OAuth token
                                  (mapped from WeChat openid)
```

- Each WeChat user is mapped to a BioMate user via the `/connect` flow.
- Conversational context is buffered server-side (WeChat doesn't preserve it).
- BioMate's `biomate_session` events are batched and pushed as WeChat messages.

## Security

- WeChat messages are HMAC-verified per [Tencent's spec](https://developers.weixin.qq.com/doc/offiaccount/Basic_Information/Access_Overview.html)
- BioMate tokens are stored encrypted, keyed by `openid + appid`
- The `/connect` code is single-use, 5-minute TTL

## Tools

Same 14-tool BioMate surface. Markdown summaries and deep links only — Open Claw drops thumbnails (WeChat image renders are inconsistent across clients).

## Disconnect

Send `/disconnect` to the Open Claw bot, or visit https://biomate.ai/account/connectors and revoke the **Open Claw** grant.

## License

MIT.
