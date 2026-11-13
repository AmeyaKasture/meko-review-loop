#!/usr/bin/env bash
# Live test of the Meko guide store (scripts/stores/meko_store.py) through rl.py.
# Needs a local Meko running (https://mekodata.ai). Uses a throwaway datapack and deletes it.
# Usage: tests/test_meko_live.sh   (skips with exit 0 when Meko is not running)
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPTS="$HERE/plugins/review-loop/skills/review-loop/scripts"
RL="$SCRIPTS/rl.py"; MS="$SCRIPTS/stores/meko_store.py"
python3 "$MS" check >/dev/null 2>&1 || { echo "skip: Meko is not running"; exit 0; }
W="$(mktemp -d)"; SLUG="rl-live/t$$"
pass=0; fail=0
expect() { if grep -qF -- "$3" <<<"$2"; then pass=$((pass+1)); echo "  ok   $1"; else fail=$((fail+1)); echo "  FAIL $1 (wanted: $3)"; echo "$2" | head -5 | sed 's/^/       /'; fi; }
cleanup() {
  python3 - "$MS" "$SLUG" <<'PY' || true
import importlib.util, sys
spec = importlib.util.spec_from_file_location("ms", sys.argv[1]); ms = importlib.util.module_from_spec(spec); spec.loader.exec_module(ms)
tok = ms.token(); dp = ms.datapack(tok, sys.argv[2], create=False)
if dp:
    c = ms.call(tok, "conversation_create", {"agent_id": ms.AGENT, "session_id": "review-loop-setup", "title": "review-loop test cleanup"})["id"]
    ms.call(tok, "datapack_delete", {"datapack_id": dp, "conversation_id": c}); print("  cleanup: test datapack deleted")
PY
  rm -rf "$W"
}
trap cleanup EXIT
export REVIEW_LOOP_HOME="$W/home" REVIEW_LOOP_GUIDE_STORE=command; mkdir -p "$REVIEW_LOOP_HOME"
printf '{"guide_store": "command", "guide_pull_cmd": "python3 %s pull", "guide_push_cmd": "python3 %s push"}\n' "$MS" "$MS" > "$REVIEW_LOOP_HOME/settings.json"
R="$W/repo"; mkdir -p "$R"; cd "$R"; git init -q; git remote add origin "https://github.com/$SLUG.git"

echo "save and load through Meko"
expect "empty store pulls nothing" "$("$RL" guide-pull)" '"source": "none"'
printf '## While writing\n- v1: return errors with context.\n' > "$W/v1.md"; printf -- '- R1-1 not-covered\n' > "$W/h.md"
expect "v1 saved in Meko" "$("$RL" guide-push --file "$W/v1.md" --history-file "$W/h.md" --marker m1)" '"saved": "store:meko:'
expect "pull reads Meko" "$("$RL" guide-pull)" '"source": "store"'
printf '## While writing\n- v2: return errors with context and the object id.\n' > "$W/v2.md"
"$RL" guide-push --file "$W/v2.md" --marker m2 >/dev/null
rm "$REVIEW_LOOP_HOME/guides/"*.md
ctx="$(echo "{\"cwd\":\"$R\"}" | "$RL" learnings-context)"
expect "hook serves the newest version with no local file" "$ctx" "v2: return errors"
expect "local copy refreshed from Meko" "$(cat "$REVIEW_LOOP_HOME/guides/rl-live__t$$.md")" "v2:"

echo "fallback when Meko is down"
ctx="$(echo "{\"cwd\":\"$R\"}" | MEKO_URL=http://127.0.0.1:9 REVIEW_LOOP_GUIDE_STORE=auto "$RL" learnings-context)"
expect "local copy still served" "$ctx" "v2: return errors"
expect "says the store was unavailable" "$ctx" "guide store unavailable"
expect "strict store refuses to save silently" "$(MEKO_URL=http://127.0.0.1:9 "$RL" guide-push --file "$W/v1.md" 2>&1 || true)" "guide not saved"
echo; echo "passed $pass, failed $fail"; [[ $fail == 0 ]]
