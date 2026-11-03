---
name: review-loop-meko-context
description: Before a review-loop starts, pull the change's recorded design decisions and the team's known review pitfalls from Meko, then hand them to review-loop as requirements and as a reviewer lens. Optional and non-blocking. Use when review-loop's Phase 0 offers it, or when the user asks to "use Meko context for this review".
when_to_use: Called by the review-loop skill after `rl.py init`, when Meko MCP tools are available. Also on "recall Meko context for this review/PR".
---

# review-loop-meko-context

Meko holds two things a fresh reviewer lacks: **why the change exists** (the
design conversation and its decisions) and **what has gone wrong before** in
this codebase (team learnings). This skill turns both into inputs that
review-loop already understands. It never writes to Meko.

Read `${CLAUDE_SKILL_DIR}/../../reference/meko-conventions.md` first.

## The independence rule

Reviewers must not inherit the author's reasoning. So this skill passes on
**requirements, constraints, and decisions about behaviour** only, for example
"p = 100 must be accepted" or "must stay readable by release N-1". It drops
anything that argues the code is correct, explains how the author implemented
something, or names a known weakness the author chose to accept. When in
doubt, leave it out.

## Procedure (at most five Meko searches in total)

Run from the repository root after `rl.py init`. Here `rl.py` means the
engine whose absolute path is stored in `.review-loop/state.json` under
`engine` (read it with `jq -r .engine .review-loop/state.json`).

1. **Resolve identifiers** as the conventions file describes. If no Meko tools
   are usable, print one line, `Meko context skipped: <reason>`, and stop.
2. **Design decisions** (only when a design conversation id or a datapack is
   known). Query with the PR title plus the main touched subsystems:
   - if the PR body has `meko_conversation_id:<id>`, call `memory_search`
     with `run_id: <id>` and `limit: 8`;
   - also call `context_search` with `force_source: "conversation"`,
     `datapack_id`, and `limit: 5`, which reaches other members' design talks.
3. **Team pitfalls** (only with a datapack): call `knowledgebase_search` with
   a query such as `"review pitfalls defects regressions in <subsystems>"`,
   `limit: 5`. Then call `memory_search` for `"review-loop:<owner/repo>"`,
   `limit: 5`, to find summaries earlier loops published.
4. **Distil, do not dump.** From the results keep at most:
   - 6 requirement or decision lines, each with its provenance id;
   - 8 pitfall lines, each a checkable statement with its provenance id;
   - any prior **verified false positives** (from earlier review-loop
     summaries), kept separately.
   Discard anything that conflicts with the current PR description or with
   the code; current evidence always wins over memory.
5. **Hand off to review-loop:**
   - Append the decisions to the intent. Write the current intent (from
     `state.json`) plus a section headed `Design decisions recorded in Meko
     (verify against the code; the PR description wins on conflict)` to a
     temp file, then run `rl.py set-intent --file <tmp>`. This only works
     before round 1.
   - Write the pitfalls to `.review-loop/pitfalls.md` as a bullet list.
     `rl.py brief` adds it to every reviewer's lens automatically.
   - Write prior verified false positives to `.review-loop/triage-notes.md`.
     That file is for **your triage only**. Never pass it to reviewers. A
     match never waives the verifier requirement, because the code may have
     changed since that ruling.
6. **Report** one line per source: how many items were used, how many were
   dropped as conflicting, and any failure such as a quota error.

## Graceful degradation

Meko is never a completion dependency. On any failure, report it once and let
review-loop continue with its normal local behaviour.
