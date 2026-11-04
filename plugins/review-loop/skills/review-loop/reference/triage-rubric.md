# Triage rubric (main thread)

You wrote this code, so you are the most biased judge of it. Authors
systematically favour their own output. This rubric stops you from
rationalising real defects away, and turns every finding into one of three
outcomes the user can scan quickly.

## The three outcomes

| Outcome | Meaning | Code | Command |
|---|---|---|---|
| **Valid** | The finding is real (or plausible and cheap to fix safely). | Fix it. | `rl.py triage ID accept --note "..."`, then after fixing `rl.py fixed ID --note "<what changed>" --cause <why missed> --why "..."` |
| **Not valid** | You can show, with evidence, that it is not a defect. | Keep as is. The reason goes in the report. | `rl.py triage ID reject --note "<evidence>"` |
| **Doubtful** | It depends on intent or a trade-off between two valid designs: a possible design difference. | Keep as is. It goes at the **top** of the report for the user. | `rl.py triage ID doubtful --note "<why the code alone cannot decide>" [--option A --option B --recommend A]` |

Bookkeeping verdicts, used rarely:

- `duplicate --duplicate-of R1-3`: the same defect as an earlier finding (check `possible_duplicates`).
- `defer --note "..."`: real but pre-existing and not made worse by this change, or outside the requested scope.

## Rules

1. **Default to valid** when a finding is reproduced (`reproduced: true`) or a
   check failed. Facts outrank your recollection of what you meant.
2. **Calling a major or blocker (or a reproduced minor) not valid requires the
   `finding-verifier` agent.** The engine enforces this. Give the verifier the
   finding (`rl.py show ID`), your rebuttal, the snapshot, and the scratch dir.
   - `REAL` -> valid (use its severity if it differs).
   - `NOT_REAL` -> `reject --verifier not-real --note "<its evidence>"`.
   - `UNCERTAIN` -> doubtful, using its two options.
3. **A not-valid reason is evidence, not opinion.** "This is intentional" is not
   evidence. "Callers at a.go:40 and b.go:12 always pass a non-empty slice"
   is evidence. If your only reason is a design preference, it is doubtful.
4. **Never fix by suppression.** Silencing a warning, adding an ignore comment,
   loosening a type, weakening a test, or swallowing an error is not a fix. If
   suppression is genuinely right, the finding is doubtful.
5. **Duplicates of fixed findings are regressions.** The engine marks them
   valid. On the second recurrence it turns them into a doubtful item: you are
   oscillating, so stop and let the user decide.
6. **Intent gaps** listed by reviewers are findings too. If the requirement is
   ambiguous, mark it doubtful rather than guess.
7. **Questions** (severity `question`, often from the team review skill) are
   answered the same way: valid if they expose something to change, not valid
   with the answer as the reason, or doubtful if only the user can answer.
8. **Nits** may be called not valid without a verifier ("style preference"),
   and never drive another round.
9. **Findings flagged `[outside change]`** point at files the change does not
   touch. They are often pre-existing (`defer`), but a change can break an
   untouched caller; then they are valid.
10. **Do not toggle** between two designs across rounds. The second time a
    design question comes up, it is doubtful.

## Why did I miss it? (the memory of each valid fix)

A valid finding means the author session wrote a bug. Before or right after
fixing it, while the context is fresh, ask yourself why, and record one cause:

| Cause | Meaning |
|---|---|
| `not-covered` | No guidance existed in the author guide or the repo rules. |
| `covered-unclear` | Guidance existed but was vague, buried, or ambiguous. Quote it. |
| `covered-ignored` | Guidance was clear but was not applied. Quote it. |
| `context-gap` | Needed knowledge of the code or system that was not looked up. |
| `spec-gap` | The requirement itself was unclear. |
| `one-off` | A slip with no reusable lesson. |

Record it with `rl.py fixed ID --note "..." --cause <cause> --why "..." [--guide-ref "..."]`,
or later with `rl.py miss ID ...`. Be honest: "it was clear and I skipped it"
is the most useful diagnosis there is. The exit skill turns these causes into
edits of the author guide. The guide is for the author session only; never
put it, or any diagnosis, in a reviewer brief.

## Fixing valid findings

- Fix the root cause, not the symptom the reviewer happened to hit.
- For valid blocker or major correctness findings, add or extend a test that
  fails without the fix, when the project has a test suite.
- Keep each fix minimal and local. Do not refactor beyond the finding.
