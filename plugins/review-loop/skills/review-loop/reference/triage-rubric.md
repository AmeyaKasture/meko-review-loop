# Triage rubric (main thread)

You wrote this code, so you are the most biased judge of it. Research on LLM
self-review shows authors systematically favour their own output. The rubric
below exists to stop you from rationalising real defects away.

## The five verdicts

| Verdict | Use when | Command |
|---|---|---|
| **accept** | The defect is real, or plausible and cheap to fix safely. | `rl.py triage ID accept --note "..."` |
| **reject** | You can show, with evidence, that it is not a defect. Majors, blockers, and reproduced minors need the verifier first. | `rl.py triage ID reject --note "<evidence>"` |
| **defer** | Real, but pre-existing and not made worse by this change, or clearly outside the requested scope. | `rl.py triage ID defer --note "..."` |
| **ask** | The right answer depends on intent, product behaviour, or a trade-off between two valid designs. | `rl.py triage ID ask --option "..." --option "..." --recommend A --note "..."` |
| **duplicate** | Same defect as an earlier finding (check `possible_duplicates`). | `rl.py triage ID duplicate --duplicate-of R1-3` |

## Rules

1. **Default to accept** when a finding is reproduced (`reproduced: true`) or
   when a check failed. Facts outrank your recollection of what you meant.
2. **Rejecting a major/blocker, or a minor the reviewer reproduced, requires
   the `finding-verifier` agent.** The script enforces this. Give the verifier the
   finding (`rl.py show ID`), your rebuttal, the snapshot, and the scratch dir.
   - Verifier `REAL` -> accept (use its severity if it differs).
   - Verifier `NOT_REAL` -> `reject --verifier not-real --note "<its evidence>"`.
   - Verifier `UNCERTAIN` -> `ask`, using its two options.
3. **A rejection note is evidence, not opinion.** "This is intentional" is not
   evidence. "Callers at a.py:40 and b.py:12 always pass a non-empty list,
   and the type is NonEmptyList" is evidence.
4. **Never fix by suppression.** Silencing a warning, adding an ignore
   comment, loosening a type, deleting or weakening a test, or catching and
   discarding an exception is not a fix. If suppression is genuinely right,
   that is an `ask`.
5. **Duplicates of fixed findings are regressions.** The script marks them
   accepted. On the second recurrence it converts them to a question: you are
   oscillating, so stop and let the human decide.
6. **Intent gaps** listed by reviewers are findings too. If a requirement is
   ambiguous, `ask` rather than guess.
7. **Nits** may be rejected without a verifier ("style preference"), and never
   drive another round. Do not misuse `defer` for a finding you disagree with;
   `defer` means real but pre-existing or out of scope.
8. **Findings flagged `[outside change]`** point at a file the change does not
   touch. They are often pre-existing (`defer`), but not always: a change can
   break an untouched caller. Accept them when the change causes or exposes
   the defect.
9. **Two valid designs** (for example, "throw" versus "return an error value")
   are an `ask` with options and your recommendation. Do not toggle between
   them across rounds.

## Writing good questions

Each `ask` must let the user decide in ten seconds:

- `--option` texts are concrete actions, not abstractions:
  "Return 404 for unknown IDs (current behaviour)" versus
  "Return 200 with an empty body, matching /v1".
- `--recommend` names the letter you would choose.
- `--note` explains in one or two sentences why the code alone cannot decide.

## Fixing accepted findings

- Fix the root cause, not the symptom the reviewer happened to hit.
- For accepted blocker/major correctness findings, add or extend a test that
  fails without the fix, when the project has a test suite.
- Keep each fix minimal and local. Do not refactor beyond the finding.
- After fixing, `rl.py fixed ID [ID...] --note "<one line on what changed>"`.
