---
name: setup
description: Set up review-loop for the current repository, including a brand-new one. Surveys the repository (languages, build and CI commands, rule files, existing review skills, typical change size); with the user's yes, adds whatever is missing (linter, type-checker, a first test, a CI workflow, an AGENTS.md); then writes the review-loop settings file, creates a team review skill if none exists, optionally configures an external author-guide store, and verifies everything with a probe run. Shows every file before writing it and never commits.
when_to_use: The user runs /review-loop:setup, or asks to set up, configure, or onboard a repository for review-loop, or to create its config file or review skill.
argument-hint: "[--private | --commit] [--skill-name NAME]"
disable-model-invocation: false
---

# Set up review-loop for this repository

Arguments: `$ARGUMENTS`

The engine is `${CLAUDE_SKILL_DIR}/../review-loop/scripts/rl.py` (written below
as `rl.py`; always call it by that full path, from inside the target
repository). If `${CLAUDE_SKILL_DIR}` is not set because you are following this
file directly (for example from `docs/SETUP.md`), use the plugin path that
`claude plugin list` prints for `review-loop`, plus
`/skills/review-loop/scripts/rl.py`.

The output of this skill is up to four files:

| File | Purpose |
|---|---|
| `.review-loop.json` at the repository root, **or** `~/.claude/review-loop/repos/<owner>__<repo>.json` | Loop settings: checks, rounds, models, size limits, review skill. |
| `.claude/skills/<name>/SKILL.md` **or** `~/.claude/skills/<name>/SKILL.md` | The team review skill every reviewer applies (only if none exists). |
| `~/.claude/review-loop/EXIT.md` | The user's instructions for the exit skill (from a template, if absent). |
| `~/.claude/review-loop/settings.json` | Only if the user wants an external author-guide store. |

## Rules

- Survey first, ask once, then write. Read-only until the user has answered.
- Never overwrite an existing file without showing a `diff -u` and getting a yes.
- Never `git add`, commit, or push. Say which files the user may want to commit.
- Every check command must be non-interactive, must not modify tracked files
  (use `--check` / `--diff` modes of formatters), and must not need network
  access or secrets beyond what a normal local build needs.
- Prefer commands the repository already documents or runs in CI over
  invented ones.
- Add tooling, dependencies, CI, or rule files only after the user said yes
  in Step 3, and only as `${CLAUDE_SKILL_DIR}/reference/tooling.md` describes.

## Step 1: Current state

1. `git rev-parse --show-toplevel`. If this is not a git repository, ask
   whether to run `git init -b main` here; stop if the user says no.
   Then `git remote get-url origin` and `git rev-list --count HEAD`. No
   remote is fine (settings are then named after the folder). With no
   commit yet, continue, but tell the user in Step 9 that the first loop
   needs one commit on `main` to compare against.
2. `rl.py config`. If it finds settings already, show them and ask whether to
   update them or start fresh; on "update", keep every key the user has not
   asked to change.

## Step 2: Survey the repository (read-only)

Collect these facts. Use a subagent (`Explore`) for a large repository so the
file dumps stay out of this conversation.

1. **Default branch:** `git symbolic-ref --short refs/remotes/origin/HEAD`
   (fallback `main`, then `master`).
2. **Languages and build systems:** `rl.py detect-checks` prints candidate
   commands from manifests (`package.json`, `pyproject.toml`, `go.mod`,
   `Cargo.toml`, `pom.xml`, Gradle, `Makefile`). For a monorepo, list each
   sub-project with its own manifest and its directory.
3. **What CI runs:** read `.github/workflows/*.yml`, `.gitlab-ci.yml`,
   `.circleci/config.yml`, `azure-pipelines.yml`, `Jenkinsfile`,
   `.pre-commit-config.yaml`, `tox.ini`, `noxfile.py`, and `Makefile` targets.
   Note the lint, typecheck, build, and unit-test steps, with their working
   directory and flags (build tags, feature flags, test markers).
4. **Contributor docs:** `CONTRIBUTING.md`, `DEVELOPMENT.md`, `README.md`
   "Development" or "Testing" sections, for the documented local commands.
