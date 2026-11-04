# My exit instructions for review-loop

The `/review-loop:exit` skill reads this file every time it revises my author
guide. Edit it freely. It lives at `~/.claude/review-loop/EXIT.md` (or the
path in `exit_instructions` in a repository's review-loop settings).

## What the author guide is

One document per repository, at `~/.claude/review-loop/guides/<owner>__<repo>.md`,
that my main Claude session reads at the start of every session. It is how
I want code written in that repository, learned from what reviewers caught.
It is not a log, a list of past bugs, or a copy of `AGENTS.md`.

## Structure

Keep these sections, in this order. Omit empty ones.

1. `## Before you start` - questions to settle before writing code.
2. `## While writing` - guidance grouped by topic, one `###` per topic,
   for example: Error handling, Persistence and compatibility, Concurrency,
   Hot paths, Tests, Naming and placement.
3. `## Where to look` - what to read before changing a given area, and why.
4. `## Design decisions` - choices I confirmed, each with its reason.
5. `## Before you finish` - a short checklist (at most 10 items) of things
   that were clear but still got skipped.

## Style

- Imperative and specific: "When a helper returns `error`, return it; never
  call `utils.ErrExit` inside it." Not "be careful with errors".
- Each item says when it applies and why, in at most three lines.
- Refer to `AGENTS.md` rules by file and heading instead of copying them.
- No finding ids, dates, counts, or PR numbers in the guide body.

## Budget

At most about 7,000 characters. When over, tighten and merge before dropping;
drop the least useful items first.

## Approval

Always show me the miss analysis and the diff, and wait for my yes before
replacing the guide.

## Other destinations

- Cross-repository guidance: also revise `~/.claude/review-loop/guides/_global.md`.
- Store: the local Meko keeps every version of the guide (datapack
  `review-loop guide <owner>/<repo>`) and each loop's history as a memory;
  the local file is its copy. To keep it in files only, set
  `"guide_store": "file"` in `~/.claude/review-loop/settings.json`.
- `AGENTS.md`: propose rules in the reply only; never edit.
