# claude-review-loop

A Claude Code plugin that turns "a second AI with a fresh context always
finds bugs" into a bounded, repeatable process, and makes the author session
better at not writing those bugs in the first place.

## Getting started

### 1. Install the plugin

```
claude plugin marketplace add AmeyaKasture/claude-review-loop
claude plugin install review-loop@review-loop
```

From a local clone, use the clone's path instead
(`claude plugin marketplace add ~/claude-review-loop`). Start a new Claude
Code session afterwards so the plugin loads.

### 2. Run the setup skill

In Claude Code, inside the repository (new or existing):

```
/review-loop:setup
```

It surveys the repository, offers to add anything missing (linter,
type-checker, a first test, CI, `AGENTS.md`), creates a team review skill if
there is none, writes the settings file, and runs every check once to prove
it works. It asks before installing or writing anything, and never commits.

### 3. Check the config files

Setup tells you where it wrote each file. Read them and adjust anything
that does not match how you work:

| File | What to check |
|---|---|
| `.review-loop.json` (committed) or `~/.claude/review-loop/repos/<owner>__<repo>.json` (private) | The main config: `checks` (the commands each round runs), `review_skill` (the name of the skill reviewers apply), `max_rounds`, models, size limits. All keys are listed under [Per-repository settings](#per-repository-settings). |
| `.claude/skills/<review_skill>/SKILL.md` (or under `~/.claude/skills/`) | The review skill the config points to: which rules, mechanical checks, and lenses reviewers apply. |
| `~/.claude/review-loop/EXIT.md` | How the exit step writes your private author guide. Optional to edit. |

To change something, edit the file, or run `/review-loop:setup` again and
say what you want changed; it re-checks every command.

### 4. Use the review loop

Implement a change on a branch as usual, then:

```
/review-loop:review-loop            # review and fix until a round is clean
/review-loop:respond 1234           # after you push: answer PR #1234's review comments
/review-loop:exit                   # when done: update your author guide from what was missed
```

The loop never commits or pushes; you do.

## How it works

1. **Implement** in your main session as usual.
2. **Loop.** Read-only reviewer agents with no author context apply your team's
   review skill (for example `branch-review`) and the repository's rule files
   (`AGENTS.md`, `CLAUDE.md`, `BUGBOT.md`). Your session sorts every finding:
   - **valid**: fix it, and record why the author session missed it;
   - **not valid**: keep the code, with the reason in the report;
   - **doubtful**: keep the code, listed first in the report as a possible
     design difference.
   It repeats until a round is clean, then a separate `claude -p` process runs a
   final gate review.
3. **Push** yourself. The loop never commits or pushes.
4. **Respond.** The same thread ingests the PR's open comments, sorts them the
   same way, sends any fixes through fresh review again, and drafts replies.
   It posts them only after you have pushed and said yes.
5. **Exit.** For every valid finding the author session answers "why did I
   miss it?" (not covered, unclear, ignored, missing context, unclear spec,
   one-off). Following your `~/.claude/review-loop/EXIT.md`, it then revises
   the **author guide**, a topic-organised instructions document, as a whole
   (rewrite, merge, move to a checklist, delete), shows you the diff, and
   applies it on your yes. The guide is a private local file per repository,
   optionally mirrored to a versioned store of your choice. A SessionStart
   hook loads it into future author sessions for that repository. Reviewers
   never see it.

Everything runs locally. Nothing is sent anywhere except the model calls
Claude Code itself makes, and PR replies you explicitly approve.

## Per-repository settings

Either commit `.review-loop.json` at the repository root, or keep the settings
private in `~/.claude/review-loop/repos/<owner>__<repo>.json` (no change to the
team repository). Command-line flags override both.

```json
{
  "review_skill": "branch-review",
  "checks": ["npm run typecheck", "npm run lint", "npm test -- --changed"],
  "max_rounds": 4,
  "gate": true,
  "reviewer_model": "opus",
  "gate_model": "opus",
  "size_limits": { "commits": 20, "files": 60, "lines": 3000 },
  "reply_prefix": "[AI] ",
  "exit_instructions": "~/.claude/review-loop/EXIT.md"
}
```

