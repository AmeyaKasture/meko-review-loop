# review-loop-meko

An optional [Meko](https://mekodata.ai) layer for `review-loop`, built in the
same shape as Meko's BMad extension: recall is non-blocking, writes are
consent-gated, and current evidence always beats memory.

| Skill | When | Meko calls |
|---|---|---|
| `review-loop-meko-context` | Before round 1 | Up to 5 searches. No writes. |
| `review-loop-meko-publish` | After the report, on your yes | 1 search, 1 `artifact_put`, 1 `memory_add` (plus `conversation_create` if needed). |

What it adds to the loop:

- **Fewer judgement-call questions.** Reviewers see recorded design decisions
  as requirements, so intended behaviour changes stop being flagged as bugs.
- **Sharper reviewers.** Team pitfalls such as "JSON tags are a wire contract"
  become part of every reviewer's lens.
- **Faster triage.** Prior verified false positives are shown to the
  orchestrator only, and never to reviewers.
- **Compounding learning.** Each loop's outcome becomes a memory that a
  datapack owner can promote to shared knowledge for the whole team.

What it deliberately does not do:

- Give reviewers MCP access. The core plugin blocks `mcp__*` in reviewer
  agents, and the gate session runs with no MCP servers and no hooks.
- Store loop state in Meko. Local state is faster, works offline, and costs
  no quota.
- Capture reviewer sessions as conversations. That would burn the Free
  tier's lifetime conversation quota on noise.

## Install

Meko must be connected first, in either of two ways:

- **Meko Cloud** as the claude.ai connector, or with
  `npx @yugabytedb/meko-mcp@latest --client claude-code`.
- **Offline Meko** (`yugabyte/meko`): install it, then run
  `meko agent connect claude`. Everything stays on your machine, with no quotas.

Then:

```
/plugin marketplace add ~/claude-review-loop
/plugin install review-loop-meko@review-loop
```

## Detaching

This directory is self-contained apart from calling `rl.py` from the core
plugin. To contribute it upstream, copy it to
`yugabyte/meko-skills/extensions/review-loop/`, next to `extensions/bmad/`.
