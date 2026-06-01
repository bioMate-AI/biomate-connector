# BioMate 微信 (Service Account)

> 在微信里跑真正的生物信息流程 —— RNA-seq, CryoSPARC, ADMET, PBPK, AlphaFold —— 不用切应用、不用学命令行。

## 安装（一键）

```bash
npx @biomate/connect open-claw
```

终端会打印一个连接码。在微信里搜索 **BioMate AI (生物伙伴)** 公众号，关注后发送：

```
/connect <code>
```

完成账号绑定。

## 试一下

```
筛选 aspirin 和 caffeine 的 hERG 和 CYP3A4 抑制
```

```
对 s3://biomate-demo/rnaseq/ 跑 nf-core/rnaseq, treated vs control, GRCh38
```

```
查询 UniProt P04637
```

机器人会回 markdown 摘要和 biomate.ai 上的实时运行链接。长流程结束时会推一条微信消息通知。

## 工具集

和 Claude Code 一样的 14 个工具 —— 见 [`../claude-code/README.md`](../claude-code/README.md)。

## 解绑

发送 `/disconnect` 给机器人，或在 https://biomate.ai/account/connectors 撤销 **WeChat / Open Claw** 授权。

## 许可

MIT。
