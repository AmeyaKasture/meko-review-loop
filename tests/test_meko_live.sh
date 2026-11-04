#!/usr/bin/env bash
# Live test of the author guide in a local (offline) Meko. Needs `meko` running on 127.0.0.1:7778.
# Uses a throwaway datapack and deletes it at the end. Usage: tests/test_meko_live.sh
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
RL="$HERE/plugins/review-loop/skills/review-loop/scripts/rl.py"
W="$(mktemp -d)"; R="$(mktemp -d)"; SLUG="rl-live/t$$"
export REVIEW_LOOP_HOME="$W/home" REVIEW_LOOP_GUIDE_STORE=meko
pass=0; fail=0
ok()  { pass=$((pass+1)); echo "  ok   $1"; }
bad() { fail=$((fail+1)); echo "  FAIL $1"; }
expect() { if grep -qF -- "$3" <<<"$2"; then ok "$1"; else bad "$1 (wanted: $3)"; echo "$2" | sed 's/^/       /' | head -6; fi; }
cleanup() {
  python3 - "$RL" "$SLUG" <<'PY' || true
import sys, importlib.util
spec = importlib.util.spec_from_file_location("rl", sys.argv[1]); rl = importlib.util.module_from_spec(spec); spec.loader.exec_module(rl)
tok = rl.meko_token(); dp = rl.meko_datapack(tok, sys.argv[2], create=False)
if dp:
    c = rl.meko_call(tok, "conversation_create", {"agent_id": rl.MEKO_AGENT, "session_id": "review-loop-setup", "title": "review-loop setup"})["id"]
    rl.meko_call(tok, "datapack_delete", {"datapack_id": dp, "conversation_id": c}); print("  cleanup: test datapack deleted")
PY
  rm -rf "$W" "$R"
}
trap cleanup EXIT
cd "$R"; git init -q; git remote add origin "https://github.com/$SLUG.git"

echo "save and load through Meko"
printf '## While writing\n- v1: return errors with context.\n' > "$W/v1.md"; printf -- '- R1-1 not-covered\n' > "$W/h.md"
expect "v1 saved in Meko" "$("$RL" guide-push --file "$W/v1.md" --history-file "$W/h.md" --marker m1)" '"saved": "meko:'
expect "pull reads Meko" "$("$RL" guide-pull)" '"source": "meko:'
printf '## While writing\n- v2: return errors with context and the object name.\n' > "$W/v2.md"
"$RL" guide-push --file "$W/v2.md" --marker m2 >/dev/null
rm -f "$REVIEW_LOOP_HOME/guides/rl-live__t$$.md"
ctx="$(echo "{\"cwd\":\"$R\"}" | "$RL" learnings-context)"
expect "hook serves newest version from Meko with no local file" "$ctx" "v2: return errors"
expect "hook names Meko as the source" "$ctx" "(from meko:"
if grep -q "v1:" <<<"$ctx"; then bad "old version not served"; else ok "old version not served"; fi
expect "local cache refreshed from Meko" "$(cat "$REVIEW_LOOP_HOME/guides/rl-live__t$$.md")" "v2:"

echo "fallback when Meko is down"
ctx="$(echo "{\"cwd\":\"$R\"}" | MEKO_URL=http://127.0.0.1:9 REVIEW_LOOP_GUIDE_STORE=auto "$RL" learnings-context)"
expect "falls back to the local copy" "$ctx" "v2: return errors"
expect "says Meko was unavailable" "$ctx" "Meko unavailable"
expect "meko-only store refuses to save silently" "$(MEKO_URL=http://127.0.0.1:9 "$RL" guide-push --file "$W/v1.md" 2>&1 || true)" "guide not saved"

echo; echo "passed $pass, failed $fail"; [[ $fail -eq 0 ]]