5. **Rule files:** every `AGENTS.md`, `CLAUDE.md`, `REVIEW.md`, `BUGBOT.md`,
   `.cursor/BUGBOT.md`, `.cursor/rules/*`, `CONTRIBUTING.md` in the tree
   (`git ls-files` filtered by name). Note which are symlinks. Skim them for
   rules that can be checked mechanically (banned calls, required patterns,
   naming, test conventions).
6. **Existing review skills:** any `SKILL.md` under `.claude/skills/`,
   `~/.claude/skills/`, `.cursor/skills/`, or a repository `skills/`
   directory whose name or description is about reviewing code. Only skills
   under `.claude/skills/` or `~/.claude/skills/` can be referenced directly;
   a review skill found elsewhere can be linked or copied there.
7. **Typical change size:** from the last 50 merge or squash commits on the
   default branch, the 90th-percentile files and lines changed
   (`git log --shortstat`). Use it to set `size_limits` a little above that.
8. **Check cost:** for each candidate check, estimate its run time (CI
   timings if visible, otherwise run it once with `time` if it is a lint or
   typecheck; do not run long test suites just to time them).

## Step 3: Fill the gaps

Skip this step if the repository already has a linter or type-checker, tests,
and a rule file. Otherwise the loop would have little to check against, so
offer to add what is missing. Read `${CLAUDE_SKILL_DIR}/reference/tooling.md`
first; it has the defaults per stack and the rules for adding them.

1. **Stack.** Use what Step 2 found. If the repository has no code yet, ask
   the user which language (and package manager) the project will use.
2. **List the gaps**, from: lint (and format) check, type-check, test runner
   with a first smoke test, CI workflow, rule file (`AGENTS.md`).
3. **Ask once** with `AskUserQuestion` (multi-select, every option marked
   "(Recommended)"): "This repository has no <gaps>. Which should I add?" Each option names the exact tool and command, for example
   "Lint: ruff (`ruff check .`)". Say that installing downloads development
   dependencies.
4. **Add the chosen pieces**, following `tooling.md`: install dev
   dependencies with the project's package manager, put configuration in the
   existing manifest, add one smoke test, write the CI workflow, and draft
   `AGENTS.md` from what the code actually does. Show the `AGENTS.md` draft
   and any manifest diff before writing them.
5. **Run each new command once.** It must pass on the current code. If a
   new linter reports many existing problems, narrow its rules as
   `tooling.md` says instead of changing code, and tell the user the count.
6. Feed the new commands and rule file into Step 4 and Step 5 as if Step 2
   had found them.

## Step 4: Draft the settings

Choose **2 to 5 checks**: the fastest set that catches what CI would
(typecheck, lint, build, fast unit tests). For a monorepo, prefix each with
`cd <subproject> &&` and include only the sub-projects that change often;
mention in `_comment` that the user should keep only the ones they touch.
Leave out integration, end-to-end, and anything over roughly five minutes.

Draft the settings object with only the keys that differ from the defaults
(`max_rounds` 5, `gate` true, `strict` false, size limits 20 commits / 100
files / 5000 lines, `reply_prefix` `"[AI] "`):

```json
{
  "_comment": "<one line: where the checks came from, e.g. 'mirrors the lint and unit-test jobs in .github/workflows/ci.yml'>",
  "review_skill": "<name>",
  "checks": ["<cmd>", "<cmd>"],
  "max_rounds": 4,
  "reviewer_model": "opus",
  "gate_model": "opus",
  "size_limits": { "commits": 20, "files": 60, "lines": 3000 }
}
```

For Go, prefer scoped checks: keep `go build ./...` for the whole module,
and run `go vet` and `go test` on `{go_packages}`, which the engine expands
each round to the changed packages and their importers. Set `go_module` for
a module that is not at the repository root, and `go_tags` when the
repository's tests need build tags.

Valid keys: `checks`, `max_rounds`, `strict`, `gate`, `gate_model`,
`reviewer_model`, `size_limits`, `review_skill`, `exit_instructions`,
`reply_prefix`, `go_module`, `go_tags`, `go_reverse_depth`, `_comment`.
`rl.py config` rejects anything else.

## Step 5: Draft the review skill (only if none exists)

If Step 2 found a suitable review skill, use its name and skip this step.
Otherwise fill in `${CLAUDE_SKILL_DIR}/reference/review-skill.template.md`:

