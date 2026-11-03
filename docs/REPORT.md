# review-loop: plugin packaging and Meko evaluation

*2026-11-03. Repository: `~/claude-review-loop` (local only, not pushed).*

## Verdict

1. **The plugin is ready to use.** It is packaged as a Claude Code plugin
   marketplace, it passes strict validation, and it converged end to end on a
   test repository with its own agents and hooks.
2. **Meko is worth a narrow integration, not a deep one.** Two Meko features
   improve the loop: recalling the design decisions behind a change, and
   publishing each loop's outcome as a team learning. Both are built as an
   optional extension. Everything else Meko offers either duplicates what git
   and local state already do well, or would harm the loop.
3. **The most valuable Meko finding was a risk, not a feature.** Without
   safeguards, Meko would have leaked the author's own conversation into the
   "independent" reviewers. That is now blocked in the core plugin, whether or
   not you install the extension.
4. **The Meko extension is untested against live Meko.** The Meko connector in
   this environment is not authenticated, and running the offline Meko binary
   was blocked by the auto-mode classifier. I built the extension against
   Meko's documented contract. Pilot it on one real PR before relying on it.
5. **Offline Meko changes the picture in one important way: AgentK.** Offline
   Meko is a local, quota-free server with the same MCP tools as Meko Cloud,
   so the extension supports it with small changes. Its AgentK extension,
   however, already runs a mature local review loop before every PR. Do not
   run both loops on the same change. Use AgentK's loop inside AgentK and
   review-loop everywhere else, and bridge the two with the prompt overrides
   in `examples/agentk/` (see the addendum below).

## What was delivered

```
claude-review-loop/
├── .claude-plugin/marketplace.json     marketplace "review-loop" listing both plugins
├── plugins/review-loop/                core plugin (v0.2.0)
│   ├── skills/review-loop/             protocol, state engine, gate script, schema, rubric
│   ├── agents/                         fresh-reviewer, finding-verifier
│   └── hooks/hooks.json                Stop guard
├── extensions/meko/                    optional review-loop-meko plugin (v0.1.0)
├── examples/meko-monorepo.review-loop.json
├── tests/test_engine.sh                35 offline checks, no model calls
├── docs/DESIGN.md, docs/REPORT.md
└── .github/workflows/ci.yml            runs the engine tests
```

Install:

```
/plugin marketplace add ~/claude-review-loop
/plugin install review-loop@review-loop
/plugin install review-loop-meko@review-loop     # optional
```

Then delete the standalone copy so the two do not overlap:

```
rm -rf ~/.claude/skills/review-loop ~/.claude/agents/fresh-reviewer.md ~/.claude/agents/finding-verifier.md
```

Until you switch, the standalone copy has been synced with every fix below.

## What Meko is, in one paragraph

Meko is Yugabyte's hosted data layer for agents. Each **datapack** holds
captured conversations, extracted **memories**, a searchable **knowledge
base**, and content-addressed **artifacts**. Private memories can be
**promoted** into shared knowledge that every member's agents can search. Meko
ships a Claude Code plugin, `meko-agent-skills`, whose hooks capture every
session automatically, and an `extensions/` pattern (BMad is the example) for
integrating other workflows. The Meko monorepo already uses it this way:
`MEKO.md` files map code areas to datapacks, and PR bodies carry
`cloud.mekodata.ai://<datapack>` and `meko_conversation_id:` lines that point
at the design conversation.

## Where Meko helps, ranked

