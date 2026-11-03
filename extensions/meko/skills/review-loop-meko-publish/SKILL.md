---
name: review-loop-meko-publish
description: After a review-loop finishes, and only with the user's explicit consent, store the report in Meko and write one compact learning summary (defect classes fixed, verified false positives, user decisions) so later loops and teammates can recall it. Use when review-loop's Phase 5 offers it, or when the user says "publish this review to Meko".
when_to_use: After `rl.py finish` and `rl.py report`, when Meko MCP tools are available and the user agrees to publish.
disable-model-invocation: false
---

# review-loop-meko-publish

This closes the improvement loop. A loop's outcome becomes something the next
loop, and the next teammate, can recall. It writes to Meko, so it is
**consent-gated**: ask once, publish only on a clear yes.

Read `${CLAUDE_SKILL_DIR}/../../reference/meko-conventions.md` first.

## Ask first

State exactly what will be stored and where, then ask:

> Publish this review to Meko datapack `<name>`? This stores the report file
> as an artifact and one summary memory (defect classes, verified false
> positives, your decisions; no code, no conversation text). Members of the
> datapack can see the artifact.

Without a yes, stop.

## Procedure

1. `rl.py export-learnings` (the engine path is `engine` in
   `.review-loop/state.json`) writes `.review-loop/learnings.json`. Read it. Its
   `marker` is the dedup key for this exact change.
2. **Dedup:** call `memory_search` for the marker (`limit: 3`). If a summary
   with that marker exists, skip step 4 and say so.
3. **Artifact:** the newest `.review-loop/report-*.md` goes to `artifact_put`
   (`content_type: text/markdown`, `filename: review-loop-<repo>-<final12>.md`).
   Get base64 with `base64 -i <file> | tr -d '\n'`. Keep the returned hash.
   Refuse files over 5 MB. Never upload anything else from the repository.
   On **offline Meko**, also offer to index the report for search with
   `knowledgebase_upload_file` (`path`: the report's absolute path), so later
   loops find it through `knowledgebase_search`. Ask first; it is a separate
   yes, because every datapack member can read the knowledge base.
4. **Summary memory:** one `memory_add`, with `agent_id` and
   `conversation_id` per the conventions (create a conversation only if none
   exists), and metadata `{"source": "review-loop", "marker": "<marker>",
   "artifact": "<hash>"}`. Body, in Markdown, at most about 25 lines:
   ```
   review-loop outcome <marker>
   Repo <repo>, target <target>, rounds <n>, outcome <outcome>.
   Defect classes fixed: <category: count, ...>
   Notable fixes: <up to 5 titles with file>
   Verified false positives (do not re-raise without new evidence): <up to 5, with why>
   Decisions by the user: <up to 5>
   Open questions: <titles>
   Report artifact: <hash>
   ```
   Never include code, diffs, secrets, or conversation turns.
5. **Report** the artifact hash and whether the memory was written or
   deduplicated. Then explain promotion in one sentence: a datapack owner or
   maintainer can promote this memory to shared knowledge from the Learnings
   tab, which makes it visible to every teammate's loops. Do not call
   `memory_promote` yourself unless the user asks; it is one-way and needs a
   preview plus explicit confirmation.
6. **PR context line (optional):** if the repository's review guide allows
   Meko context lines in PR bodies, print the line for the user to add
   themselves, for example `cloud.mekodata.ai://<datapack-id>;<name>`. Do not
   edit the PR.

## Graceful degradation

If any call fails, report it and stop. The local report stays complete and
valid; publishing is never a completion dependency.
