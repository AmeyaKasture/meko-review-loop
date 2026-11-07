# meko-review-loop

A Claude Code plugin for a simple observation: **a second AI with a fresh
context finds bugs that the AI which wrote the code cannot see.**
while trying to make the orchestrator learn inherent coding strategies using meko

`review-loop` turns that observation into a bounded, repeatable workflow:

1. Your main Claude session writes the code.
2. Independent reviewer agents with **no access to the author's reasoning**
   review it, using your team's own review skill and rule files.
3. The main session sorts every finding into **valid**, **not valid**, or
   **doubtful**, fixes the valid ones, and the loop repeats until a round is clean.
4. A final **gate review runs in a completely separate Claude process**.
5. You push. The same session then **answers the PR's review comments**.
6. At the end, the main session asks itself **why it missed each bug**, and
   revises a private **author guide** so it does not write that class of bug
   again. The guide is stored in your **local Meko** and loaded into every
   future main session. Reviewers never see it.

Everything runs on your machine. Nothing leaves it except the model calls
Claude Code itself makes, and PR replies you explicitly approve.

---

## Contents

- [How it works](#how-it-works)
- [Requirements](#requirements)
- [Install](#install)
- [Quick start](#quick-start)
- [The three commands](#the-three-commands)
  - [`/review-loop:review-loop`: the review loop](#review-loopreview-loop-the-review-loop)
  - [`/review-loop:respond`: answer PR comments](#review-looprespond-answer-pr-comments)
  - [`/review-loop:exit`: why did I miss it?](#review-loopexit-why-did-i-miss-it)
- [Triage: valid, not valid, doubtful](#triage-valid-not-valid-doubtful)
- [The report](#the-report)
- [The author guide and Meko](#the-author-guide-and-meko)
- [Who sees what (independence)](#who-sees-what-independence)
- [Configuration](#configuration)
- [Files and where they live](#files-and-where-they-live)
- [Engine command reference (`rl.py`)](#engine-command-reference-rlpy)
- [Testing](#testing)
- [Troubleshooting](#troubleshooting)
- [Limitations](#limitations)
- [Repository layout](#repository-layout)
- [License](#license)

---

## How it works

```
                 ┌──────────────────────────── main session (author) ───────────────────────────┐
 session start → │ AGENTS.md (via CLAUDE.md) + author guide (from local Meko)  →  write the code │
                 └──────────────┬─────────────────────────────────────────────────────▲─────────┘
                                │ /review-loop:review-loop                            │
                                ▼                                                     │
   ┌─ round N ──────────────────────────────────────────────────────┐                 │
   │ snapshot tree → run checks → generate briefs                    │                 │
   │ fresh-reviewer ×2 (parallel, read-only, no MCP, no author info) │                 │
   │   apply your review skill (e.g. branch-review) + AGENTS.md      │                 │
   │ → ingest JSON → triage: valid | not valid | doubtful            │                 │
   │ → fix valid + record why it was missed → status                 │                 │
   └───────────────┬───────────── repeat until a round is clean ─────┘                 │
                   ▼                                                                   │
   gate: separate `claude -p` process, no hooks, no MCP, same tree                      │
                   ▼                                                                   │
   report (design differences first) → you push                                         │
                   ▼                                                                   │
   /review-loop:respond <PR>: ingest open comments → triage → fix → re-review → drafts  │
                   ▼                                                                   │
   /review-loop:exit: why was each bug missed? → revise author guide → diff → your yes ─┘
                      (new version saved in local Meko)
```

The **state engine** (`rl.py`) owns everything that should be deterministic
rather than left to the model's memory: snapshots, check runs, the findings
ledger, duplicate and oscillation detection, the convergence decision, the
report, PR comment ingestion, and the author guide store. The skills tell
Claude when to call it; `rl.py status` always prints the authoritative next
step.

---

## Requirements

| Need | Why |
|---|---|
| Claude Code (CLI) | Runs the skills, agents, and hooks. |
| `git`, `python3` (3.9+), `jq` | Engine, snapshots, gate script. |
| `gh` (signed in) | Only for checking out PRs and for `respond`. |
| Offline Meko (optional, recommended) | The author guide's home. Without it, the guide is a local file. |
| macOS 14+ on Apple Silicon, or Linux | Only for Meko. |

---

## Install

### 1. The plugin (local)

```
git clone git@github.com:AmeyaKasture/claude-review-loop.git ~/claude-review-loop
```

Then, inside Claude Code:

```
/plugin marketplace add ~/claude-review-loop
/plugin install review-loop@review-loop
```

Or from a shell: `claude plugin marketplace add ~/claude-review-loop && claude plugin install review-loop@review-loop`.
Claude Code reads the plugin straight from that folder, so a `git pull` updates it.

### 2. Local Meko (optional, recommended)

```
curl -fsSL https://mekodata.ai/install.sh | sh        # user-level, no root; serves 127.0.0.1:7778
```

Keep it local only (no usage stats, no scheduled update checks):

```
cat > ~/.meko/config.toml <<'EOF'
[call_home]
enabled = false

[update]
channel = "off"
auto = false
EOF
~/.local/bin/meko restart
```

Store the author guide in Meko, and let your main sessions search it:

```
mkdir -p ~/.claude/review-loop
echo '{"guide_store": "meko"}' > ~/.claude/review-loop/settings.json
~/.local/bin/meko agent connect claude
```

Do not run `meko cloud_link` or turn on `meko memory-extractor` if you want
everything to stay on your machine.

### 3. Your exit instructions

The first `/review-loop:exit` creates `~/.claude/review-loop/EXIT.md` from a
template. Edit it to define how your author guide is structured
([details](#exitmd-your-instructions)).

---

## Quick start

```
cd ~/my-repo
# ... implement something with Claude ...

/review-loop:review-loop                 # review loop on branch + uncommitted changes vs main
# read the report; answer the design differences; commit and push yourself

/review-loop:respond 1234                # once PR #1234 has review comments
/review-loop:exit                        # when the PR is done
```

Variants:

```
/review-loop:review-loop 1234                     # check out PR #1234 and harden it
/review-loop:review-loop main --strict            # minor findings block too
/review-loop:review-loop --gate-model sonnet      # gate on a different model
/review-loop:review-loop --review-skill branch-review --checks "go build ./...;;go vet ./..."
```

---

## The three commands

### `/review-loop:review-loop`: the review loop

**Phase 0: set up.**
- Resolves the target: a PR (checked out with `gh`), a base ref, or the
  current branch plus uncommitted changes against the default branch.
- Writes the **intent**: the PR description or your stated requirements.
  It contains requirements only, never the author's reasoning.
- `rl.py init` loads settings, then applies a **size gate**: by default it
  asks first if the change exceeds 20 commits, 100 files, or 5,000 lines.
- Chooses **checks** (build, lint, targeted tests) and **probes** them, so a
  missing tool cannot block convergence forever.

**Phase 1: review round.**
- **Go test scope.** Checks that contain `{go_packages}` run only on the
  packages that matter (see [Scoped Go checks](#scoped-go-checks)). Before
  each round, the main session declares any extra packages it knows are
  affected: `rl.py scope --add ./src/x --note "registry lookup"`.
- Snapshots the whole working tree, untracked files included, as a commit
  under `refs/review-loop/round-N`, using a temporary git index. Your HEAD,
  index, and stash are never touched.
- Runs the checks and generates one **brief** per reviewer.
- Spawns reviewers in parallel. Each gets a one-line prompt: the path to its
  brief.
  - **Round 1:** two full reviewers with different lenses.
  - **Later rounds:** one fresh full reviewer, plus one **delta** reviewer
    that checks only the fixes since the previous round, where regressions
    hide.
- Reviewers apply your **team review skill** if one is configured, read every
  governing **rule file**, read the check logs (they do not re-run suites),
  try to reproduce defects in a scratch directory, and write schema-validated
  JSON. If an affected package is missing from the test scope, they report it
  as a `test-gap` finding and it is added for the next round.
- `verify-tree` confirms no reviewer modified the repository, then the
  output is ingested into the ledger.

**Phase 2: triage**, into [three outcomes](#triage-valid-not-valid-doubtful).

**Phase 3: fix** the valid findings. While the context is fresh, record
*why the author session missed each one* (`--cause`). Then
`rl.py status` decides: another round, the gate, or the end.

**Phase 4: gate.** `fresh-session-review.sh` starts a separate
`claude -p` process with no session history, no MCP servers, no hooks, and a
JSON schema enforced on its output. A different model is recommended.

**Phase 5: report and ask.** The report lists design differences first. In
an interactive session each one becomes a multiple-choice question with a
recommended option. Your answers are applied; if they change code, one more
delta round runs.

**Convergence.** A review round accepts nothing blocking and the checks pass,
then the gate accepts nothing blocking on the identical tree. Blocking means
`major` or `blocker` by default; with `--strict`, `minor` blocks too. A round
cap (default 5) always ends the loop.

### `/review-loop:respond`: answer PR comments

Run it in the same session after you push.

1. `rl.py reopen --pr N`, `rl.py snapshot --kind pr`, and `rl.py pr-ingest`
   read the PR's **unresolved** review threads, review bodies, and PR
   comments through `gh` (read-only). Resolved threads, your own comments, and
   comments already ingested are skipped.
2. Each comment gets a severity from its own tag:

   | Comment looks like | Severity |
   |---|---|
   | `[CRITICAL]`, "Critical Severity" | blocker |
   | `[AI][Warning]`, `[WARNING]`, `**major** —`, "High Severity" | major |
   | `[AI][Suggestion]`, `**minor** —`, "Medium Severity" | minor |
   | `**nit** —`, "Low Severity" | nit |
   | `[QUESTION]`, or untagged text ending with `?` | question |
   | Untagged inline comment | major (a change request) |
   | Untagged PR-level comment | question |

3. Every comment is triaged with the same three outcomes. The whole thread is
   read first.
4. Valid comments are fixed, and the miss is diagnosed. Any fix sends the code
   back through a fresh review round and the gate; PR-driven fixes are never
   exempt.
5. `rl.py pr-replies` writes drafts to `.review-loop/pr-replies.md` and
   **posts nothing**:

   | Outcome | Draft reply |
   |---|---|
   | Valid | `[AI] Fixed: <what changed>.` |
   | Not valid | `[AI] Keeping this as is: <evidence>` |
   | Doubtful | `[AI] Possible design difference: <why>. Options: (A) … / (B) … Keeping the current design until we agree.` |
   | Decided | `[AI] Decided: <your answer>.` |
   | Deferred | `[AI] Out of scope for this PR: <reason>` |

6. You commit and push. After you confirm, `rl.py pr-replies --post` replies
   in each thread. It **refuses while fixes are uncommitted or unpushed**,
   never posts the same reply twice, and never resolves threads, approves, or
   requests changes.

### `/review-loop:exit`: why did I miss it?

Exit does not dump lessons into a file. It diagnoses each miss, then edits
one coherent document.

1. `rl.py guide-pull` fetches the newest author guide from Meko (or the local
   copy).
2. `rl.py export-learnings` gathers the loop's outcomes: fixes, not-valid
   reasons, design decisions, PR comment results, and any fix without a
   diagnosis.
3. For every valid finding, the main session answers **"why did I miss
   this?"**. It first searches the guide and the `AGENTS.md` chain for guidance
   that already covered it, then picks one cause:

   | Cause | Meaning | Change to the guide |
   |---|---|---|
   | `not-covered` | No guidance existed | Add guidance in the topic section where an author would look, stating when it applies and why. |
   | `covered-unclear` | Guidance existed but was vague or buried (must quote it) | Rewrite that item; never add a second one. |
   | `covered-ignored` | Guidance was clear but was not applied (must quote it) | No new text. Move it to "Before you finish", tie it to the moment it matters, or make it a concrete check. |
   | `context-gap` | Needed knowledge of the code or system | Add a "Where to look" pointer. |
   | `spec-gap` | The requirement was unclear | Optionally a question under "Before you start". |
   | `one-off` | A slip | Nothing. |
   | (design difference you decided) | | Recorded under "Design decisions", with the reason. |

4. It rewrites the guide **as a whole document**. Several findings with one
   root cause become one change, overlaps are merged, superseded text is
   deleted, and the guide stays within its size budget. Ids, dates, and
   counts never go in the body; they go in the history.
5. It shows you the miss table and a `diff -u` of the guide, and asks you to
   apply it, edit it first, or skip it.
6. On apply, `rl.py guide-push` saves a **new version in Meko**, stores the
   loop's history as a Meko memory, refreshes the local copy, and appends the
   local history file. The same loop is never recorded twice.
7. Items that are clearly team rules are proposed as exact `AGENTS.md` text in
   the reply. Exit never edits `AGENTS.md`.

#### EXIT.md: your instructions

`~/.claude/review-loop/EXIT.md` (or the path in `exit_instructions`) controls
every exit. The default template defines:

- **Sections**, in order: `Before you start`, `While writing` (one `###` per
  topic: error handling, persistence, concurrency, hot paths, tests, naming),
  `Where to look`, `Design decisions`, and `Before you finish` (a checklist of
  at most 10 items).
- **Style:** imperative and specific, each item saying when it applies and
  why. `AGENTS.md` rules are referenced, not copied.
- **Budget:** about 7,000 characters.
- **Approval:** always show the diff and wait for a yes.
- **Destinations:** the global guide for cross-repository items; `AGENTS.md`
  proposals only in the reply.

Where EXIT.md conflicts with the skill, EXIT.md wins, except for two rules:
the guide never goes to reviewers or into the repository, and it never goes to
a cloud service.

---

## Triage: valid, not valid, doubtful

| Outcome | Code | Report section | Command |
|---|---|---|---|
| **Valid** | Fixed | "Valid: fixed", with why it was missed | `rl.py triage ID accept --note "..."` → `rl.py fixed ID --note "..." --cause <cause> --why "..."` |
| **Not valid** | Kept | "Not valid: code kept as is", with evidence | `rl.py triage ID reject --note "<evidence>"` |
| **Doubtful** | Kept | **First**: "Possible design differences", with options | `rl.py triage ID doubtful --note "<why>" [--option A --option B --recommend A]` |

Two bookkeeping verdicts exist as well: `duplicate --duplicate-of ID` and
`defer --note` (real but pre-existing, or out of scope).

Rules the engine enforces:

- **Calling a `major` or `blocker` finding (or a reproduced `minor`) not
  valid requires a neutral `finding-verifier` agent**, which returns
  `REAL`, `NOT_REAL`, or `UNCERTAIN`. This is the main guard against the
  author explaining real bugs away.
- `reject`, `doubtful`, and `defer` need a reason.
- **Oscillation:** a finding that recurs after being fixed is a regression and
  is accepted again; on the second recurrence it becomes a question for you.
- A same-round duplicate from another reviewer is never a regression.
- Every finding is flagged `in-change`, `near-change`, or `outside-change`.
- Doubtful and not-valid items never block; the loop keeps iterating on the
  valid ones.
- After a clean round, code edits are forbidden, so what ships is what the
  last clean review saw. Valid minor findings become optional fixes.

Severities: `blocker`, `major` (any `AGENTS.md` violation defaults here),
`minor`, `nit`, and `question` (needs author input; never blocks).

---

## The report

`rl.py report` writes `.review-loop/report-<timestamp>.md`:

```
# Review loop report: Converged
- Target, base, final snapshot, rounds, threshold, gate, outcome
## Possible design differences (code kept as is; your call)
### D1. <title>  (R2-3, question - PR comment by @carol)
- A. Keep the current design (no change) **(recommended)**
- B. Change it as the reviewer suggests
## Decisions you made
## Valid: fixed
- R1-1 [major] cmd/export.go:212 <title> - <what changed>
  - Missed because: covered-ignored - <why>
## Not valid: code kept as is (with reason)
## Optional fixes (valid, below threshold, not applied)
## Deferred (pre-existing or out of scope)
## Rounds        (table: kind, reviewers, checks, found, fixed, not valid, doubtful)
## Process notes
## How to inspect   (git diff base..refs/review-loop/round-N; rl.py show ID)
```

---

## The author guide and Meko

**What the main session reads before it writes code:**

```
session start
  ├─ CLAUDE.md → AGENTS.md            repository rules, loaded by Claude Code
  └─ SessionStart hook → rl.py learnings-context
        ├─ ~/.claude/review-loop/guides/_global.md       cross-repository guidance
        └─ local Meko: newest author-guide.md            fallback: the local copy
```

**Where things live:**

| What | Where | Why |
|---|---|---|
| Author guide | Local Meko: datapack `review-loop guide <owner>/<repo>`, document `author-guide.md`, one version per approved revision | One home that any agent connected to Meko can read, with full history. |
| Why each loop's bugs were missed | The same datapack, one memory per loop, plus `…history.md` locally | Searchable later without cluttering the guide. |
| Local copy | `~/.claude/review-loop/guides/<owner>__<repo>.md` | Refreshed on every read; used only when Meko is down, and the session is told so. |
| Rules (`AGENTS.md`; `CLAUDE.md` and `BUGBOT.md` may be symlinks) | The repository | Versioned with each branch, and reviewers need them. |

`guide_store` in `~/.claude/review-loop/settings.json`:

| Value | Behaviour |
|---|---|
| `meko` | Meko required. Saving fails loudly if Meko is down; reading falls back to the local copy with a note. |
| `auto` (default) | Meko when it is running; otherwise local files. |
| `file` | Local files only. |

The engine talks to Meko directly, through `http://127.0.0.1:7778/mcp`
(`artifact_put`, `memory_add`, `datapack_create`, `conversation_create`)
and `/api/v1` (listing and reading artifacts), with the token from
`meko token`. So saving and loading never depend on the model remembering
to do it. Override the address with `MEKO_URL`, or the token with
`MEKO_TOKEN`.

---

## Who sees what (independence)

| Information | Main session | Reviewers + verifier | Gate process |
|---|:-:|:-:|:-:|
| Author guide, miss history | yes | **no** | **no** |
| Author reasoning, ledger, earlier reports | yes | **no** | **no** |
| Stated requirements (intent) | yes | yes | yes |
| `AGENTS.md` / `CLAUDE.md` / `BUGBOT.md` rules | yes | yes | yes |
| Team review skill (for example `branch-review`) | n/a | yes | yes |
| PR comments | yes | **no** | **no** |
| MCP tools (Meko, Slack, …) | yes | **blocked** | **blocked** |
| Can edit the repository | yes | **no** | **no** |

How this is enforced:

- The guide arrives through a **SessionStart** hook. Subagents do not run
  SessionStart, and the gate runs with `disableAllHooks`.
- `fresh-reviewer` and `finding-verifier` have `Edit`, `Write`,
  `NotebookEdit`, and every `mcp__*` tool removed.
- The gate uses `--strict-mcp-config`, `--no-session-persistence`, and no
  write tools.
- A reviewer's prompt is one line, the path to a script-generated brief. The
  orchestrator never summarises the change or mentions earlier fixes.
- Briefs and agent definitions forbid reading `~/.claude/review-loop/` or the
  loop's state, and forbid querying Meko.

These behaviours were checked live: a new main session quoted a guide stored
in Meko; a reviewer subagent reported neither the guide nor any MCP tool; and
a gate-style session saw nothing.

---

## Configuration

### Per-repository settings

Read from the first of:

1. `<repo>/.review-loop.json` (commit it if the team wants shared settings);
2. `~/.claude/review-loop/repos/<owner>__<repo>.json` (private: no change to
   the team repository).

Command-line flags override both.

```json
{
  "review_skill": "branch-review",
  "checks": ["cd yb-voyager && go build ./...", "cd yb-voyager && go vet -tags unit ./cmd/... ./src/..."],
  "max_rounds": 4,
  "strict": false,
  "gate": true,
  "reviewer_model": "opus",
  "gate_model": "opus",
  "size_limits": { "commits": 20, "files": 60, "lines": 3000 },
  "reply_prefix": "[AI] ",
  "exit_instructions": "~/.claude/review-loop/EXIT.md"
}
```

| Key | Default | Effect |
|---|---|---|
| `review_skill` | none | A skill in `.claude/skills/` or `~/.claude/skills/` that every reviewer and the gate must apply. The brief adapts it to the loop's diff range, skips its summary and posting steps, and maps CRITICAL/WARNING/SUGGESTION/QUESTION to blocker/major/minor/question. |
| `checks` | auto-detected | Commands run on every snapshot. Errors are findings; warnings in touched files are major findings. |
| `max_rounds` | 5 | Round cap. One gate may run after it. |
| `strict` | false | Minor findings also block. |
| `gate` | true | Run the separate-process gate. |
| `reviewer_model`, `gate_model` | inherit | Models for reviewers and gate. A different gate model adds diversity. |
| `size_limits` | 20 commits / 100 files / 5,000 lines | Above these, the loop asks before starting. |
| `reply_prefix` | `[AI] ` | Prefix for PR replies. |
| `exit_instructions` | `~/.claude/review-loop/EXIT.md` | Your exit instructions. |

### Flags

| Flag | Effect |
|---|---|
| `[PR number \| PR URL \| base ref]` | What to review. |
| `--max-rounds N` | Round cap. |
| `--strict` | Minor findings block. |
| `--no-gate` | Skip the gate. |
| `--gate-model M`, `--reviewer-model M` | Models. |
| `--review-skill NAME` | Team review skill. |
| `--checks "a;;b"` | Exact check commands, separated by `;;`. |

### Scoped Go checks

Put `{go_packages}` in a check and the engine fills it in each round with:

| Source | Packages |
|---|---|
| The diff | Every package with a changed `.go` file. |
| The import graph (`go list`, about 0.5 s) | Packages that import those, to `go_reverse_depth` levels (default 1), including test imports. |
| The main session | Packages it declared with `rl.py scope --add`: links the import graph cannot see (interfaces, registries, reflection, generated code, shared config or SQL, tests elsewhere). |
| Reviewers | A `test-gap` finding naming a missing package; once accepted, it is added with `scope --add`. |

The main session can widen the scope, never narrow it: the engine always
computes the minimum itself. Keep a full `go build ./...` alongside, so a
broken caller anywhere still fails compilation. A round with no Go change
skips scoped checks. On yb-voyager's recent history this chose 7 to 10 of 57
packages; unit tests for one package take about 2 to 27 seconds.

```json
"go_module": "yb-voyager",
"go_tags": "unit",
"go_reverse_depth": 1,
"checks": [
  "cd yb-voyager && go build ./...",
  "cd yb-voyager && go vet -tags unit {go_packages}",
  "cd yb-voyager && go test -tags unit -count=1 {go_packages}"
]
```

| Key | Default | Effect |
|---|---|---|
| `go_module` | auto (the `go.mod` with most changed files) | Module directory, relative to the repository root. |
| `go_tags` | none | Build tags for `go list`. |
| `go_reverse_depth` | 1 | How many levels of importers to include. |

`rl.py scope` prints the current scope with the reason for each package.

### Global settings and environment

| Setting | Effect |
|---|---|
| `~/.claude/review-loop/settings.json` → `guide_store` | `meko`, `auto`, or `file` (see above). |
| `REVIEW_LOOP_HOME` | Moves `~/.claude/review-loop` (used by the tests). |
| `REVIEW_LOOP_GUIDE_STORE` | Overrides `guide_store`. |
| `MEKO_URL`, `MEKO_TOKEN` | Meko address and token. |

### Rule files

Briefs list every rule file governing the touched files, from each changed
file's directory up to the root: `REVIEW.md`, `AGENTS.md`, `CLAUDE.md`,
`BUGBOT.md`, `.cursor/BUGBOT.md`, and `CONTRIBUTING.md`. Symlinked copies
(for example `CLAUDE.md` → `AGENTS.md`) are listed once. A violated rule is a
`guideline` finding.

---

## Files and where they live

| Path | What it is |
|---|---|
| `<repo>/.review-loop/state.json` | The loop's ledger (git-excluded through `.git/info/exclude`). |
| `<repo>/.review-loop/rounds/N/` | Briefs, reviewer JSON, check logs, scratch space. |
| `<repo>/.review-loop/report-*.md` | Reports. |
| `<repo>/.review-loop/pr-replies.md` | Draft PR replies. |
| `<repo>/.review-loop/learnings.json` | The exit step's input. |
| `refs/review-loop/round-N` | Snapshot commits (not on any branch). |
| `~/.claude/review-loop/EXIT.md` | Your exit instructions. |
| `~/.claude/review-loop/settings.json` | `guide_store`. |
| `~/.claude/review-loop/repos/<owner>__<repo>.json` | Private per-repository settings. |
| `~/.claude/review-loop/guides/<owner>__<repo>.md` | Local copy of the author guide. |
| `~/.claude/review-loop/guides/<owner>__<repo>.history.md` | Local miss history. |
| `~/.claude/review-loop/guides/_global.md` | Cross-repository guidance. |
| Meko datapack `review-loop guide <owner>/<repo>` | Guide versions and history memories. |

To remove a loop's snapshots: `git for-each-ref --format='%(refname)' refs/review-loop/ | xargs -n1 git update-ref -d`.

---

## Engine command reference (`rl.py`)

`plugins/review-loop/skills/review-loop/scripts/rl.py`. The skills call it
for you; it is also handy for inspection. Run it from inside the repository.

| Command | Purpose |
|---|---|
| `init [--target T] [--base REF] [--intent-file F] [--session ID] [--max-rounds N] [--strict] [--no-gate] [--gate-model M] [--reviewer-model M] [--review-skill S] [--checks "a;;b"] [--force]` | Start a loop. Refuses to discard unsaved lessons without `--force`. |
| `detect-checks`, `set-checks "a;;b"`, `run-checks [--probe] [--timeout S]` | Choose, validate, and run checks. |
| `scope [--add PKG ...] [--note TEXT]` | Show the Go test scope, or widen it with packages the import graph cannot see. |
| `set-intent --file F` | Change the intent before round 1. |
| `snapshot [--kind review\|gate\|pr]` | Snapshot the tree for a new round. |
| `brief --mode full\|delta\|gate --name N [--lens TEXT]` | Generate a reviewer brief. |
| `ingest FILE`, `drop-reviewer NAME --reason R`, `verify-tree` | Ingest reviewer output, record an exclusion, check for tampering. |
| `triage ID accept\|reject\|doubtful\|defer\|ask\|duplicate [--note] [--verifier real\|not-real\|uncertain] [--option ...] [--recommend A] [--duplicate-of ID]` | Record a triage decision. |
| `fixed ID... [--note] [--cause C --why W --guide-ref R] [--lesson L]` | Mark fixed, with the miss diagnosis. |
| `miss ID --cause C --why W [--guide-ref R]`, `lesson ID TEXT` | Diagnose later; attach a lesson. |
| `status [-v] [--json]`, `show ID` | Next step; one finding in full. |
| `answer ID A\|B\|text [--note]` | Record your decision on a design difference. |
| `report`, `finish --status converged\|capped\|cancelled\|awaiting-user\|done` | Report; end state. |
| `reopen --pr N [--extra-rounds K]`, `pr-ingest [--from-file F]`, `pr-replies [--post]` | PR comment workflow. |
| `export-learnings` | Input for the exit step. |
| `guide-pull`, `guide-push --file F [--history-file H] [--marker M]` | Read or save the author guide (Meko and local copy). |
| `learnings-context`, `guard` | Hook entry points (SessionStart, Stop). |

`fresh-session-review.sh [--model M] [--name gate] [--budget-turns N] [--allow-mcp]` runs the gate.

---

## Testing

```
bash tests/test_engine.sh          # 100 offline checks, no model calls, no network (Go scope tests need `go`)
bash tests/test_meko_live.sh       # 9 live checks against a running local Meko (throwaway datapack)
claude plugin validate --strict .
claude plugin validate --strict plugins/review-loop
```

The offline suite covers settings resolution, rule-file de-duplication, team
skill briefs, the size gate and check probe, ingestion repairs, all triage
rules (verifier gate, duplicates, regressions, oscillation), the three
outcomes, miss diagnoses, report order, PR ingestion (resolved and own
comments skipped, severity parsing of real comment formats), reply drafts and
the push guard, the hooks, the file-mode guide store, and Go test scoping
(changed packages, importers, author declarations, unrelated packages left
out, placeholder substitution). CI runs it on every
push.

---

## Troubleshooting

| Symptom | Meaning and fix |
|---|---|
| `SIZE GATE` after init | The change is large. Confirm, or split it. |
| `[ENV-ERROR]` / `NEXT: FIX_CHECKS` | A check cannot run here (missing tool, or `go list` failed for `{go_packages}`). Replace it with `rl.py set-checks`, or fix `go_module` / `go_tags`. |
| `[SKIP]` on a scoped check | The round changed no Go package. |
| `invalid reviewer output` | The orchestrator asks that reviewer to rewrite its JSON, or records `drop-reviewer`. It never skips one silently. |
| `requires an independent verifier` | You tried to call a serious finding not valid. Run `finding-verifier` first. |
| `NEXT: CAPPED` | Round cap reached with blocking findings left; see the report. |
| `refusing to post` | Commit and push your fixes first. |
| `lessons that were never saved` on init | Run `/review-loop:exit` for the previous loop, or `init --force`. |
| `guide not saved: … not reachable` | Meko is down: `meko start` (or `meko status`), or set `guide_store` to `file`. |
| Session ends mid-loop and is pushed back | The Stop guard (at most 3 times per round). To stop: `rl.py finish --status cancelled`. |
| The guide does not appear in a new session | `rl.py guide-pull` in the repository; check `meko status`; the hook only runs inside a git repository with an `origin` remote matching the guide's name. |

---

## Limitations

- Reviewers keep `Bash` so they can build, test, and reproduce bugs.
  Nothing technically stops them from reading the local guide copy or
  calling Meko over HTTP; only their instructions do.
- The loop is only as good as its checks. On large repositories, targeted
  checks matter more than anything else for cost and time.
- PR ingestion reads the first 100 threads, reviews, and comments.
- The hook injects at most about 8,000 characters of guide text.
- Meko support targets the offline Meko on `127.0.0.1`. Meko Cloud is
  deliberately not used.

---

## Repository layout

| Path | What it is |
|---|---|
| `.claude-plugin/marketplace.json` | The local marketplace. |
| `plugins/review-loop/.claude-plugin/plugin.json` | Plugin manifest. |
| `plugins/review-loop/skills/review-loop/` | Loop protocol (`SKILL.md`), `scripts/rl.py`, `scripts/fresh-session-review.sh`, and `reference/` (brief template, findings schema, triage rubric). |
| `plugins/review-loop/skills/respond/` | PR comment workflow. |
| `plugins/review-loop/skills/exit/` | Miss diagnosis and guide revision, with `reference/EXIT.template.md`. |
| `plugins/review-loop/agents/` | `fresh-reviewer`, `finding-verifier`. |
| `plugins/review-loop/hooks/hooks.json` | Stop guard and SessionStart guide hook. |
| `examples/` | Settings for yb-voyager and the Meko monorepo. |
| `tests/` | Offline and live test suites, fixtures. |
| `docs/DESIGN.md` | Why each safeguard exists. |
| `docs/architecture.html` | The whole workflow as a page (open it locally in a browser). |
| `CHANGELOG.md` | Versions. |

---

## License

Apache-2.0.
