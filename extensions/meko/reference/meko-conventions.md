# Meko conventions this extension relies on

Sources: the `meko-mcp-tools` skill in `yugabyte/meko-skills` (v2.5.1), the
Meko Cloud MCP reference, and offline Meko's `backend/docs/api.md` in
`yugabyte/meko` (release meko-v0.20261103.4). Re-check these when Meko releases
a new version.

## Two kinds of Meko

| | Meko Cloud | Offline Meko (`yugabyte/meko`) |
|---|---|---|
| Where | `https://mcp.mekodata.ai/mcp` | `http://127.0.0.1:7778/mcp`, one local binary, data in `~/.meko` |
| Auth | API key or OAuth | `meko token` (client token) |
| Tools | the set below | the same names and arguments, plus `knowledgebase_upload_file` (upload by local path) |
| Quotas | Free tier lifetime caps | none |
| Datapacks | team datapacks, IDs in `MEKO.md` | local ones; the default datapack answers a blank `datapack_id`. While `meko cloud_link` is on, calls naming a Cloud datapack are routed to Cloud. |

Tell them apart from the MCP server's URL in `claude mcp get meko` when that
works, or from the tool list: only offline Meko has `knowledgebase_upload_file`
and `datapack_metrics`.

## Finding the Meko tools

Tool names depend on how Meko is installed. Use whichever set is present:

| Install | Tool name example |
|---|---|
| claude.ai connector | `mcp__claude_ai_Meko__memory_search` |
| `meko-agent-skills` plugin | `mcp__plugin_meko-agent-skills_meko__memory_search` |
| `claude mcp add ... meko`, or offline Meko's `meko agent connect claude` | `mcp__meko__memory_search` |

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
  5. on offline Meko, the default datapack (blank `datapack_id`);
  6. otherwise skip datapack-scoped calls and say so once.

  A datapack ID from `MEKO.md` is usually a Cloud datapack. On offline Meko
  without a Cloud link it answers `datapack_access_denied`. Say so once and fall
  back to the local default datapack, which then holds only your own loops'
  summaries.
- **Gotchas datapacks**: a `MEKO.md` row whose datapack name ends in
  `/gotchas` holds the team's mined pitfalls. AgentK's reviewers search it
  with `knowledgebase_search` and credit items as `MG-<n>`. Search it first for
  pitfalls, and keep its `MG-<n>` ids as provenance.
- **Design conversation**: a PR body line `meko_conversation_id:<id>`.

## Read scoping that matters here

- `memory_search` returns only the current user's memories. On someone else's
  PR it will usually find nothing; that is not an error.
- `knowledgebase_search` returns team-shared knowledge for the datapack
  (uploaded docs plus promoted learnings). This is the team path.
- `context_search` with `force_source: "conversation"` searches saved
  conversations of every member on the datapack.

## Quotas and errors

- Offline Meko has no quotas. Keep the same small search budget anyway,
  because every result lands in the orchestrator's context.
- Meko Cloud's Free tier caps searches per user for life (1,000 `memory_search`, 1,000
  `knowledgebase_search`, 1,000 `context_search`). Budget at most five
  searches per loop.
- `free_tier_limit_reached` (possibly HTTP 403) is a failed call, not an empty
  result and not an auth problem. Do not retry. Report it and continue without Meko.
- A response with `error` or an error-shaped `detail` is a failure. Only a
  successful response with an empty `results` array means "nothing found".
