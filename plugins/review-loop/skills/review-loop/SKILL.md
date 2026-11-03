---
name: review-loop
description: Iterative fresh-context review-and-fix loop for a PR, branch, or working tree. Spawns independent read-only reviewer agents (no author context) that run checks and try to reproduce defects, triages their findings in this thread with an anti-self-bias verifier, fixes what is real, and repeats until a round is clean. Finishes with a gate review in a completely new Claude session, then writes a report that asks the user about every judgement call.
when_to_use: The user asks to iterate on a change until reviewers find nothing, to "loop review", "review until clean", "harden this PR", or to get independent fresh-eyes review with fixes applied. Also after finishing a substantial implementation when the user wants it verified before opening or merging a PR.
argument-hint: "[PR number | PR URL | base ref] [--max-rounds N] [--strict] [--no-gate] [--gate-model MODEL] [--reviewer-model MODEL] [--checks \"cmd1;;cmd2\"]"
disable-model-invocation: false
---

# Review loop

Arguments: `$ARGUMENTS`

You are the **orchestrator and fixer**. You keep this conversation's context.
Reviewers never see it. Independence is the mechanism that makes this work, so
protect it: reviewers receive only a brief file that the script generates.

State lives in `<repo>/.review-loop/` and is managed by the state engine
`${CLAUDE_SKILL_DIR}/scripts/rl.py` (executable; written below as `rl.py`).
Always call it by that full path, because shell variables do not persist
between Bash calls. Run every command from inside the target repository.
In each command below, `rl.py` means `${CLAUDE_SKILL_DIR}/scripts/rl.py`.

Run `rl.py status` whenever you are unsure what to do next; its `NEXT:` line is
authoritative. Read `${CLAUDE_SKILL_DIR}/reference/triage-rubric.md` before
your first triage.

## Phase 0: set up

1. **Resolve the target.**
   - PR number or URL: `gh pr view <pr> --json number,title,body,baseRefName,headRefName,url`.
     If the working tree is dirty and not already on the PR's head branch,
     stop and tell the user. Otherwise `gh pr checkout <pr>`, then
     `git fetch origin <baseRefName>` and use `origin/<baseRefName>` as base.
   - A ref (for example `main` or a SHA): use it as base for the current branch.
   - Nothing: current branch plus uncommitted changes against the default
     branch's merge-base (the script computes this when `--base` is omitted).
2. **Write the intent** to a temporary file: the PR title and body, or the
   user's stated requirements from this conversation. Requirements only.
   Do NOT include your implementation reasoning, known trade-offs, or why you
   believe the code is correct; that would anchor the reviewers.
3. **Initialise:**
   ```
   rl.py init --session ${CLAUDE_SESSION_ID} --target "<pr or branch>" [--base <ref>] \
       --intent-file <file> [--max-rounds N] [--strict] [--no-gate] [--gate-model M] [--reviewer-model M]
   ```
   Defaults come from `.review-loop.json` at the repo root when present
   (keys: `checks`, `max_rounds`, `strict`, `gate`, `gate_model`,
   `reviewer_model`, `size_limits`); flags override it.
   **Size gate:** if `init` prints `SIZE GATE`, ask the user whether to proceed
   before anything else (in a non-interactive run, stop and report instead).
   **Optional Meko context:** if the `review-loop-meko-context` skill is
   available, run it now; it can enrich the intent file and write
   `.review-loop/pitfalls.md`, which briefs include automatically.
4. **Choose checks.** `rl.py detect-checks`, then pick the fastest set that covers
   the touched code (typecheck, lint, targeted tests), and record it:
   `rl.py set-checks "cmd1;;cmd2"`. For very large repositories, scope tests to
   the touched modules. If the user passed `--checks`, use exactly those.
5. **Validate the checks:** `rl.py run-checks --probe`. Any `ENV-ERROR` (missing
   tool or module) must be replaced before round 1, because a check that
   cannot run fails every round and the loop can never converge. Ordinary
   test failures are fine here; they become findings.

## Phase 1: review round

1. `rl.py snapshot` (creates `refs/review-loop/round-N`; the user's index and
   HEAD are untouched).
2. `rl.py run-checks` (use `run_in_background` if the suite is slow).
3. Generate briefs. Each prints a brief path:
   - **Round 1:** two `full` reviewers with different lenses, for example
     ```
     rl.py brief --mode full --name r1a --lens "Correctness, edge cases, error handling, concurrency, resource handling."
     rl.py brief --mode full --name r1b --lens "Contracts and callers, security, test adequacy, and gaps against the stated intent."
     ```
     For a large change (roughly more than 800 changed lines or 15 files), add
     a third reviewer, or split the lenses by subsystem.
   - **Later rounds (dual path):** one fresh `full` reviewer and one `delta`
     reviewer that scrutinises only the fixes since the previous round.
