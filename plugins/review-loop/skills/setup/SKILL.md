---
name: setup
description: Set up Meko Review Loop for the current repository, including a brand-new one, with any coding agent. Surveys the repository (languages, build and CI commands, rule files, existing review skills, typical change size); with the user's yes, adds whatever is missing (linter, type-checker, a first test, a CI workflow, an AGENTS.md); offers Meko, a local memory engine, as the home of the author guide, and installs and connects it on request; then writes the settings file, creates a team review skill if none exists, and verifies everything with a probe run. Shows every file before writing it and never commits.
when_to_use: The user runs /review-loop:setup (or the review-loop-setup skill), or asks to set up, configure, or onboard a repository for review-loop, to create its config file or review skill, or to set up Meko for it.
argument-hint: "[--private | --commit] [--skill-name NAME] [--no-meko]"
disable-model-invocation: false
---

# Set up Meko Review Loop for this repository

Arguments: `$ARGUMENTS`

**Paths.** `${CLAUDE_SKILL_DIR}` below is the directory that contains this
file. Claude Code fills it in; installs for other agents have it written out.
If you see it unexpanded, use this file's own directory. The engine is
`${CLAUDE_SKILL_DIR}/../review-loop/scripts/rl.py` (written below as `rl.py`;
always call it by that full path, from inside the target repository).

**Asking.** Where this skill says *ask*, use your agent's question tool if it
has one (Claude Code: `AskUserQuestion`), otherwise a short numbered list of
options in the chat. Put the recommended option first, marked "(Recommended)".

**Your agent.** Work out which agent you are running in (Claude Code, Codex,
Cursor, Gemini CLI, OpenCode, Kiro, or another). It decides the skills
directory, how reviewers run, and which `meko agent connect` target to use.

The output of this skill is up to five files:

| File | Purpose |
|---|---|
| `.review-loop.json` at the repository root, **or** `~/.review-loop/repos/<owner>__<repo>.json` | Loop settings: checks, rounds, agents, models, size limits, review skill. |
| `<skills dir>/<name>/SKILL.md` | The team review skill every reviewer applies (only if none exists). |
| `~/.review-loop/EXIT.md` | The user's instructions for the exit step (from a template, if absent). |
| `~/.review-loop/settings.json` | Where the author guide lives (Meko or another store). |
| Repository tooling | Only what the user approves in Step 3. |

`<skills dir>` is `.agents/skills/` (read by several agents), or your agent's
own: `.claude/skills/`, `.cursor/skills/`, `.codex/skills/`,
`.gemini/skills/`. The engine looks in all of them, in the repository first
and then in the home directory.

## Rules

- Survey first, ask, then write. Read-only until the user has answered.
- Never overwrite an existing file without showing a `diff -u` and getting a yes.
- Never `git add`, commit, or push. Say which files the user may want to commit.
- Never download or install anything (tools, Meko) without an explicit yes
  to a question that named it.
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
   commit yet, continue, but tell the user in Step 10 that the first loop
   needs one commit on `main` to compare against.
2. `rl.py config`. If it finds settings already, show them and ask whether to
   update them or start fresh; on "update", keep every key the user has not
   asked to change.

## Step 2: Survey the repository (read-only)

