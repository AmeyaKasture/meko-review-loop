# claude-review-loop

A Claude Code plugin that turns "a second AI with a fresh context always
finds bugs" into a bounded, repeatable process.

```
/review-loop:review-loop            # current branch + uncommitted changes
/review-loop:review-loop 1234       # check out PR #1234 and harden it
/review-loop:review-loop --strict   # minor findings block convergence too
```

Each round, read-only reviewer agents with no author context run your checks,
try to reproduce defects, and return structured findings. Your session triages
them. Rejecting a serious finding requires a neutral verifier agent. Your
session fixes what is real, and the loop repeats until a round is clean. A
final gate review then runs in a completely separate Claude process. The
report asks you about every judgement call instead of guessing.

## Install

```
/plugin marketplace add ~/claude-review-loop        # or the git URL once pushed
/plugin install review-loop@review-loop
/plugin install review-loop-meko@review-loop        # optional, needs Meko Cloud or offline Meko connected
```

If you used the earlier standalone copy, remove it so the two do not overlap:

```
rm -rf ~/.claude/skills/review-loop ~/.claude/agents/fresh-reviewer.md ~/.claude/agents/finding-verifier.md
```

## Per-repository defaults

Commit a `.review-loop.json` at the repository root so everyone gets the same
checks and limits. Command-line flags override it.

```json
{
  "checks": ["make lint", "pytest -q tests/unit"],
  "max_rounds": 5,
  "strict": false,
  "gate": true,
  "gate_model": "sonnet",
  "reviewer_model": "opus",
  "size_limits": { "commits": 20, "files": 100, "lines": 5000 },
  "meko": { "datapack_id": "<uuid>" }
}
```

## Repository layout

| Path | What it is |
|---|---|
| `plugins/review-loop/` | The core plugin: skill, two agents, Stop guard hook. |
| `plugins/review-loop/skills/review-loop/scripts/rl.py` | Deterministic state engine. |
| `plugins/review-loop/skills/review-loop/scripts/fresh-session-review.sh` | Isolated gate review in a new process. |
| `extensions/meko/` | Optional Meko layer: design-context recall and consent-gated publishing. |
| `examples/agentk/` | Prompt overrides that bring review-loop's triage rules into AgentK. |
| `tests/test_engine.sh` | Offline engine tests with no model calls. |
| `docs/DESIGN.md` | Why each safeguard exists, and how this compares with other tools. |
| `docs/REPORT.md` | Packaging and Meko evaluation report. |

## Development

```
bash tests/test_engine.sh                      # engine tests
claude plugin validate --strict .              # marketplace
claude plugin validate --strict plugins/review-loop
claude --plugin-dir plugins/review-loop        # try it without installing
```

## License

Apache-2.0.
