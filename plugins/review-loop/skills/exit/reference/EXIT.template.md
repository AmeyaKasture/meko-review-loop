# My exit instructions for review-loop

The `/review-loop:exit` skill reads this file every time it revises my author
guide. Edit it freely. It lives at `~/.review-loop/EXIT.md` (or the
path in `exit_instructions` in a repository's review-loop settings).

## What the author guide is

One document per repository, at `~/.review-loop/guides/<owner>__<repo>.md`,
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

- Imperative and specific: "When a helper can fail, return the error to the
  caller; never exit the process inside it." Not "be careful with errors".
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

- Cross-repository guidance: also revise `~/.review-loop/guides/_global.md`.
- Store: by default the guide is a local file. To keep every version in an
  external store (a private git repository, a synced folder, a memory
  server), set `guide_pull_cmd` and `guide_push_cmd` in
  `~/.review-loop/settings.json`; the local file then becomes its copy.
- `AGENTS.md`: propose rules in the reply only; never edit.