Collect these facts. For a large repository, use a read-only subagent if
your agent has one, so the file dumps stay out of this conversation.

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
5. **Rule files:** every `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, `REVIEW.md`,
   `BUGBOT.md`, `.cursor/BUGBOT.md`, `.cursor/rules/*`, `CONTRIBUTING.md` in
   the tree (`git ls-files` filtered by name). Note which are symlinks. Skim
   them for rules that can be checked mechanically (banned calls, required
   patterns, naming, test conventions).
6. **Existing review skills:** any `SKILL.md` in the skills directories
   above (repository and home) or a repository `skills/` directory whose name
   or description is about reviewing code. A review skill elsewhere can be
   referenced by path (`"review_skill": "docs/review.md"`).
7. **Typical change size:** from the last 50 merge or squash commits on the
   default branch, the 90th-percentile files and lines changed
   (`git log --shortstat`). Use it to set `size_limits` a little above that.
8. **Check cost:** for each candidate check, estimate its run time (CI
   timings if visible, otherwise run it once with `time` if it is a lint or
   typecheck; do not run long test suites just to time them).
9. **Agent CLIs on this machine:** `command -v claude codex gemini cursor-agent`.
   These can run reviewers and the gate as separate processes (Step 6).
10. **Meko:** `python3 ${CLAUDE_SKILL_DIR}/../review-loop/scripts/stores/meko_store.py check`.
    Exit 0 means Meko is installed and running. Otherwise note whether the
    `meko` command exists (`command -v meko || ls ~/.local/bin/meko`).

## Step 3: Fill the gaps

Skip this step if the repository already has a linter or type-checker, tests,
and a rule file. Otherwise the loop would have little to check against, so
offer to add what is missing. Read `${CLAUDE_SKILL_DIR}/reference/tooling.md`
first; it has the defaults per stack and the rules for adding them.

1. **Stack.** Use what Step 2 found. If the repository has no code yet, ask
   the user which language (and package manager) the project will use.
2. **List the gaps**, from: lint (and format) check, type-check, test runner
   with a first smoke test, CI workflow, rule file (`AGENTS.md`).
3. **Ask once** (multi-select where your agent supports it, every option
   marked "(Recommended)"): "This repository has no <gaps>. Which should I
   add?" Each option names the exact tool and command, for example
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
6. Feed the new commands and rule file into Step 5 and Step 6 as if Step 2
   had found them.

## Step 4: Meko for the author guide

Skip this step if the user passed `--no-meko`.

The author guide is the private document, revised after every loop, that
teaches the author agent what it keeps getting wrong in this repository.
[Meko](https://mekodata.ai) is a local, offline memory engine that can be its
home: every revision is kept as a version, each loop's miss history becomes
a searchable memory, and any agent connected to Meko (Claude Code, Codex,
Cursor, OpenCode, Kiro) can recall it. Without Meko the guide is a local
file, which works fine for one agent on one machine.

1. **Meko is running** (Step 2 check passed): ask "Keep the author guide in
   Meko?" with *Yes, use Meko (Recommended)* and *No, local files*.
2. **Meko is installed but not running:** ask whether to start it; on yes run
   `meko start` (or `~/.local/bin/meko start`), then the check again.
3. **Meko is not installed:** ask "Meko is not installed. Install it to keep
   the author guide as versioned, searchable memory shared across your
   agents?" with *Install Meko (Recommended)*, *Not now, use local files*,
   and *Tell me more* (answer from the paragraph above, then ask again).
   On *Install*:
   1. Tell the user it downloads and runs the official installer from
      `https://mekodata.ai/install.sh`, installs for this user only, and runs
      on `127.0.0.1` only.
   2. `curl -fsSL https://mekodata.ai/install.sh | sh`
   3. Ask whether to turn off usage statistics and automatic update checks
      (recommended for a fully offline setup). On yes, add to
      `~/.meko/config.toml` (merge if the file exists; never drop other keys):
      ```
      [call_home]
      enabled = false

      [update]
      channel = "off"
      auto = false
      ```
      then `meko restart`.
   4. `meko status` must show it running; then the Step 2 check must pass.
      If it does not, show the output, fall back to local files, and say so.
4. **Connect the user's agent** (optional, ask): `meko agent connect <agent>`
   with `claude`, `codex`, `cursor`, `opencode`, or `kiro` (`meko agent list`
   shows what is available). This lets the author's own sessions search Meko.
   Never connect it to reviewers: they must not see the guide.
5. **Configure the store** in `~/.review-loop/settings.json` (merge with
   existing keys), with the absolute path of `meko_store.py`:
   ```json
   {"guide_store": "auto",
    "guide_pull_cmd": "python3 <abs path>/scripts/stores/meko_store.py pull",
    "guide_push_cmd": "python3 <abs path>/scripts/stores/meko_store.py push"}
   ```
   `auto` falls back to the local copy whenever Meko is down. Use
   `"command"` instead if the user wants a failed save to be an error.
6. `rl.py guide-pull` must succeed: `source` is `store` (a guide exists),
   or `none` with no error note (Meko has no guide for this repository yet).

## Step 5: Draft the settings

Choose **2 to 5 checks**: the fastest set that catches what CI would
(typecheck, lint, build, fast unit tests). For a monorepo, prefix each with
`cd <subproject> &&` and include only the sub-projects that change often;
mention in `_comment` that the user should keep only the ones they touch.
Leave out integration, end-to-end, and anything over roughly five minutes.

Draft the settings object with only the keys that differ from the defaults
(`max_rounds` 5, `gate` true, `strict` false, size limits 20 commits / 100
files / 5000 lines, `reply_prefix` `"[AI] "`, gate agent `claude`):

```json
{
  "_comment": "<one line: where the checks came from, e.g. 'mirrors the lint and unit-test jobs in .github/workflows/ci.yml'>",
  "review_skill": "<name>",
  "checks": ["<cmd>", "<cmd>"],
  "max_rounds": 4,
  "reviewer_agent": "<subagent | claude | codex | gemini | cursor | custom>",
  "gate_agent": "<claude | codex | gemini | cursor | custom>",
  "reviewer_model": "<model>",
  "gate_model": "<model>",
  "size_limits": { "commits": 20, "files": 60, "lines": 3000 }
}
```

`reviewer_agent` is `subagent` (reviewers are subagents of the author's own
agent) or a CLI that runs each reviewer as a separate process.
`gate_agent` is the CLI for the final gate review. For `custom`, add
`headless_command`, a shell template with `{prompt_file}`, `{model}`, and
`{output}`. Model names belong to the chosen agent (for example `opus` for
Claude, a GPT model for Codex); omit them to use each agent's default.

For Go, prefer scoped checks: keep `go build ./...` for the whole module,
and run `go vet` and `go test` on `{go_packages}`, which the engine expands
each round to the changed packages and their importers. Set `go_module` for
a module that is not at the repository root, and `go_tags` when the
repository's tests need build tags.

Valid keys: `checks`, `max_rounds`, `strict`, `gate`, `gate_model`,
`reviewer_model`, `reviewer_agent`, `gate_agent`, `headless_command`,
`size_limits`, `review_skill`, `exit_instructions`, `reply_prefix`,
`go_module`, `go_tags`, `go_reverse_depth`, `_comment`. `rl.py config`
rejects anything else.

## Step 6: Draft the review skill (only if none exists)

If Step 2 found a suitable review skill, use its name (or path) and skip
this step. Otherwise fill in `${CLAUDE_SKILL_DIR}/reference/review-skill.template.md`:

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

## Step 7: Ask where things go

Show a short summary of the survey (stack, CI commands found, rule files,
existing review skill, typical change size, agent CLIs found, Meko status)
and the drafted checks. Then ask, in one go where possible (skip any
question an argument already answers):

1. **Where should the settings live?**
   - *Private* (`~/.review-loop/repos/<owner>__<repo>.json`): no change to
     the repository. Recommend this for a repository the user does not own
     or a team that has not adopted review-loop.
   - *Committed* (`.review-loop.json`): shared with the team.
2. **Review skill** (only when Step 6 drafted one): in the repository
   (`.agents/skills/<name>/`, or your agent's skills directory), privately
   (`~/.agents/skills/<name>/`), or not at all (reviewers use the built-in
   lenses).
3. **Who reviews:** offer only what Step 2 found installed, for example
   "Subagents of this agent (Recommended)", "Codex reviews, Claude gate"
   (cross-vendor), or "Claude for both". Recommend a gate from a different
   vendor than the author's agent when one is installed: different models
   miss different bugs.
4. **Depth:** thorough (largest models, gate on), balanced, or fastest
   (smaller models, no gate).

## Step 8: Write the files

1. Settings JSON at the chosen path (create `~/.review-loop/repos/` if
   needed). Show the content first if a file exists there.
2. The review skill at the chosen path, if any.
3. If `~/.review-loop/EXIT.md` does not exist, copy
   `${CLAUDE_SKILL_DIR}/../exit/reference/EXIT.template.md` there and tell
   the user they can edit it.
4. The guide store from Step 4, if not written there yet.

## Step 9: Verify

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
5. Every agent CLI named in `reviewer_agent` / `gate_agent` must be on the
   `PATH`.
6. Repeat until `problems` is empty.

## Step 10: Report

Tell the user, briefly:

- anything Step 3 added (tools, dependencies, smoke test, CI, `AGENTS.md`);
- where the author guide lives (Meko, with the datapack name
  `review-loop guide <owner>/<repo>`, or local files), and whether an agent
  was connected to Meko;
- the files written (with paths), and which ones they may want to commit;
- the checks and their probe results, and who reviews (agents and models);
- how to use it: implement a change, then run the review-loop skill
  (Claude Code: `/review-loop:review-loop`); after pushing, the respond skill
  with the PR number; when the work is done, the exit skill.
- For agents without a session-start hook (anything but Claude Code): to
  give the author session its guide, run
  `rl.py learnings-context --text` at the start of a session, or wire it
  into the agent's own startup hook if it has one.
