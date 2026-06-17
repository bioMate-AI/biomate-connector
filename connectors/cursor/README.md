# BioMate × Cursor

> Run real bioinformatics from Cursor's chat. RNA-seq, CryoSPARC, ADMET, PBPK, AlphaFold — alongside your code.

## Install (30 seconds)

```bash
npx @biomate/connect cursor
```

Then **restart Cursor**. The BioMate tools appear in the MCP servers panel (Settings → Features → Model Context Protocol).

## Try it

```
@biomate Screen the SMILES in compounds.smi for hERG and CYP3A4 inhibition.
```

```
@biomate Run WGS variant-calling pipeline WGS variant calling on samples in s3://biomate-demo/wgs/run-2026-05/
against GRCh38. Use the standard GATK best-practices configuration.
```

```
@biomate Refine this cryo-EM particle stack — s3://biomate-demo/cryo/particles.cs — with
CryoSPARC homogeneous refinement. C2 symmetry.
```

## What Cursor does well with BioMate

Cursor's chat surface is great for the **research loop**:

1. Open a notebook or script
2. Ask BioMate to run an analysis pipeline
3. Get the result file URLs back in chat
4. Right-click → "Add to Context" so the file feeds your next prompt
5. Iterate on plot scripts or downstream analysis with the result already loaded

The agentic `biomate_session` tool streams workflow events into the chat panel, so you see phase + step progress live without leaving Cursor.

## Manual config

`~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "biomate": {
      "command": "npx",
      "args": ["-y", "@biomate/mcp-server"],
      "env": {
        "BIOMATE_API_BASE": "https://api.biomate.ai",
        "BIOMATE_REFRESH_TOKEN": "<your-refresh-token>"
      }
    }
  }
}
```

Get the refresh token at https://biomate.ai/account/connectors/cursor.

## Tools & scopes

Identical to the Claude Code integration — see [`../claude-code/README.md`](../claude-code/README.md).

## License

MIT.
