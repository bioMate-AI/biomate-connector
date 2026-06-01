# BioMate × Claude Desktop

> Run real bioinformatics from Claude Desktop. Same OAuth, same MCP tools, same streaming workflow runs as our Claude Code integration — in the desktop UI.

## Install (30 seconds)

```bash
npx @biomate/connect claude-desktop
```

Then quit and reopen Claude Desktop. You'll see the BioMate tools in the MCP indicator.

## Config locations (if installing manually)

| OS | Path |
|---|---|
| macOS | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Windows | `%APPDATA%\Claude\claude_desktop_config.json` |
| Linux | `~/.config/Claude/claude_desktop_config.json` |

Drop in [`mcp-snippet.json`](./mcp-snippet.json) (merging with existing `mcpServers`) and replace the refresh token from https://biomate.ai/account/connectors/claude-desktop.

## Try it

Same prompts as Claude Code. Claude Desktop's chat surface renders thumbnails, so QC gate cards and finding plots show inline:

```
Run ADMET screening on these SMILES and show the hERG / DILI gate results:
  CC(=O)Oc1ccccc1C(=O)O
  CN1C=NC2=C1C(=O)N(C(=O)N2C)C
  CC1=C(C=C(C=C1)NC(=O)CCl)C(=O)O
```

## Tools, scopes, security

See [`../claude-code/README.md`](../claude-code/README.md) — identical tool surface.

## License

MIT.