4. **Spawn all reviewers in ONE message, in parallel**, with
   `subagent_type: "fresh-reviewer"` (installed as a plugin it may be listed as
   `review-loop:fresh-reviewer`; use whichever name the Agent tool lists). If
   state has a `reviewer_model`, pass it as the Agent `model`. The prompt must be exactly:
   `Read and follow the brief at <brief path>.`
   Add nothing else: no summaries, no previous findings, no hints about what
   you fixed. If the `fresh-reviewer` agent type is unavailable, use
   `general-purpose` with the same one-line prompt plus "Do not modify any
   repository file, and do not call any mcp__ tool."
5. When they return: `rl.py verify-tree` (if it reports changes, inspect and
   restore before continuing), then `rl.py ingest <output json>` for each.
   If an ingest is rejected as invalid, send the printed errors to that same
   reviewer with `SendMessage` and ask it to rewrite its file, then ingest
   again. Never skip a reviewer silently: the next `snapshot` refuses while any
   reviewer is unaccounted for. If a reviewer truly cannot be recovered, record
   it with `rl.py drop-reviewer NAME --reason "..."`.

## Phase 2: triage (you, applying the rubric)

For each finding, decide accept, reject, defer, ask, or duplicate, and record
it with `rl.py triage`. Use `rl.py show ID` for detail. To reject a major,
blocker, or high-confidence finding, first spawn a `finding-verifier` agent
(several in parallel if needed) with: the output of `rl.py show ID`, your
rebuttal, the repo path, the snapshot SHA, and the round's scratch directory.
Follow its verdict exactly as the rubric says.

## Phase 3: fix

Fix every accepted finding (follow the rubric's fixing rules), then mark them
with `rl.py fixed ID ... --note "..."`. Run the most relevant check locally as a
sanity test. Then run `rl.py status` and follow `NEXT`:

- `NEXT_ROUND` -> back to Phase 1.
- `FIX_CHECKS` -> a check cannot run in this environment; replace it with
  `rl.py set-checks`, re-run `rl.py run-checks`, and check `status` again.
- `GATE` -> Phase 4. (The round was clean. Do not edit code now; accepted
  minor findings are reported as optional fixes instead.)
- `CONVERGED` or `CAPPED` -> Phase 5.

## Phase 4: gate review in a completely new session

1. `rl.py snapshot --kind gate` and `rl.py run-checks`.
2. Run, with `run_in_background: true`, and wait for its completion notice:
   ```
   bash ${CLAUDE_SKILL_DIR}/scripts/fresh-session-review.sh [--model <gate model>]
   ```
   This starts a separate `claude -p` process with no session history and a
   schema-enforced JSON output, then ingests the result. Prefer a different
   model from the one that wrote the code (model diversity catches different
   bugs).
3. Triage as in Phase 2. If anything blocking is accepted, fix it (Phase 3);
   `status` will then send you back to Phase 1.

## Phase 5: finish and ask

1. `rl.py finish --status awaiting-user` if there are questions, otherwise
   `--status converged` or `--status capped`.
2. `rl.py report` prints the report path. Read it.
3. Tell the user, briefly: the outcome, rounds run, what was fixed, what was
   rejected and why, and the report path.
4. If there are questions and the session is interactive, ask them with
   `AskUserQuestion` (up to four per call; each option label is a short
   action, the recommended option first with "(Recommended)"). Include the
   optional minor fixes as one multi-select question if any exist.
5. Record each answer with `rl.py answer ID <letter or free text> [--note ...]`,
   then apply the user's decisions. If that changes code, run one more `delta`
   round (Phase 1 with only a delta reviewer, no gate) unless the change is
   trivial, then regenerate the report. Finish with `rl.py finish --status done`.
6. **Optional Meko publish:** if the `review-loop-meko-publish` skill is
   available, offer it once. It publishes only with the user's explicit consent.

## Invariants

- Never commit, push, or post PR comments unless the user explicitly asks.
- Never edit code between a clean round and the end, except to apply the
  user's answers in Phase 5.
- Never paraphrase or pre-digest the change for a reviewer.
- Never let the loop run past the round cap; the cap exists because LLM
  reviewers can always find something, and convergence is judged at the
  blocking threshold (major by default, minor with `--strict`).
- If the user interrupts or wants to stop: `rl.py finish --status cancelled`,
  then `rl.py report`.
- The plugin's Stop hook will push you back into the loop while status is
  `running`, at most three times per round. Do not fight it; follow its
  `Next step`.
