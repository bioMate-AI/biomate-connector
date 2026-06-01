# Render Templates for BioMate Progress Events

Render incoming `notifications/progress` events using these markdown templates so the chat surface mirrors BioMate's web panel. Each template assumes `p` is the progress payload (`kind`, `summary_md`, `view_url`, `thumbnail_png_b64`, `delta`).

## phase_started / phase_completed / phase_failed

```markdown
> **Phase {n}: {delta.name}** — {kind without `phase_` prefix, capitalized}
```

If `phase_failed`, prefix with ⚠ and emit a single line of context from `delta.error_summary`.

## step_started / step_completed / step_failed

```markdown
- {✓|⋯|✗} `{delta.name}`  *{delta.elapsed_s or ""}s*
```

Use `✓` for completed, `⋯` for started/running, `✗` for failed. Do not render a separate line for `step_started` if a `step_completed` is expected within a few seconds — collapse to a single completed line.

## qc_gate

```markdown
### QC gate — {delta.metric}

| | Measured | Threshold | Verdict |
|---|---|---|---|
| **{delta.metric}** | `{delta.value}` | `{delta.threshold}` | **{delta.verdict}** |
```

If `thumbnail_png_b64` is set, render the embedded image directly under the table — it's the same card the web UI shows.

When the verdict is `halt` or `advisory`, also add:

> **Next:** the auto-loop will propose a parameter fix. Watch for the next `auto_loop_remediation` event.

## auto_loop_remediation

```markdown
### Auto-loop fix

`{delta.param}`: ~~`{delta.was}`~~ → **`{delta.now}`**

*Reason: {delta.reason_md}*

Re-running phase **{delta.phase}**…
```

This is the diff card. Always show was → now with strikethrough on `was` so the user sees what changed.

## finding

```markdown
### {delta.title}

{summary_md from the event}

{render thumbnail_png_b64 if present}

[Open in BioMate panel →]({view_url})
```

Findings are the user's actual scientific output. Render them prominently — usually as their own block, not collapsed inline.

## text_delta

Stream as plain text into the running assistant message (no special formatting). These are free-form narration from BioMate's AI assistant.

## done

```markdown
---

### ✓ Session complete

**Run:** `{delta.run_id}`
**Workflow:** {delta.workflow_name}
**Outputs:** {delta.output_count} files
**Findings:** {delta.finding_count}

[Open the full panel →]({view_url})
```

If the user asked for a report (`PDF`, `methods section`, `IND submission`, `CRO package`), follow up with a `export_report` call and attach the resulting file. Do not wait for them to ask.

## Footer convention

After every streamed session, end the assistant turn with one line:

> Live panel: <{view_url}> · BioMate run id `{run_id}`

This gives the user the escape hatch into the full UI, which is the only place they can get pixel-perfect parameter editing, interactive 3D viewers, and live cost meters.