| Idea | Value | Cost | Verdict |
|---|---|---|---|
| Feed reviewers the **design decisions** recorded for the PR | High. Reviewers stop flagging intended behaviour as bugs, so there are fewer judgement-call questions. | Up to 2 searches per loop | **Built** (`review-loop-meko-context`) |
| Feed reviewers the **team's known pitfalls** from shared knowledge | High once the team has promoted learnings, near zero before that. | 1 to 2 searches | **Built** (written to `.review-loop/pitfalls.md`) |
| **Publish each loop's outcome** as one memory plus the report artifact | Medium now, compounding over time. Promotion turns it into team knowledge. | 1 search, 1 `artifact_put`, 1 `memory_add` | **Built**, consent-gated (`review-loop-meko-publish`) |
| Recall prior **verified false positives** for triage | Medium. It speeds triage, but the verifier still has to run, because the code may have changed. | Shares the searches above | **Built**, orchestrator only, never sent to reviewers |
| Print the Meko **PR context line** | Low, but free, and the Meko repo's `REVIEW.md` explicitly allows it. | None | **Built** (printed, never edits the PR) |
| Store **loop state** in Meko | Negative. Local state is faster, works offline, and costs no quota. | Many writes | **Rejected** |
| **Capture reviewer sessions** as Meko conversations | Negative. It is noise, and it burns the Free tier's lifetime conversation quota. | 1 conversation per reviewer | **Rejected**, and actively prevented |
| Give **reviewers** Meko access | Harmful. See the next section. | n/a | **Rejected**, and actively prevented |
| Code **RAG** over the knowledge base for reviewers | Low. Reviewers read the live repository, and an index goes stale. | Uploads and quota | **Rejected** |
| `track_token_usage` per round | Low. It is a dashboard nicety. | 1 call per round | **Deferred** |

Quota check: one loop with the extension uses at most six searches and two
writes. On the Free tier's lifetime caps of 1,000 searches per kind, that is
roughly 150 loops or more before any cap matters.

## Risks found, and what now prevents them

| Risk | How it would happen | Mitigation, in the core plugin |
|---|---|---|
| **Author context leaks into reviewers** | Subagents inherit the parent's MCP tools. Meko's `context_search` conversation bucket includes your own captured authoring session, so a reviewer could read your reasoning. | Both reviewer agents now set `disallowedTools: mcp__*`. Tested: the agent saw zero MCP tools. |
| **Gate session captured by Meko** | The headless gate is a new Claude session, so Meko's SessionStart hook would record it and inject context. Meko has no switch to disable capture. | The gate runs with `--strict-mcp-config` and `disableAllHooks`. Tested: zero MCP tools, compared with 21 in a default session. |
| **Quota errors mistaken for empty results** | `free_tier_limit_reached` returns no `results` key, which a naive skill reads as "nothing found". | The extension treats it as a failure, never retries, and continues without Meko. |
| **Wrong datapack** | The monorepo has many datapacks per area, and memory search on a shared datapack returns only your own memories. | Resolution order: config, PR line, nearest `MEKO.md`, pinned datapack, else skip. `knowledgebase_search` is used for team knowledge. |

## Lessons from the Meko repository applied to the core plugin

The monorepo's `REVIEW.md` encodes review policy that any team would want, so
these became generic features:

- **Review guide discovery.** Briefs list every `REVIEW.md`, `AGENTS.md`,
  `CLAUDE.md`, and `CONTRIBUTING.md` that governs the touched files. Reviewers
  cite violated rules as `guideline` findings. In the end-to-end test, two
  such findings were raised and fixed.
- **Size gate.** By default, a change above 20 commits, 100 files, or 5,000
  lines needs your go-ahead first. These are the Meko thresholds, and
  `size_limits` overrides them.
- **Model policy.** `reviewer_model` and `gate_model` in `.review-loop.json`.
  The Meko example sets Opus for both, because its guide requires Opus-class
  models for substantive reviews. That gives up cross-model diversity for
  policy compliance; the separate process still gives independence.
- **No attribution.** The loop never commits or edits PR bodies, so it
  cannot violate the guide's no-AI-attribution rule.
- **Not enforced: the guide's 10-minute and 1M-token review budget.** A skill
  cannot meter its own tokens. The round cap and check selection are the
  practical levers.

## Verification

| Check | Result |
|---|---|
| Offline engine tests | 38 of 38 pass |
| `claude plugin validate --strict` on the marketplace and both plugins | All pass |
| End-to-end run on the standalone skill, with Sonnet orchestrating | Converged in 3 rounds. 5 findings fixed, 1 question. About $0.80. |
| End-to-end run on the packaged plugin, with Sonnet orchestrating | Converged in 3 rounds. 6 findings fixed, 2 of them `REVIEW.md` rules. 1 question. About $0.71. |
| Plugin reviewers block MCP | Confirmed: 0 MCP tools visible |
| Gate isolation flags | Confirmed: 0 MCP tools, against 21 by default |
| Live Meko calls | **Not tested.** The Cloud connector is unauthenticated, and running the offline binary was blocked. |

