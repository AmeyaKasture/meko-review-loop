# Meko conventions this extension relies on

Source: the `meko-mcp-tools` skill in `yugabyte/meko-skills` (v2.5.1) and the
Meko MCP reference. Re-check these when Meko releases a new version.

## Finding the Meko tools

Tool names depend on how Meko is installed. Use whichever set is present:

| Install | Tool name example |
|---|---|
| claude.ai connector | `mcp__claude_ai_Meko__memory_search` |
| `meko-agent-skills` plugin | `mcp__plugin_meko-agent-skills_meko__memory_search` |
| `claude mcp add ... meko` | `mcp__meko__memory_search` |

If the only Meko tool listed is `authenticate`, the connector is not signed
in: tell the user to run `/mcp` and authenticate, then skip Meko for this run.

## Identifiers: read, never invent

- **agent_id**: use the SessionStart-injected value verbatim (shape
  `claude_code:<repo-root-basename>`). If nothing was injected, use
  `claude_code:<repo-root-basename>`, the documented default shape.
- **conversation_id**: use the SessionStart-injected UUID. Without one:
  - reads may pass a fresh 32-character lowercase hex id (trace nesting only;
    never `""`, which fails);
  - writes need a real conversation: call `conversation_create` once
    (title `review-loop <target>`) and reuse its id.
- **datapack_id**, first match wins:
  1. `.review-loop.json` key `meko.datapack_id`;
  2. a PR body line `cloud.mekodata.ai://<datapack-id>;<name>`;
  3. the nearest `MEKO.md` above the touched files (pick the row whose topic
     matches the change, else the row marked as the default);
  4. a datapack pinned by the `meko-select-datapack` skill;
  5. otherwise skip datapack-scoped calls and say so once.
- **Design conversation**: a PR body line `meko_conversation_id:<id>`.

## Read scoping that matters here

- `memory_search` returns only the current user's memories. On someone else's
  PR it will usually find nothing; that is not an error.
- `knowledgebase_search` returns team-shared knowledge for the datapack
  (uploaded docs plus promoted learnings). This is the team path.
- `context_search` with `force_source: "conversation"` searches saved
  conversations of every member on the datapack.

## Quotas and errors

- Free tier caps searches per user for life (1,000 `memory_search`, 1,000
  `knowledgebase_search`, 1,000 `context_search`). Budget at most five
  searches per loop.
- `free_tier_limit_reached` (possibly HTTP 403) is a failed call, not an empty
  result and not an auth problem. Do not retry. Report it and continue without Meko.
- A response with `error` or an error-shaped `detail` is a failure. Only a
  successful response with an empty `results` array means "nothing found".