- `{{SKILL_NAME}}`: the `--skill-name` argument, else `branch-review`
  (avoid names of built-in skills such as `code-review`).
- `{{REPO_NAME}}`, `{{DEFAULT_BRANCH}}`: from Step 2.
- `{{STANDARDS}}`: a bullet list of the rule files found, each with one line
  on what it governs. If there are none, say "This repository has no rule
  files; review against the language's common conventions and the
  contributor docs" and name those docs.
- `{{MECHANICAL_CHECKS}}`: concrete, cheap commands scoped to changed files
  (a linter or vet on changed packages, a formatter in check mode), plus
  greps over added lines for each lexically checkable rule from the rule
  files (for example `git diff $MERGE_BASE..HEAD | grep '^+' | grep -n 'console.log'`).
  Only include rules the repository actually states.
- `{{EXTRA_LENSES}}`: up to four numbered lenses specific to this codebase,
  derived from what it is (for example "7. **Schema and migrations**: ..."
  for a service with a database, "7. **Public API compatibility**: ..." for a
  library, "7. **Hot paths**: ..." for performance-sensitive code). Leave
  empty if nothing stands out.

Keep the skill under about 250 lines. It must keep the four classes
CRITICAL, WARNING, SUGGESTION, QUESTION; review-loop maps them to its
severities.

## Step 6: Ask where things go

Show a short summary of the survey (stack, CI commands found, rule files,
existing review skill, typical change size) and the drafted checks. Then ask
with one `AskUserQuestion` call (skip any question an argument already
answers; put the recommended option first):

1. **Where should the settings live?**
   - *Private* (`~/.claude/review-loop/repos/<owner>__<repo>.json`): no change
     to the repository. Recommend this for a repository the user does not own
     or a team that has not adopted review-loop.
   - *Committed* (`.review-loop.json`): shared with the team.
2. **Review skill** (only when Step 5 drafted one): create it in the
   repository (`.claude/skills/<name>/`), privately (`~/.claude/skills/<name>/`),
   or not at all (reviewers use the built-in lenses).
3. **Models and gate:** "Opus reviewers and gate" (thorough), "Sonnet
   reviewers, Opus gate" (balanced), or "Sonnet, no gate" (fastest).
4. **Author guide store:** "Local files" (default, recommended) or "External
   store" (the user supplies pull and push commands; see
   `examples/guide-store/` in the review-loop repository).

## Step 7: Write the files

1. Settings JSON at the chosen path (create `~/.claude/review-loop/repos/`
   if needed). Show the content first if a file exists there.
2. The review skill at the chosen path, if any.
3. If `~/.claude/review-loop/EXIT.md` does not exist, copy
   `${CLAUDE_SKILL_DIR}/../exit/reference/EXIT.template.md` there and tell
   the user they can edit it.
4. For an external guide store, write `~/.claude/review-loop/settings.json`:
   `{"guide_store": "auto", "guide_pull_cmd": "...", "guide_push_cmd": "..."}`.
   Merge with any existing keys. Then `rl.py guide-pull` must succeed
   (source `store`, `file`, or `none` with no error note).

## Step 8: Verify

1. `rl.py config --probe`. It runs every check once and reports `PASS`,
   `FAIL`, `TIMEOUT`, or `ENV-ERROR`, plus any `problems`. Checks with
   `{go_packages}` show `SCOPED`; for them it verifies that `go list` works
   with the configured `go_module` and `go_tags`.
2. For each `ENV-ERROR` (missing tool or module): replace the check with an
   equivalent that runs here, drop it, or tell the user what to install. A
   check that cannot run would block every loop.
3. For each `TIMEOUT`: scope the check down or drop it.
4. `FAIL` on the default branch is acceptable only if it is a real,
   pre-existing failure; tell the user, because every loop will report it.
5. Repeat until `problems` is empty.

## Step 9: Report

Tell the user, briefly:

- anything Step 3 added (tools, dependencies, smoke test, CI, `AGENTS.md`);
- the files written (with paths), and which ones they may want to commit;
- the checks and their probe results;
- how to use it: implement a change, then `/review-loop:review-loop`; after
  pushing, `/review-loop:respond <PR>`; when the work is done,
  `/review-loop:exit`.