| Key | Default | Meaning |
|---|---|---|
| `checks` | none | Shell commands every round runs (fast lint, typecheck, unit tests). |
| `review_skill` | none | A skill in `.claude/skills/` or `~/.claude/skills/` that every reviewer applies. |
| `max_rounds` | 5 | Round cap. |
| `strict` | false | Block on minor findings too, not only major and blocker. |
| `gate` | true | Finish with a review in a separate `claude -p` process. |
| `reviewer_model`, `gate_model` | session model | Models for reviewers and the gate. |
| `size_limits` | 20 commits, 100 files, 5000 lines | Above these, the loop asks before round 1. |
| `reply_prefix` | `"[AI] "` | Prefix for drafted PR replies. |
| `exit_instructions` | `~/.claude/review-loop/EXIT.md` | Your rules for revising the author guide. |
| `go_module`, `go_tags`, `go_reverse_depth` | auto, none, 1 | Go only: see [Scoped Go checks](#scoped-go-checks). |

`rl.py config --probe` shows which file is in effect, flags unknown keys or a
missing review skill, and runs each check once. More examples (TypeScript,
Python, a Go monorepo) are in [`examples/`](examples/).

## Scoped Go checks

Put `{go_packages}` in a check and the engine fills it in each round with:

| Source | Packages |
|---|---|
| The diff | Every package with a changed `.go` file. |
| The import graph (`go list`) | Packages that import those, to `go_reverse_depth` levels (default 1), including test imports. |
| The main session | Packages it declared with `rl.py scope --add`: links the import graph cannot see (interfaces, registries, reflection, generated code, shared config or SQL, tests elsewhere). |
| Reviewers | A `test-gap` finding naming a missing package; once accepted, it is added with `scope --add`. |

The main session can widen the scope, never narrow it. Keep a full
`go build ./...` alongside, so a broken caller anywhere still fails
compilation. A round with no Go change skips scoped checks.

```json
"go_module": "services/api",
"go_tags": "unit",
"go_reverse_depth": 1,
"checks": [
  "cd services/api && go build ./...",
  "cd services/api && go vet -tags unit {go_packages}",
  "cd services/api && go test -tags unit -count=1 {go_packages}"
]
```

`go_module` is the module directory relative to the repository root (default:
the `go.mod` with the most changed files); `go_tags` are build tags for
`go list`. `rl.py scope` prints the current scope with the reason for each
package.

## Author guide store

By default the author guide is a local file. To keep every version
somewhere else (a private git repository, a synced folder, a notes or memory
service), give review-loop two shell commands in
`~/.claude/review-loop/settings.json`:

```json
{
  "guide_store": "auto",
  "guide_pull_cmd": "git-store.sh pull",
  "guide_push_cmd": "git-store.sh push"
}
```

Both commands get `RL_REPO_SLUG` (`owner/repo`). Pull prints the newest
guide (nothing if there is none). Push gets `RL_GUIDE_FILE`,
`RL_HISTORY_FILE` (may be empty), and `RL_MARKER`, and may print a version
id. `guide_store` is `auto` (use the commands, fall back to the local copy),
`command` (a failed save is an error), or `file` (ignore the commands).
[`examples/guide-store/git-store.sh`](examples/guide-store/git-store.sh) is a
complete store backed by git.

## Private files

| Path | What it is |
|---|---|
| `~/.claude/review-loop/EXIT.md` | Your instructions for the exit skill (created from a template on first use). |
| `~/.claude/review-loop/guides/<owner>__<repo>.md` | The author guide (or the local copy of it, with an external store). |
| `~/.claude/review-loop/guides/<owner>__<repo>.history.md` | Why each loop's findings were missed, and what changed in the guide. |
| `~/.claude/review-loop/guides/_global.md` | Guidance that applies to every repository. |
| `~/.claude/review-loop/repos/<owner>__<repo>.json` | Private per-repository settings. |
| `~/.claude/review-loop/settings.json` | Global settings: the author guide store. |
| `<repo>/.review-loop/` | Loop state, briefs, reports, PR reply drafts (git-excluded). |

## Repository layout

| Path | What it is |
|---|---|
| `plugins/review-loop/skills/review-loop/` | The loop protocol, the `rl.py` state engine, the gate script, brief, schema, rubric. |
| `plugins/review-loop/skills/respond/` | Answer PR comments after a push. |
| `plugins/review-loop/skills/exit/` | Miss analysis and author-guide revision; includes the `EXIT.md` template. |
| `plugins/review-loop/skills/setup/` | Survey a repository, add missing tooling, and write its settings and review skill; includes the review-skill template and per-stack tooling defaults. |
| `plugins/review-loop/agents/` | `fresh-reviewer` and `finding-verifier`: read-only, no MCP tools. |
| `plugins/review-loop/hooks/hooks.json` | Stop guard and SessionStart author-guide hook. |
| `examples/` | Example settings for common stacks, and an example guide store. |
| `tests/test_engine.sh` | Offline engine tests with no model calls. |
| `docs/SETUP.md` | Agent-run installation and setup. |
| `docs/DESIGN.md` | Why each safeguard exists. |
| `docs/architecture.html` | The whole workflow, as a page. |

## Development

```
bash tests/test_engine.sh
claude plugin validate --strict .
claude plugin validate --strict plugins/review-loop
```

## License

Apache-2.0.
