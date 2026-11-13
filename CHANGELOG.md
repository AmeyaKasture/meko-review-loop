# Changelog


## 0.4.0

- Renamed the project Meko Review Loop (`meko-review-loop`; the Claude Code
  marketplace is now `meko-review-loop`, so the plugin is
  `review-loop@meko-review-loop`).
- Agent-agnostic: `install.sh --agent claude|codex|cursor|gemini|agents`
  installs the Claude Code plugin or copies the skills (namespaced
  `review-loop-setup`, `-respond`, `-exit`) into another agent's skills
  directory. Skill texts no longer assume Claude Code's tools.
- `headless-review.sh` runs reviewers, the gate, and the finding verifier as
  separate processes of any agent CLI (`claude`, `codex`, `gemini`, `cursor`,
  or a `custom` command template), chosen by `reviewer_agent` / `gate_agent`.
  Enables cross-vendor review. `fresh-session-review.sh` now wraps it.
- `rl.py ingest` accepts findings inside an agent's JSON envelope or prose;
  `rl.py brief --output file|structured`; `rl.py learnings-context --text` for
  agents without a SessionStart hook.
- Review skills are found in `.agents/skills`, `.claude/skills`,
  `.cursor/skills`, `.codex/skills`, `.gemini/skills`, or by path.
- Private files move to `~/.review-loop/`; an existing `~/.claude/review-loop/`
  keeps working until the new directory exists.
- Meko is back as a first-class, optional guide store
  (`scripts/stores/meko_store.py`, plugged in through the store commands).
  The setup skill checks for Meko, offers to install, start, and connect it
  to the user's agent, and configures it. `tests/test_meko_live.sh` tests it
  against a running Meko.
- Three triage outcomes: valid (fix it and diagnose the miss), not valid (keep the
  code, reason in the report), and doubtful (keep the code, listed first in
  the report as a possible design difference). `reject`, `doubtful`, and
  `defer` now require a reason.
- Team review skill: `review_skill` (for example `branch-review`) is applied
  by every reviewer and the gate, adapted to the loop's snapshot range.
  Severity `question` maps the skill's QUESTION class and never blocks.
- Rule files: `BUGBOT.md` and `.cursor/BUGBOT.md` are discovered, and
  symlinked copies (`CLAUDE.md` -> `AGENTS.md`) are listed once.
- Private settings in `~/.review-loop/repos/<owner>__<repo>.json`.
- `respond` skill and `reopen`, `pr-ingest`, `pr-replies` commands: answer PR
  comments in the same thread; fixes go back through fresh review; replies
  are drafted locally and posted only after a push and explicit approval.
- Miss analysis: `fixed --cause/--why/--guide-ref` and `rl.py miss` record why
  the author session missed each valid finding (`not-covered`,
  `covered-unclear`, `covered-ignored`, `context-gap`, `spec-gap`, `one-off`).
- `exit` skill: turns the causes into a revision of the author guide
  (`~/.review-loop/guides/<owner>__<repo>.md`), a semantic document
  edited as a whole per the user's `EXIT.md`, shown as a diff and applied on
  approval, with a separate history file. A SessionStart hook loads the
  guide into future author sessions only.
- Author guide store: the guide is a local file by default. An external,
  versioned store can be attached with two shell commands
  (`guide_pull_cmd`, `guide_push_cmd` in `~/.review-loop/settings.json`);
  `rl.py guide-pull` and `guide-push` use it with the local file as cache and
  fallback (`guide_store`: `auto`, `command`, or `file`).
  `examples/guide-store/git-store.sh` is a store backed by a private git
  repository. Reviewers are told never to query any memory or notes service,
  and have no MCP tools.
- `setup` skill (`/review-loop:setup`): surveys a repository (build system, CI
  jobs, rule files, existing review skills, typical change size), offers to
  add missing tooling (linter, type-checker, smoke test, CI workflow,
  `AGENTS.md`) from per-stack defaults, works on an empty repository, drafts the
  settings and, if needed, a team review skill from a template, asks where to
  keep them, writes them, and probes every check. `docs/SETUP.md` lets Claude
  install the plugin and run the setup from one pasted sentence.
- `rl.py config [--probe]`: shows the settings in effect, flags unknown keys
  and a missing review skill, and runs each check once without loop state.
- Examples are generic (TypeScript app, Python service, Go monorepo).
- Scoped Go checks: `{go_packages}` in a check expands to the changed packages,
  their importers (`go_reverse_depth`), and packages the author declares with
  `rl.py scope --add` (widen only, never narrow). Briefs list the scope with
  reasons; reviewers read the logs instead of re-running suites and report
  missing packages as `test-gap` findings.
- Reviewers no longer receive team pitfalls or any author history.
- Removed the memory-service extension and the external agent bridge.

## 0.3.0 (2026-11-03)

- Findings are marked `in-change`, `near-change`, or `outside-change` against the
  reviewed diff (borrowed from another review agent's validation, as a flag rather than a
  filter). The ingest listing shows `[outside change]`, and the rubric says when
  to defer such findings.
- The optional memory-service extension (0.2.0) supports a local, offline
  service, team pitfall collections with provenance, and an optional upload
  of the report.
- Example prompt overrides for an external review agent that require an
  independent verifier before a blocker or major finding is declined.

## 0.2.0 (2026-11-03)

- Packaged as a Claude Code plugin marketplace with two plugins: `review-loop`
  and an optional memory-service extension.
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
  `rl.py export-learnings` support the memory-service extension.
- Duplicate hints now cover other reviewers in the same round. Duplicate
  chains resolve to the root finding.

## 0.1.0 (2026-11-03)

- First working version as a personal skill: fresh-context reviewers,
  verifier-backed rejections, ledger with oscillation handling, separate
  `claude -p` gate, decisions report.