The packaged run exposed four defects, all now fixed and covered by tests:

1. A reviewer wrote `suggestion` instead of `suggested_fix` and omitted fields,
   so its output was rejected. The orchestrator then skipped that reviewer
   silently. Briefs now include an exact JSON example, ingest repairs common
   aliases, and the next round refuses to start while a reviewer is
   unaccounted for. `drop-reviewer` records an explicit exclusion.
2. A high-confidence nitpick could not be rejected without a verifier, so the
   orchestrator misused "defer". Verifiers are now required only for major
   and blocker findings, and for minor findings the reviewer reproduced.
3. A duplicate of a finding fixed earlier in the same round counted as a
   regression, which caused false oscillation alarms. Only a recurrence in a
   later round counts now.
4. The engine crashed outside a repository, and a missing test tool blocked
   convergence forever. Clear errors and a check probe now handle both.


## Addendum: offline Meko and AgentK

### What offline Meko is

`yugabyte/meko` is the open-source, self-hosted Meko: one binary (`meko`, plus
`meko-cli`) running a local server on `127.0.0.1:7778`, with SQLite and
sqlite-vec storage under `~/.meko`. Embedding and reranking run locally, and a
release ships its models. It serves the same MCP tools as Meko Cloud, with the
same names and argument rules, and adds `knowledgebase_upload_file`, which
uploads by local path. It has no quotas. `meko agent connect claude` registers it
with Claude Code. `meko cloud_link` can route calls for Cloud datapacks to Meko
Cloud while keeping local ones local. It also has its own extension system
(`meko extension install`), whose main extension is AgentK.

### What changes for review-loop

| Topic | With Meko Cloud | With offline Meko | Change made |
|---|---|---|---|
| Cost of recall | Lifetime quota | Free | Kept the small search budget anyway, to protect the orchestrator's context |
| Datapacks | Team IDs in `MEKO.md` | Local default datapack; Cloud IDs fail unless linked | Fall back to the default datapack on `datapack_access_denied`, and say so |
| Publishing | Artifact plus memory | Same, plus an optional one-call KB upload of the report | Added as a separate, consent-gated step |
| Independence risk | `context_search` can return the author's captured conversation | Same; it leaves out only the *caller's own* conversation | No change needed: reviewers already block `mcp__*` |
| Memory extractor | Server-side | Runs your own `claude -p` on turns sent to Meko | No change needed: the gate sends no turns, because its hooks are off |
| Pitfalls source | Any datapack | Any datapack | Prefer AgentK's `/gotchas` datapacks and keep their `MG-<n>` ids |

### AgentK already has a review loop

Before each PR, AgentK reviews the change locally with an algorithm ported from
the cloud reviewer (Sagittarius 3.0 by default):

- five specialist passes, each in a fresh subagent;
- an independent scorer per finding, which keeps only findings that score 50
  or more, or are confirmed;
- a check that each finding sits in a changed hunk;
- optional deep sweeps and re-review rounds, with your triage of findings
  before the task fixes them;
- Meko gotchas, review guides, and `git blame` history in the prompt, and a
  `me_ak_review:` summary line in the PR.

Compared with review-loop:

| | AgentK review | review-loop |
|---|---|---|
| Where it runs | Inside AgentK sessions only | Any Claude Code session, and CI |
| Reviewers can run code | No: Bash is removed | Yes: they run checks and reproduce defects |
| False-positive filter | Independent scorer on every finding | Orchestrator triage, with a verifier on rejections |
| Author declining a finding | Free: "fix or decline each" | A neutral verifier is required for major and blocker findings |
| Later rounds | Told the earlier findings and outcomes | Fresh and unanchored; a ledger handles duplicates |
| Deterministic checks | Run by the task, not a convergence gate | Lint and test results gate convergence |
| Final gate | None beyond the last round | A separate-process review on the final tree |
| UI and telemetry | Rich web UI, phone, usage stats | A Markdown report |

