---
name: exit
description: Close a finished review loop by asking, for every valid finding, why the author session missed it, and then revising the author guide, a semantic, topic-organised instructions document, so that class of miss does not recur. Follows the user's own EXIT.md. Edits the guide as a coherent document (rewrite, merge, reorder, delete), never as an appended log, and shows the user a diff before applying it. A SessionStart hook loads the guide into future author sessions only; reviewers never see it.
when_to_use: The user runs /review-loop:exit, or says the loop or PR is done and asks to save, record, or report the learnings.
argument-hint: "[--force]"
disable-model-invocation: true
---

# Exit: why did I miss it, and what must the guide say?

The engine is `${CLAUDE_SKILL_DIR}/../review-loop/scripts/rl.py` (written below
as `rl.py`; call it by that full path, from inside the repository).

The goal is an author session that does not write these bugs. Collecting
lessons does not achieve that. Each miss has a cause, and the cause decides
how the instructions must change. The **author guide** is the instructions
document the author session reads at the start of every session in this
repository. It must stay a short, well-organised document that a careful
engineer could follow, not a pile of notes.

## Steps

1. **Check the loop is finished.** `rl.py status`. If it is still `running`,
   stop and say so, unless the user passed `--force`.
2. **Load the current guide:** `rl.py guide-pull`. The guide is the local
   file `guide_file`; when the user has configured an external guide store
   (`guide_pull_cmd` / `guide_push_cmd` in `~/.review-loop/settings.json`),
   that store holds the canonical version and this refreshes the local copy.
   The output says where the guide came from. If it reports the store
   unavailable, tell the user before going on.
3. **Export:** `rl.py export-learnings` prints the path of
   `.review-loop/learnings.json`. Read it: fixed findings (with `claim`,
   `fix`, `miss`, `lesson`), not-valid findings, design differences with the
   user's decisions, PR comment outcomes, `miss_causes`,
   `missing_miss_analysis`, `guide_file`, `guide_history`, `global_guide`.
4. **Ask yourself why you missed each one.** For every id in
   `missing_miss_analysis` (and any recorded `miss` that now looks wrong),
   diagnose it before touching the guide:
   - First search for guidance that already covered it: read the current
     `guide_file` and `global_guide`, and grep the repository's rule files
     (`AGENTS.md` from the changed file's directory up to the root).
   - Then pick exactly one cause from `miss_causes` and record it:
     `rl.py miss ID --cause <cause> --why "<one or two honest sentences>" [--guide-ref "<file: section or rule>"]`.
     `covered-unclear` and `covered-ignored` must quote the guidance they refer to.
   - If this session did not write the code (no authoring context), say so in
     `--why` and diagnose from the diff and the finding.
   - Be honest. "The rule was clear and I did not apply it" is a valid and
     useful answer; it changes the fix to the guide.
   Export again when done.
5. **Read the user's instructions:** the file at `exit_instructions` (default
   `~/.review-loop/EXIT.md`). If it does not exist, copy
   `${CLAUDE_SKILL_DIR}/reference/EXIT.template.md` there, tell the user, and
   follow it this time. It defines the guide's sections, style, size budget,
   and approval rules. Where it conflicts with this skill it wins, except for
   the safety rules below.
6. **Plan the guide revision from the causes**, not from the findings:

   | Cause | Change to the guide |
   |---|---|
   | `not-covered` | Add guidance in the section where an author would look while writing that kind of code. State *when* it applies and *why*, with a one-line example if it helps. Create a section only if none fits. |
   | `covered-unclear` | Rewrite the existing guidance so its trigger and action are unambiguous. Do not add a second item next to it. |
   | `covered-ignored` | Do not repeat the text. Make it harder to skip: move it to the "Before you finish" checklist, attach it to the moment it matters ("when you add a helper that returns error ..."), or turn it into a concrete check. If it is an `AGENTS.md` rule, reference it rather than copy it. |
   | `context-gap` | Add a "where to look" pointer: what to read before changing that area, and the fact that was missing. |
   | `spec-gap` | No coding guidance. Optionally add a question to "Before you start". |
   | `one-off` | No change. |
   | Design difference the user decided | Record the decision and its reason under design decisions, so it is neither re-litigated nor violated. |

   Several findings with one root cause become one change. A new item that
   overlaps an existing one is merged into it. Guidance that the new
   guidance supersedes is deleted.
7. **Write the revised guide** to a temporary file as a whole document:
   topic sections, imperative sentences, no finding ids, dates, counts, or
   loop markers in the body (those go in the history). Keep it within the
   budget in `EXIT.md`; when over, tighten wording and drop the least useful
   items rather than truncating.
8. **Show the user and ask.** Print the miss analysis as a short table
   (finding, cause, why), then `diff -u <guide_file> <temp file>` (or the full
   new file the first time). Ask the user (Claude Code: `AskUserQuestion`): apply, let me edit
   first, or skip. Apply only on "apply" (or as `EXIT.md` says).
9. **On apply:** write the history entry (each finding's cause and a one-line
   summary of what changed in the guide) to a temporary file, then save both:
   ```
   rl.py guide-push --file <new guide> --history-file <history> --marker <marker from learnings.json>
   ```
   It sends the new version and the history to the external store (if one is
   configured), refreshes the local copy, and appends `guide_history`. A marker
   already recorded is not recorded twice. Never write `guide_file` directly.
   Put cross-repository guidance in `global_guide` (a local file).
10. **Rule proposals.** When a `covered-ignored` or `not-covered` item is
   clearly a team rule (it would bind every author, not just this session),
   propose exact text for the nearest `AGENTS.md`. Never edit it yourself:
   rules are shared with reviewers and the team; the guide is private.
11. **Finish.** Report the causes, what changed in the guide, and any rule
    proposals. Then `rl.py finish --status done` if the loop is not already
    done.

## Safety rules (not overridable)

- The guide and its history are only for the author session. Never write
  them into the repository, a reviewer brief, or anything a reviewer reads.
- Only `rl.py guide-push` writes the guide, through the store the user
  configured. Never copy it to another service or connector or MCP tool on
  your own initiative.
- No secrets, customer data, or code excerpts longer than a line.
