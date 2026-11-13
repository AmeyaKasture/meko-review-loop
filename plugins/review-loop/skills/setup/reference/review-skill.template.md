---
name: {{SKILL_NAME}}
description: Review the code changes on the current branch against {{DEFAULT_BRANCH}} for {{REPO_NAME}}, applying the repository's rule files, its mechanical checks, and a fixed set of review lenses. Reports findings classed CRITICAL, WARNING, SUGGESTION, or QUESTION.
---

# {{REPO_NAME}} branch review

Review every change on the current branch compared to `{{DEFAULT_BRANCH}}`.
When another workflow (for example review-loop) gives you an explicit commit
range, use that range wherever this skill says `MERGE_BASE` or `HEAD`.

## Step 1: Gather context

```bash
MERGE_BASE=$(git merge-base {{DEFAULT_BRANCH}} HEAD)
git diff --name-status $MERGE_BASE..HEAD
git diff $MERGE_BASE..HEAD
git log --oneline $MERGE_BASE..HEAD
```

For a large diff, review file by file (`git diff $MERGE_BASE..HEAD -- <path>`).
Read the full current version of every changed file, not only the hunks.

## Step 2: Standards

{{STANDARDS}}

Walk the rule files from each changed file's directory up to the repository
root. Inner files are more specific and win on conflict. Count a symlinked
copy (for example `CLAUDE.md` -> `AGENTS.md`) once. Treat every rule as an
active checklist item: look for a violation of it in the added lines, and
cite the rule file in the finding when a rule drove it.

## Step 3: Mechanical checks

Run these on the changed files every time; they are cheap and objective. Skip
a tool that is not installed and say so.

{{MECHANICAL_CHECKS}}

## Step 4: Review lenses

Apply every lens to every logical change:

1. **Correctness**: logic errors, off-by-one, wrong conditions, unhandled
   edge cases (empty, null, zero, very large, unicode, concurrent access).
2. **Error handling**: errors swallowed, lost context, partial failure that
   leaves state inconsistent, retries without bounds.
3. **Contracts and callers**: changed signatures, return values, or
   semantics that existing callers, serialised data, or public APIs rely on.
4. **Concurrency and resources**: races, missing locks, leaked files,
   connections, goroutines, threads, or temporary data.
5. **Security**: untrusted input reaching queries, shells, paths, or
   templates; secrets in code or logs; missing authorisation checks.
6. **Tests**: new behaviour without a test; tests that cannot fail; flaky
   timing; missing negative cases.
{{EXTRA_LENSES}}

## Step 5: Report

Group findings by severity class:

| Class | Meaning |
|---|---|
| CRITICAL | Wrong results, data loss, security hole, or crash on a realistic path. Must fix. |
| WARNING | A real defect or rule violation with limited impact. Should fix. |
| SUGGESTION | Clarity, naming, small simplifications. Optional. |
| QUESTION | Intent or design is unclear; the author must answer. |

Each finding names the file and line, states the problem in one sentence,
gives the evidence (the rule, the failing input, or the reproduction), and
suggests a fix. Report only what you can support with evidence; do not pad.
