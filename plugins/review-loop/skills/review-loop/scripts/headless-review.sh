#!/usr/bin/env bash
# Run one independent reviewer, the gate, or a finding verifier as a SEPARATE, non-interactive
# process of any coding agent's CLI: no shared session, no inherited conversation, optionally a
# different vendor's model. Works for agents without subagents, and for cross-vendor review.
#
# usage: headless-review.sh --mode full|delta|gate|verify [--name NAME] [--agent AGENT] [--model M]
#                           [--lens TEXT] [--finding ID --rebuttal-file FILE] [--budget-turns N] [--allow-mcp]
#   AGENT: claude | codex | gemini | cursor | custom   (default: settings gate_agent, else claude)
#   custom runs settings "headless_command", a shell template with {prompt_file}, {model}, {output}.
#
# Precondition (review modes): `rl.py snapshot` and `rl.py run-checks` for the round already ran.
# Review modes ingest the result; verify prints the verdict and saves it under the round's scratch dir.
#
# Isolation: for claude the process loads NO MCP servers and runs NO hooks (unless --allow-mcp).
# Other CLIs cannot be locked down from here; run them with a profile that has no memory connectors.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
RL="$HERE/rl.py"
SCHEMA="$HERE/../reference/findings.schema.json"
VERIFIER="$HERE/../../../agents/finding-verifier.md"            # Claude Code plugin layout
[[ -f "$VERIFIER" ]] || VERIFIER="$HERE/../agents/finding-verifier.md"  # install.sh layout
MODE=""; NAME=""; AGENT=""; MODEL=""; LENS=""; FINDING=""; REBUTTAL=""; TURNS=80; ALLOW_MCP=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode) MODE="$2"; shift 2;;
    --name) NAME="$2"; shift 2;;
    --agent) AGENT="$2"; shift 2;;
    --model) MODEL="$2"; shift 2;;
    --lens) LENS="$2"; shift 2;;
    --finding) FINDING="$2"; shift 2;;
    --rebuttal-file) REBUTTAL="$2"; shift 2;;
    --budget-turns) TURNS="$2"; shift 2;;
    --allow-mcp) ALLOW_MCP=1; shift;;
    *) echo "unknown arg $1" >&2; exit 2;;
  esac
done
[[ "$MODE" =~ ^(full|delta|gate|verify)$ ]] || { echo "--mode full|delta|gate|verify is required" >&2; exit 2; }
ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"
setting() { "$RL" config 2>/dev/null | jq -r --arg k "$1" '.settings[$k] // empty' || true; }
[[ -z "$AGENT" ]] && AGENT="$(setting gate_agent)"; AGENT="${AGENT:-claude}"
if [[ -z "$MODEL" ]]; then
  key=reviewer_model; [[ "$MODE" == gate ]] && key=gate_model
  MODEL="$(jq -r --arg k "$key" '.[$k] // ""' .review-loop/state.json)"
fi
ROUND="$(jq -r .round .review-loop/state.json)"
DIR=".review-loop/rounds/$ROUND"; mkdir -p "$DIR/scratch"

# Structured output where the CLI can enforce the schema (claude); otherwise the agent writes the file.
OUTMODE=file; [[ "$AGENT" == claude && "$MODE" != verify ]] && OUTMODE=structured
if [[ "$MODE" == verify ]]; then
  [[ -n "$FINDING" ]] || { echo "--finding ID is required for --mode verify" >&2; exit 2; }
  NAME="${NAME:-verify-$FINDING}"
  PROMPT="$DIR/scratch/$NAME.prompt.md"
  { awk 'BEGIN{n=0} /^---$/ && n<2 {n++; next} n>=2' "$VERIFIER"
    printf '\n\n## The finding\n\n```\n'; "$RL" show "$FINDING"; printf '```\n'
    printf '\n## The author'"'"'s rebuttal\n\n'; [[ -n "$REBUTTAL" ]] && cat "$REBUTTAL" || echo "(none given)"
    printf '\n\nRepository: %s\nSnapshot: %s\nScratch directory (the only place you may write): %s/scratch\n' \
      "$ROOT" "$(jq -r --arg r "$ROUND" '.rounds[$r].snapshot // ""' .review-loop/state.json)" "$ROOT/$DIR"
    printf '\nEnd your reply with one line: VERDICT: REAL, VERDICT: NOT_REAL, or VERDICT: UNCERTAIN.\n'
  } > "$PROMPT"