Neither is strictly better. AgentK's scorer is a good idea that review-loop
lacks, and review-loop's executed evidence and verifier-gated declines are what
AgentK lacks.

### What I built from this

- **AgentK bridge** (`examples/agentk/.agent-k/stages/`): overrides of AgentK's
  `code-review-fix` and `code-review-findings` prompts. They keep every AgentK
  placeholder and add review-loop's rules: verify with a test or repro, never
  fix by suppression, and decline a blocker or major finding only after an
  independent verifier says NOT_REAL. This is the cheapest way to improve
  AgentK's weakest step without forking it.
- **Location check in the core plugin**, borrowed from AgentK as a flag rather
  than a filter: each finding is marked `in-change`, `near-change`, or
  `outside-change`. A finding in an untouched file is shown as
  `[outside change]`, and the rubric says when to defer it.
- **Offline Meko support** in the extension, as in the table above.

### Not built, and why

- **Packaging review-loop as a Meko extension** (`meko extension install`).
  Meko extensions are long-running web apps served at `/ext/<name>/`, such as
  AgentK and Presenter. review-loop is a skill that runs inside a Claude Code
  session, so it would gain nothing from that packaging.
- **A per-finding scorer** like AgentK's. It is worth trying later as a cheap
  small-model pass before triage, if reports show too many false positives.

### Testing offline Meko yourself

I could not run the downloaded binary, so here is a sandboxed way to try it,
with no service, Keychain entry, or change to your Claude Code setup:

```
mkdir -p ~/.meko-sandbox && printf '[web]\nenabled = false\n' > ~/.meko-sandbox/config.toml
MEKO_HOME=~/.meko-sandbox MEKO_SECRETS=file MEKO_CALL_HOME=0 MEKO_UPDATE_AUTO=false \
  <path-to>/meko serve --port 17778
```

Or install it normally with `curl -fsSL https://mekodata.ai/install.sh | sh`,
then `meko agent connect claude`. With it running, `review-loop-meko` works
unchanged, because the tool names are the same.

## Recommended next steps

1. **Install the plugin** with the commands above, and remove the standalone copy.
2. **Pilot on one real Meko PR.** Copy
   `examples/meko-monorepo.review-loop.json` to the monorepo root, trimmed to
   the trees you touch, and run `/review-loop:review-loop <PR>`. The check
   probe will flag any linter that is not installed.
3. **Connect a Meko**, either offline (no quotas, nothing leaves your machine)
   or Cloud via `/mcp`, then install `review-loop-meko` and repeat the pilot. Compare how many judgement-call questions the report raises
   with and without design context. That number is the extension's real
   value, so keep it only if it drops.
4. **If you use AgentK**, copy `examples/agentk/.agent-k` into the repository,
   and let AgentK's own review run instead of review-loop on those tasks.
5. **Push to GitHub when you are happy with it.** I did not create a remote,
   because publishing is your call. A private repository is the safe default:
   ```
   cd ~/claude-review-loop && gh repo create claude-review-loop --private --source . --push
   ```
6. **Upstream later, if the pilot pays off.** `extensions/meko/` is
   self-contained, so it can be proposed to `yugabyte/meko-skills` under
   `extensions/review-loop/`, next to the BMad extension.

## Sources

- Meko docs in the monorepo: `meko/docs/content/docs/` (MCP server reference, memory, learnings, integrations)
- Meko review policy: `~/voyager2/meko/REVIEW.md`, and datapack maps such as `amp/compute/MEKO.md`
- Meko Claude Code plugin and BMad extension: https://github.com/yugabyte/meko-skills (v2.5.1)
- Offline Meko and AgentK: https://github.com/yugabyte/meko (release meko-v0.20261103.4): `backend/docs/`, `extensions/agentk/docs/design/code-review.md`, `extensions/agentk/src/server/prompts.ts`
- Claude Code docs: https://code.claude.com/docs/en/sub-agents, https://code.claude.com/docs/en/skills, https://code.claude.com/docs/en/hooks
