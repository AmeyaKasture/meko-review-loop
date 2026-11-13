# Meko Review Loop

An agent-agnostic review-and-fix loop for AI coding agents. It turns "a
second AI with a fresh context always finds bugs" into a bounded, repeatable
process, and it makes the author agent better at not writing those bugs in
the first place. With [Meko](https://mekodata.ai), a local memory engine,
what the author agent learns is kept as versioned, searchable memory that
every agent you use can recall.

It works with Claude Code, Codex, Cursor, Gemini CLI, and any agent that can
follow a Markdown skill and run shell commands. Reviewers can be a different
agent from the author, or a different vendor's model.

## Getting started

### 1. Install

```
git clone https://github.com/AmeyaKasture/meko-review-loop.git ~/meko-review-loop
~/meko-review-loop/install.sh --agent <your agent>
```

| Agent | `--agent` | What it installs |
|---|---|---|
| Claude Code | `claude` | The plugin: skills, reviewer subagents, and hooks. |
| Codex | `codex` | Skills in `~/.codex/skills/`. |
| Cursor | `cursor` | Skills in `~/.cursor/skills/`. |
| Gemini CLI | `gemini` | Skills in `~/.gemini/skills/`. |
| Any other | `agents`, or `--dir <its skills directory>` | Skills in `~/.agents/skills/` (or the directory you name). |

Add `--project <repo>` to install into one repository instead of your home
directory. Re-run the same command to update.

### 2. Run the setup skill

In your agent, inside the repository (new or existing), run the setup skill:
`/review-loop:setup` in Claude Code, or ask your agent to "use the
review-loop-setup skill".

It surveys the repository, offers to add anything missing (linter,
type-checker, a first test, CI, `AGENTS.md`), checks for Meko and offers to
install and connect it, asks which agents should review, creates a team
review skill if there is none, writes the settings file, and runs every
check once to prove it works. It asks before installing or writing
anything, and never commits.

### 3. Check the config files

Setup tells you where it wrote each file. Read them and adjust anything
that does not match how you work:

| File | What to check |
|---|---|
| `.review-loop.json` (committed) or `~/.review-loop/repos/<owner>__<repo>.json` (private) | The main config: `checks` (the commands each round runs), `review_skill` (the skill reviewers apply), who reviews (`reviewer_agent`, `gate_agent`, models), `max_rounds`, size limits. All keys are under [Settings](#settings). |
| `<skills dir>/<review_skill>/SKILL.md` | The review skill the config points to: which rules, mechanical checks, and lenses reviewers apply. |
| `~/.review-loop/settings.json` | Where your author guide lives: Meko or local files. |
| `~/.review-loop/EXIT.md` | How the exit step writes your private author guide. Optional to edit. |

To change something, edit the file, or run the setup skill again and say
what you want changed; it re-checks every command.

### 4. Use the review loop

Implement a change on a branch as usual, then run the skills (Claude Code
names shown; in other agents ask for `review-loop`, `review-loop-respond`,
and `review-loop-exit`):

```
/review-loop:review-loop            # review and fix until a round is clean
/review-loop:respond 1234           # after you push: answer PR #1234's review comments
/review-loop:exit                   # when done: update your author guide from what was missed
```

The loop never commits or pushes; you do.

## How it works

1. **Implement** in your main session as usual.
2. **Loop.** Reviewers with a fresh context and no edit rights apply your
   team's review skill and the repository's rule files (`AGENTS.md`,
   `CLAUDE.md`, `REVIEW.md`, `BUGBOT.md`). They are subagents of your agent,
   or separate processes of any agent CLI. Your session sorts every finding:
   - **valid**: fix it, and record why the author session missed it;
   - **not valid**: keep the code, with the reason in the report (a neutral
     verifier must agree for serious findings);
   - **doubtful**: keep the code, listed first in the report as a possible
     design difference.
   It repeats until a round is clean, then a separate agent process, ideally
   another vendor's model, runs a final gate review.
3. **Push** yourself. The loop never commits or pushes.
4. **Respond.** The same thread ingests the PR's open comments, sorts them the
   same way, sends any fixes through fresh review again, and drafts replies.
   It posts them only after you have pushed and said yes.
5. **Exit.** For every valid finding the author session answers "why did I
   miss it?" (not covered, unclear, ignored, missing context, unclear spec,
   one-off). Following your `~/.review-loop/EXIT.md`, it then revises the
   **author guide**, a topic-organised instructions document, as a whole
   (rewrite, merge, move to a checklist, delete), shows you the diff, and
   applies it on your yes. Future author sessions in that repository start
   with the guide. Reviewers never see it.

Everything runs locally. Nothing is sent anywhere except the model calls
your agents make, and PR replies you explicitly approve.

## Supported agents

The protocol is plain Markdown and the engine (`rl.py`) is plain Python, so
any agent that can follow a skill and run shell commands can drive the loop.
The pieces that depend on the agent:

| | Claude Code | Codex, Cursor, Gemini CLI, others |
|---|---|---|
| Skills | Plugin, namespaced (`/review-loop:…`) | Copied by `install.sh` (`review-loop-…`) |
| Reviewers | `fresh-reviewer` subagents (no edit or MCP tools), or any CLI below | The agent's own read-only subagents, or any CLI below |
| Gate and verifier | Any CLI below | Any CLI below |
| Author guide at session start | SessionStart hook, automatic | `rl.py learnings-context --text`, or the agent's own startup hook |
| Stop guard (keeps a running loop going) | Stop hook | Not available; `rl.py status` says what is next |

Reviewer and gate CLIs (`reviewer_agent`, `gate_agent`) run through
`scripts/headless-review.sh`:

| Value | Runs | Notes |
|---|---|---|
| `claude` | `claude -p`, schema-enforced JSON | No MCP servers, no hooks, no edit tools. Tested. |
| `codex` | `codex exec --sandbox workspace-write` | The reviewer writes its findings file. |
| `gemini` | `gemini -p --output-format json --yolo` | |
| `cursor` | `cursor-agent -p --output-format json --force` | |
| `custom` | Your `headless_command` template (`{prompt_file}`, `{model}`, `{output}`) | For any other CLI. |

Only the `claude` preset is exercised end to end by the maintainers so far;
the others follow each CLI's documented headless mode. If your version's
flags differ, use `custom`. For CLIs other than `claude`, the script cannot
switch off memory connectors, so give reviewers a profile without them.

## Meko

[Meko](https://mekodata.ai) is a local, offline memory engine for agents. It
is optional; without it the author guide is a local file.

With Meko, each repository's author guide lives in a datapack
(`review-loop guide <owner>/<repo>`): every approved revision is a new
version of `author-guide.md`, and each loop's miss history becomes a
searchable memory. Any agent you connect to Meko (Claude Code, Codex,
Cursor, OpenCode, Kiro) can recall it, so what one agent learned is not
locked into one tool. Reviewers are never connected: they must not see the
guide.

**Let your agent set it up.** The setup skill checks whether Meko is
installed and running. If it is not, it asks whether to install it, and on
your yes it runs the official installer, offers to turn off usage statistics
and update checks for a fully offline setup, starts Meko, offers
`meko agent connect <your agent>`, and points the author guide at it.

**By hand:**

```
curl -fsSL https://mekodata.ai/install.sh | sh
meko agent connect <claude | codex | cursor | opencode | kiro>   # optional
```

then in `~/.review-loop/settings.json`:

```json
{
  "guide_store": "auto",
  "guide_pull_cmd": "python3 <install dir>/review-loop/scripts/stores/meko_store.py pull",
  "guide_push_cmd": "python3 <install dir>/review-loop/scripts/stores/meko_store.py push"
}
```

`auto` falls back to the local copy whenever Meko is down, and says so;
`command` makes a failed save an error. `meko_store.py check` tells you
whether Meko is reachable.

## Settings

Either commit `.review-loop.json` at the repository root, or keep the settings
private in `~/.review-loop/repos/<owner>__<repo>.json` (no change to the team
repository). Command-line flags override both.

```json
{
  "review_skill": "branch-review",
  "checks": ["npm run typecheck", "npm run lint", "npm test -- --changed"],
  "max_rounds": 4,
  "reviewer_agent": "subagent",
  "gate_agent": "codex",
  "gate": true,
  "size_limits": { "commits": 20, "files": 60, "lines": 3000 },
  "reply_prefix": "[AI] "
}
```

| Key | Default | Meaning |
|---|---|---|
| `checks` | none | Shell commands every round runs (fast lint, typecheck, unit tests). |
| `review_skill` | none | A skill name, looked up in `.agents/skills`, `.claude/skills`, `.cursor/skills`, `.codex/skills`, `.gemini/skills` (repository, then home), or a path to a Markdown file. |
| `reviewer_agent` | `subagent` | `subagent`, or a CLI (`claude`, `codex`, `gemini`, `cursor`, `custom`) that runs each reviewer as its own process. |
| `gate_agent` | `claude` | The CLI for the gate review and the finding verifier. |
| `headless_command` | none | Command template for `custom`. |
| `reviewer_model`, `gate_model` | the agent's default | Models, in the chosen agent's naming. |
| `max_rounds` | 5 | Round cap. |
| `strict` | false | Block on minor findings too, not only major and blocker. |
| `gate` | true | Finish with a gate review in a separate process. |
| `size_limits` | 20 commits, 100 files, 5000 lines | Above these, the loop asks before round 1. |
| `reply_prefix` | `"[AI] "` | Prefix for drafted PR replies. |
| `exit_instructions` | `~/.review-loop/EXIT.md` | Your rules for revising the author guide. |
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

## Other guide stores

Meko is one store. Any other works through the same two commands in
`~/.review-loop/settings.json` (`guide_pull_cmd`, `guide_push_cmd`). Both
get `RL_REPO_SLUG` (`owner/repo`). Pull prints the newest guide (nothing if
there is none) and exits non-zero if the store is unavailable. Push gets
`RL_GUIDE_FILE`, `RL_HISTORY_FILE` (may be empty), and `RL_MARKER`, and may
print a version id. `guide_store` is `auto`, `command`, or `file`.
[`examples/guide-store/git-store.sh`](examples/guide-store/git-store.sh) is a
complete store backed by a private git repository.

## Private files

| Path | What it is |
|---|---|
| `~/.review-loop/EXIT.md` | Your instructions for the exit skill (created from a template on first use). |
| `~/.review-loop/guides/<owner>__<repo>.md` | The author guide (or the local copy of it, with Meko or another store). |
| `~/.review-loop/guides/<owner>__<repo>.history.md` | Why each loop's findings were missed, and what changed in the guide. |
| `~/.review-loop/guides/_global.md` | Guidance that applies to every repository. |
| `~/.review-loop/repos/<owner>__<repo>.json` | Private per-repository settings. |
| `~/.review-loop/settings.json` | Global settings: the author guide store. |
| `<repo>/.review-loop/` | Loop state, briefs, reports, PR reply drafts (git-excluded). |

An existing `~/.claude/review-loop/` from earlier versions is used as is
until `~/.review-loop/` exists. `REVIEW_LOOP_HOME` overrides both.

## Repository layout

| Path | What it is |
|---|---|
| `install.sh` | Installs for Claude Code (plugin) or copies the skills for any other agent. |
| `plugins/review-loop/skills/review-loop/` | The loop protocol, the `rl.py` state engine, the headless reviewer runner, the Meko store, brief, schema, rubric. |
| `plugins/review-loop/skills/setup/` | Survey a repository, add missing tooling, set up Meko, and write the settings and review skill. |
| `plugins/review-loop/skills/respond/` | Answer PR comments after a push. |
| `plugins/review-loop/skills/exit/` | Miss analysis and author-guide revision; includes the `EXIT.md` template. |
| `plugins/review-loop/agents/` | Reviewer and verifier instructions (Claude Code subagents; also used by the headless runner). |
| `plugins/review-loop/hooks/hooks.json` | Claude Code hooks: Stop guard and SessionStart author guide. |
| `examples/` | Example settings for common stacks, and an example git-backed guide store. |
| `tests/test_engine.sh` | Offline engine tests with no model calls. |
| `tests/test_meko_live.sh` | Live guide-store tests against a running Meko (skips when Meko is not running). |
| `docs/SETUP.md` | Agent-run installation and setup. |
| `docs/DESIGN.md` | Why each safeguard exists. |
| `docs/architecture.html` | The whole workflow, as a page. |

## Development

```
bash tests/test_engine.sh
bash tests/test_meko_live.sh
claude plugin validate --strict .
claude plugin validate --strict plugins/review-loop
```

## License

Apache-2.0.