else
  NAME="${NAME:-$MODE}"
  args=(brief --mode "$MODE" --name "$NAME" --output "$OUTMODE"); [[ -n "$LENS" ]] && args+=(--lens "$LENS")
  PROMPT="$("$RL" "${args[@]}" | jq -r .brief)"
fi
RAW="$DIR/$NAME.raw.out"; LAST="$DIR/$NAME.last.txt"; OUT="$DIR/$NAME.json"; ERR="$DIR/$NAME.stderr"
SYS="You are a fresh, independent code reviewer running non-interactively. Never modify the repository; write only under $DIR/scratch/ and the output file your instructions name."

echo "$MODE review via $AGENT: round $ROUND, model ${MODEL:-default}, prompt $PROMPT" >&2
set +e
case "$AGENT" in
  claude)
    a=(-p "$(cat "$PROMPT")" --output-format json --no-session-persistence --max-turns "$TURNS"
       --disallowedTools "Edit Write NotebookEdit" --allowedTools "Bash Read Grep Glob Skill Agent"
       --append-system-prompt "$SYS")
    [[ "$OUTMODE" == structured ]] && a+=(--json-schema "$(cat "$SCHEMA")")
    [[ -n "$MODEL" ]] && a+=(--model "$MODEL")
    [[ "$ALLOW_MCP" == 1 ]] || a+=(--strict-mcp-config --settings '{"disableAllHooks":true}')
    CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1 claude "${a[@]}" > "$RAW" 2> "$ERR";;
  codex)
    a=(exec --skip-git-repo-check --sandbox workspace-write -o "$LAST")
    [[ -n "$MODEL" ]] && a+=(-m "$MODEL")
    codex "${a[@]}" "$SYS"$'\n\n'"$(cat "$PROMPT")" > "$RAW" 2> "$ERR";;
  gemini)
    a=(-p "$SYS"$'\n\n'"$(cat "$PROMPT")" --output-format json --yolo)
    [[ -n "$MODEL" ]] && a+=(-m "$MODEL")
    gemini "${a[@]}" > "$RAW" 2> "$ERR";;
  cursor)
    a=(-p "$SYS"$'\n\n'"$(cat "$PROMPT")" --output-format json --force)
    [[ -n "$MODEL" ]] && a+=(--model "$MODEL")
    cursor-agent "${a[@]}" > "$RAW" 2> "$ERR";;
  custom)
    T="$(setting headless_command)"
    [[ -n "$T" ]] || { echo "agent custom needs \"headless_command\" in the review-loop settings" >&2; exit 2; }
    T="${T//\{prompt_file\}/$PROMPT}"; T="${T//\{model\}/$MODEL}"; T="${T//\{output\}/$OUT}"
    bash -c "$T" > "$RAW" 2> "$ERR";;
  *) echo "unknown agent '$AGENT' (claude, codex, gemini, cursor, custom)" >&2; exit 2;;
esac
rc=$?
set -e
if [[ ! -s "$LAST" ]]; then  # claude wraps the reply in a JSON envelope; keep its text for verdicts
  if [[ "$MODE" == verify ]] && jq -e '.result' "$RAW" >/dev/null 2>&1; then jq -r '.result' "$RAW" > "$LAST"
  else cp "$RAW" "$LAST" 2>/dev/null || true; fi
fi
if [[ $rc -ne 0 ]]; then
  echo "$AGENT exited $rc. See $RAW and $ERR" >&2; tail -20 "$ERR" >&2 || true; exit 1
fi

if [[ "$MODE" == verify ]]; then
  cp "$LAST" "$DIR/scratch/$NAME.verdict.txt"
  tail -40 "$LAST"
  grep -oE 'VERDICT: (REAL|NOT_REAL|UNCERTAIN)' "$LAST" | tail -1 || echo "VERDICT: UNCERTAIN (none stated)"
  exit 0
fi
"$RL" verify-tree || echo "WARNING: the reviewer changed the tree; inspect and restore before continuing" >&2
# The agent wrote the findings file itself (file mode), or returned them on stdout (structured / envelope).
[[ -s "$OUT" ]] || cp "$LAST" "$OUT"
"$RL" ingest "$OUT" --reviewer "$NAME"
