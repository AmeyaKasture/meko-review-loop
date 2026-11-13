---
name: respond
description: After the user has pushed a branch that went through review-loop, answer the GitHub PR's review comments from the same orchestrator thread. Ingests unresolved comments into the loop's ledger, sorts each into valid (fix and record a lesson), not valid (keep the code, explain why), or doubtful (keep the code, flag a possible design difference), sends any fixes through fresh independent review again, and drafts replies that are posted only after the user pushes and confirms.
when_to_use: The user says the PR has comments, asks to "answer", "address", or "respond to" PR review comments, or runs /review-loop:respond after pushing.
argument-hint: "<PR number>"
disable-model-invocation: false
---

# Respond to PR comments

Arguments: `$ARGUMENTS` (the PR number).

You are the same orchestrator that ran the review loop, so you keep its
ledger and its context. The engine is
`${CLAUDE_SKILL_DIR}/../review-loop/scripts/rl.py` (written below as `rl.py`;
always call it by that full path). Read
`${CLAUDE_SKILL_DIR}/../review-loop/reference/triage-rubric.md` before
triaging.

## Steps

1. **Preconditions.** `rl.py status` must show a finished loop for this branch
   (converged, done, capped, or awaiting-user). If there is no loop state, run
   `rl.py init --target <PR>` first so the ledger exists. Make sure the local
   branch matches what was pushed (`git status`, `git log @{u}..`); if the user
   has new local edits, ask before continuing.
2. **Open the PR round:**
   ```
   rl.py reopen --pr <N>
   rl.py snapshot --kind pr
   rl.py pr-ingest
   ```
   `pr-ingest` reads unresolved review threads, review bodies, and PR
   comments through `gh` (read-only). It skips resolved threads, comments by
   the PR author, and comments it has already ingested. Comments tagged
   `[CRITICAL]`, `[WARNING]`, `[SUGGESTION]`, or `[QUESTION]` keep that
   severity; an untagged inline comment counts as `major`, and an untagged
   PR-level comment or a question counts as `question`.
3. **Triage every comment** with the rubric's three outcomes:
   - valid -> `rl.py triage ID accept --note "..."`
   - not valid -> `rl.py triage ID reject --note "<evidence>"` (the verifier
     rule applies to major and blocker comments exactly as in the loop)
   - doubtful -> `rl.py triage ID doubtful --note "<why>"`
   Read the whole thread (`rl.py show ID`, field `source.thread`) before
   deciding; a later reply may already settle it.
4. **Fix the valid ones**, then
   `rl.py fixed ID --note "<what changed>" --cause <cause> --why "<why the loop and I missed it>"`.
5. **Follow `rl.py status`.** If anything was fixed it says `NEXT_ROUND`: run
   the normal review-loop Phases 1 to 4 (fresh reviewers, full plus delta,
   then the gate) on the new tree. PR comment fixes are never exempt from
   independent review. When it says `CONVERGED`, continue.
6. **Report and draft replies:**
   ```
   rl.py report
   rl.py pr-replies
   ```
   `pr-replies` writes `.review-loop/pr-replies.md` and posts nothing. Show
   the user the doubtful items first, then a short summary of the drafts.
7. **Hand over.** Tell the user to commit and push the fixes themselves. Never
   commit or push for them.
8. **Post only on an explicit yes.** After the user says they pushed, ask
   the user (Claude Code: `AskUserQuestion`) whether to post the drafted replies (options: post all,
   let me edit first, do not post). Only on "post all" run
   `rl.py pr-replies --post`. It refuses while fixes are unpushed. Each reply
   starts with the configured `reply_prefix` (default `[AI] `).
9. `rl.py finish --status done`, then remind the user that
   `/review-loop:exit` revises the author guide.

## Rules

- Never resolve threads, approve, or request changes on the PR.
- Never paste a reviewer's or commenter's text into a fresh reviewer's brief.
  Fresh reviewers get only the script-generated brief, as in the loop.
