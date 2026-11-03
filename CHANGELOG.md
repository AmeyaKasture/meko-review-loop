# Changelog

## 0.3.0 (2026-11-03)

- Findings are marked `in-change`, `near-change`, or `outside-change` against the
  reviewed diff (borrowed from AgentK's validation, as a flag rather than a
  filter). The ingest listing shows `[outside change]`, and the rubric says when
  to defer such findings.
- `review-loop-meko` 0.2.0 supports offline Meko (`yugabyte/meko`): local
  default-datapack fallback, `/gotchas` datapacks with `MG-<n>` provenance, and
  an optional local KB upload of the report.
- `examples/agentk/`: AgentK prompt overrides that require an independent
  verifier before a blocker or major finding is declined.

## 0.2.0 (2026-11-03)

- Packaged as a Claude Code plugin marketplace with two plugins: `review-loop`
  and the optional `review-loop-meko` extension.
- Stop guard moved from skill frontmatter to the plugin's `hooks/hooks.json`.
- Reviewer and verifier agents block all MCP tools (`mcp__*`), so memory
  connectors cannot hand reviewers the author's own conversation.
- The gate session runs with `--strict-mcp-config` and all hooks disabled, so
  connectors cannot inject context, capture the session, or spend quota.
- Per-repository defaults in `.review-loop.json`: checks, rounds, strictness,
  gate, models, size limits.
- Size gate: oversized changes require the user's go-ahead before round 1.
- Briefs list the repository review guides (`REVIEW.md`, `AGENTS.md`,
  `CLAUDE.md`, `CONTRIBUTING.md`) that govern the touched files. New
  finding category: `guideline`.
- Check probe (`run-checks --probe`) and `ENV-ERROR` detection, so a check
  that cannot run never blocks convergence.
- `rl.py answer` records your decisions. `rl.py set-intent` and
  `rl.py export-learnings` support the Meko extension.
- Duplicate hints now cover other reviewers in the same round. Duplicate
  chains resolve to the root finding.

## 0.1.0 (2026-11-03)

- First working version as a personal skill: fresh-context reviewers,
  verifier-backed rejections, ledger with oscillation handling, separate
  `claude -p` gate, decisions report.
