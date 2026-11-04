# Changelog


## 0.4.0

- Three triage outcomes: valid (fix it and diagnose the miss), not valid (keep the
  code, reason in the report), and doubtful (keep the code, listed first in
  the report as a possible design difference). `reject`, `doubtful`, and
  `defer` now require a reason.
- Team review skill: `review_skill` (for example `branch-review`) is applied
  by every reviewer and the gate, adapted to the loop's snapshot range.
  Severity `question` maps the skill's QUESTION class and never blocks.
- Rule files: `BUGBOT.md` and `.cursor/BUGBOT.md` are discovered, and
  symlinked copies (`CLAUDE.md` -> `AGENTS.md`) are listed once.
- Private settings in `~/.claude/review-loop/repos/<owner>__<repo>.json`.
- `respond` skill and `reopen`, `pr-ingest`, `pr-replies` commands: answer PR
  comments in the same thread; fixes go back through fresh review; replies
  are drafted locally and posted only after a push and explicit approval.
- Miss analysis: `fixed --cause/--why/--guide-ref` and `rl.py miss` record why
  the author session missed each valid finding (`not-covered`,
  `covered-unclear`, `covered-ignored`, `context-gap`, `spec-gap`, `one-off`).
- `exit` skill: turns the causes into a revision of the author guide
  (`~/.claude/review-loop/guides/<owner>__<repo>.md`), a semantic document
  edited as a whole per the user's `EXIT.md`, shown as a diff and applied on
  approval, with a separate history file. A SessionStart hook loads the
  guide into future author sessions only.
- Author guide store: the local (offline) Meko keeps every guide version as an
  artifact in datapack `review-loop guide <owner>/<repo>` and each loop's
  history as a memory; `rl.py guide-pull` and `guide-push` read and write it
  over Meko's local MCP endpoint, with the local file as cache and fallback
  (`guide_store`: `meko`, `auto`, or `file`). Reviewers are told never to
  query Meko, and have no MCP tools.
- Reviewers no longer receive team pitfalls or any author history.
- Removed the cloud-oriented Meko extension and the AgentK bridge.

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
