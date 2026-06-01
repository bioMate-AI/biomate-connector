# Demo 6 — Open Claw / WeChat × BioMate (ADMET in Mandarin)

**Duration target:** 60 seconds
**Surface:** WeChat (iOS or Android, captured via QuickTime / Vysor)
**Workflow exercised:** ADMET (DeepPK) in Mandarin

## Pre-roll

- [ ] BioMate AI 公众号 followed on demo phone
- [ ] Account linked via `/connect <code>` already (don't re-record the linking step here — show that in a separate setup video)
- [ ] Phone screen recorder running

## Script

### Beat 1 (0:00–0:08)

Open WeChat → BioMate AI 公众号 → chat thread visible.

**Caption (in Chinese):** `微信里跑 ADMET — 不用切应用`
**Caption (English subs):** `ADMET in WeChat — no app switching`

### Beat 2 — Send (0:08–0:45)

Send message:

```
筛选 aspirin 和 caffeine 的 hERG 抑制和 CYP3A4 代谢
```

Bot responds within 3 seconds:

```
好的，我用 BioMate ADMET 流程跑这两个化合物 (workflow 12722).

▸ 化合物准备 ✓ 2 个 SMILES 已规范化
▸ ADMET 预测 ✓ DeepPK (hERG, CYP3A4)
▸ 质控门 ✓ 通过

结果:
| 化合物    | hERG IC50  | CYP3A4 比 | 状态 |
| Aspirin   | >100 μM    | 0.12      | ✓ 通过 |
| Caffeine  | 38.4 μM    | 0.04      | ✓ 通过 |

完整报告: https://biomate.ai/runs/run-cn-001
```

### Beat 3 — Payoff (0:45–1:00)

Tap the link → mobile-rendered biomate.ai/runs page opens in WeChat's built-in browser. Show the QC card, scroll to the methods report download.

**Caption (Chinese):** `12 页方法学报告 · 中文模板支持`
**Caption (English subs):** `12-page methods report · Chinese template available`

## Voiceover (Mandarin, with English subs)

> "BioMate Open Claw 让中国研究员在微信里直接跑真正的 ADMET 流程 —— AWS Batch 后端，QC 门自动，PDF 方法学报告完整。从来不用换应用。"
> *"BioMate Open Claw lets Chinese researchers run real ADMET pipelines directly from WeChat — AWS Batch backend, automatic QC gates, complete PDF methods report. Never leave the app."*
