#!/usr/bin/env bash
# Gate review in a COMPLETELY NEW Claude Code process: no shared session,
# no inherited conversation, optionally a different model. Schema-enforced output.
#
# Precondition: the orchestrator already ran `rl.py snapshot --kind gate` and `rl.py run-checks`.
# usage: fresh-session-review.sh [--model MODEL] [--name gate] [--budget-turns N] [--allow-mcp]
#
# Isolation: by default the gate session loads NO MCP servers and runs NO hooks, so
# connectors (memory servers, chat integrations) cannot inject context, capture the session, or spend quota.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
RL="$HERE/rl.py"
SCHEMA="$HERE/../reference/findings.schema.json"
MODEL=""; NAME="gate"; TURNS=80; ALLOW_MCP=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --model) MODEL="$2"; shift 2;;
    --name) NAME="$2"; shift 2;;
    --budget-turns) TURNS="$2"; shift 2;;
    --allow-mcp) ALLOW_MCP=1; shift;;
    *) echo "unknown arg $1" >&2; exit 2;;
  esac
done
ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"
[[ -z "$MODEL" ]] && MODEL="$(jq -r '.gate_model // ""' .review-loop/state.json)"

BRIEF_JSON="$($RL brief --mode gate --name "$NAME")"
BRIEF="$(jq -r .brief <<<"$BRIEF_JSON")"
ROUND="$(jq -r .round .review-loop/state.json)"
RAW=".review-loop/rounds/$ROUND/$NAME.raw.json"
OUT=".review-loop/rounds/$ROUND/$NAME.json"

args=(-p "$(cat "$BRIEF")"
      --output-format json
      --json-schema "$(cat "$SCHEMA")"
      --no-session-persistence
      --max-turns "$TURNS"
      --disallowedTools "Edit Write NotebookEdit"
      --allowedTools "Bash Read Grep Glob Skill Agent"
      --append-system-prompt "You are a fresh, independent code reviewer running non-interactively. Never modify the repository; write only under .review-loop/rounds/$ROUND/scratch/.")
[[ -n "$MODEL" ]] && args+=(--model "$MODEL")
[[ "$ALLOW_MCP" == 1 ]] || args+=(--strict-mcp-config --settings '{"disableAllHooks":true}')

echo "gate review: round $ROUND, model ${MODEL:-default}, brief $BRIEF" >&2
set +e
CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1 claude "${args[@]}" > "$RAW" 2> ".review-loop/rounds/$ROUND/$NAME.stderr"
rc=$?
set -e
if [[ $rc -ne 0 ]] || ! jq -e '.structured_output' "$RAW" >/dev/null 2>&1; then
  echo "gate session failed (exit $rc). See $RAW and .review-loop/rounds/$ROUND/$NAME.stderr" >&2
  jq -r '.result // empty' "$RAW" 2>/dev/null | tail -20 >&2 || true
  exit 1
fi
jq '.structured_output' "$RAW" > "$OUT"
jq -r '"cost $\(.total_cost_usd) turns \(.num_turns)"' "$RAW" >&2
$RL verify-tree || { echo "WARNING: gate reviewer changed the tree" >&2; }
$RL ingest "$OUT" --reviewer "$NAME"
