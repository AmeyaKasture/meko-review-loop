#!/usr/bin/env bash
# Offline regression tests for rl.py. No model calls. Usage: tests/test_engine.sh
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
RL="$HERE/plugins/review-loop/skills/review-loop/scripts/rl.py"
T="$(mktemp -d)"; W="$(mktemp -d)"; trap 'rm -rf "$T" "$W"' EXIT
pass=0; fail=0
ok()   { pass=$((pass+1)); echo "  ok   $1"; }
bad()  { fail=$((fail+1)); echo "  FAIL $1"; }
expect() { # name, haystack, needle
  if grep -qF -- "$3" <<<"$2"; then ok "$1"; else bad "$1 (wanted: $3)"; echo "$2" | sed 's/^/       /' | head -8; fi; }

cd "$T"; git init -q -b main; git config user.email t@t; git config user.name t
printf 'def mean(xs):\n    return sum(xs) / len(xs)\n' > stats.py
printf '# Review guide\nAll public functions need docstrings.\n' > REVIEW.md
git add -A; git commit -qm init; git checkout -qb feature
mkdir -p pkg; printf 'def median(xs):\n    s = sorted(xs)\n    return s[len(s)//2]\n' > pkg/stats2.py
printf 'Use pytest.\n' > pkg/AGENTS.md
git add -A; git commit -qm "add median"
echo "untracked" > notes.txt
cat > .review-loop.json <<'J'
{"checks": ["python3 -c 'print(1)'"], "max_rounds": 2, "reviewer_model": "opus", "size_limits": {"files": 2}}
J
echo "Add median()." > "$W/intent.txt"

echo "init/config/size gate"
out="$("$RL" init --session S1 --target feature --intent-file "$W/intent.txt" 2>&1)"
expect "config checks loaded" "$out" "print(1)"
expect "reviewer model from config" "$out" '"reviewer_model": "opus"'
expect "size gate trips on files>2" "$out" "SIZE GATE"
expect "engine path recorded" "$(jq -r .engine .review-loop/state.json)" "rl.py"
expect "state excluded from git" "$(git status --short)" "?? .review-loop.json"
if git status --short | grep -q '.review-loop/'; then bad ".review-loop/ hidden from git status"; else ok ".review-loop/ hidden from git status"; fi

echo "set-intent before round 1"
echo "Add median(). Decision: even lists average the middles." > "$W/i2"
expect "set-intent works at round 0" "$("$RL" set-intent --file "$W/i2")" "intent updated"

echo "probe + env errors"
"$RL" set-checks "definitely-not-a-tool-xyz;;python3 -c 'print(1)'" >/dev/null
expect "probe flags missing tool" "$("$RL" run-checks --probe)" "[ENV-ERROR]"

echo "snapshot/brief/guides/pitfalls"
"$RL" set-checks "python3 -c 'print(1)'" >/dev/null
"$RL" snapshot >/dev/null; "$RL" run-checks >/dev/null
printf -- '- JSON tags are a wire contract (kb:123)\n' > .review-loop/pitfalls.md
b="$(jq -r .brief <<<"$("$RL" brief --mode full --name a)")"
expect "root REVIEW.md listed" "$(cat "$b")" '`REVIEW.md`'
expect "nested AGENTS.md listed" "$(cat "$b")" '`pkg/AGENTS.md`'
expect "pitfalls injected into lens" "$(cat "$b")" "JSON tags are a wire contract"
expect "untracked file in snapshot" "$(git show --stat refs/review-loop/round-1)" "notes.txt"
expect "set-intent refused after round 1" "$("$RL" set-intent --file "$W/i2" 2>&1 || true)" "only change before round 1"
expect "status waits for reviewers" "$("$RL" status)" "NEXT: REVIEW"

echo "ingest/triage rules"
cat > "$W/a.json" <<'J'
{"reviewer":"a","round":1,"verdict":"issues_found","checks_run":[],"findings":[
 {"severity":"major","confidence":"high","category":"correctness","file":"pkg/stats2.py","line":3,"title":"median wrong for even lists","claim":"upper middle","evidence":"s[len//2]","failure_scenario":"[1,2,3,4] -> 3"},
 {"severity":"minor","confidence":"medium","category":"guideline","file":"pkg/stats2.py","line":1,"title":"missing docstring","claim":"REVIEW.md rule","evidence":"no docstring","failure_scenario":"n/a"}]}
J
"$RL" brief --mode full --name b >/dev/null
sed 's/"reviewer":"a"/"reviewer":"b"/' "$W/a.json" > "$W/b.json"
"$RL" ingest "$W/a.json" >/dev/null
expect "same-round duplicate hinted" "$("$RL" ingest "$W/b.json")" "~dup? R1-1"
expect "major reject needs verifier" "$("$RL" triage R1-1 reject --note x 2>&1 || true)" "requires an independent verifier"
"$RL" triage R1-1 accept >/dev/null; "$RL" triage R1-2 accept >/dev/null
"$RL" triage R1-3 duplicate --duplicate-of R1-1 >/dev/null
"$RL" triage R1-4 duplicate --duplicate-of R1-2 >/dev/null
expect "FIX next" "$("$RL" status)" "NEXT: FIX"
expect "guard blocks own session" "$(echo "{\"session_id\":\"S1\",\"cwd\":\"$T\"}" | "$RL" guard)" '"decision": "block"'
expect "guard ignores other session" "x$(echo "{\"session_id\":\"S2\",\"cwd\":\"$T\"}" | "$RL" guard)" "x"
"$RL" fixed R1-1 R1-2 --note fixed >/dev/null
expect "next round after blocking fix" "$("$RL" status)" "NEXT: NEXT_ROUND"

echo "unaccounted reviewer blocks the next round"
"$RL" brief --mode full --name late >/dev/null
expect "snapshot refuses with missing reviewer" "$("$RL" snapshot 2>&1 || true)" "reviewer output not ingested: late"
FX="$HERE/tests/fixtures/reviewer-aliased-fields.json"
jq '.reviewer="late" | .round=1' "$FX" > "$W/late.json"
expect "aliased fields repaired on ingest" "$("$RL" ingest "$W/late.json")" "repaired"
for id in $(jq -r '.findings[] | select(.reviewer=="late") | .id' .review-loop/state.json); do "$RL" triage "$id" duplicate --duplicate-of R1-1 >/dev/null; done
expect "brief carries exact example" "$(cat .review-loop/rounds/1/brief-late.md)" '"failure_scenario":'

echo "round 2 clean -> gate -> converged; answers; learnings"
"$RL" snapshot >/dev/null; "$RL" run-checks >/dev/null; "$RL" brief --mode delta --name d >/dev/null
cat > "$W/d.json" <<'J'
{"reviewer":"d","round":2,"verdict":"issues_found","checks_run":[],"findings":[
 {"severity":"minor","confidence":"low","category":"api-contract","file":"pkg/stats2.py","line":1,"title":"empty list behaviour undefined","claim":"raise or return None?","evidence":"x","failure_scenario":"median([])"}]}
J
"$RL" ingest "$W/d.json" >/dev/null
echo '{"reviewer":"d2","round":2,"verdict":"issues_found","findings":[{"severity":"nit","confidence":"high","category":"style","file":"pkg/stats2.py","line":1,"title":"rename s","claim":"x","evidence":"x","failure_scenario":"x"}]}' > "$W/d2.json"
"$RL" brief --mode full --name d2 >/dev/null; "$RL" ingest "$W/d2.json" >/dev/null
expect "high-confidence nit rejectable without verifier" "$("$RL" triage R2-2 reject --note "style preference")" "R2-2 -> rejected"
"$RL" triage R2-1 ask --option "Raise ValueError (Recommended)" --option "Return None" --recommend A --note "intent silent" >/dev/null
expect "gate requested" "$("$RL" status)" "NEXT: GATE"
"$RL" snapshot --kind gate >/dev/null; "$RL" run-checks >/dev/null; "$RL" brief --mode gate --name gate >/dev/null
echo '{"reviewer":"gate","round":3,"verdict":"clean","checks_run":[],"findings":[]}' > "$W/g.json"
"$RL" ingest "$W/g.json" >/dev/null
expect "converged" "$("$RL" status)" "NEXT: CONVERGED"
"$RL" answer R2-1 A --note "per team convention" >/dev/null
rep="$(cat "$("$RL" report)")"
expect "report shows decision" "$rep" "Decisions you made"
if grep -q "(Recommended) \*\*(recommended)\*\*" <<<"$rep"; then bad "no doubled recommended label"; else ok "no doubled recommended label"; fi
lj="$(cat "$("$RL" export-learnings)")"
expect "learnings marker" "$lj" '"marker": "review-loop:'
expect "learnings defect classes" "$lj" '"correctness": 1'
expect "learnings user decision" "$lj" "Raise ValueError"

echo "verify-tree"
expect "tree intact" "$("$RL" verify-tree)" "tree intact"
echo "tamper" >> stats.py
expect "tamper detected" "$("$RL" verify-tree 2>&1 || true)" "TREE CHANGED"

echo "cross-round recurrence -> regression -> oscillation question"
git checkout -q -- stats.py
"$RL" init --force --session S3 --checks "true" >/dev/null 2>&1
F1='{"severity":"major","confidence":"high","category":"edge-case","file":"pkg/stats2.py","line":3,"title":"empty list crashes","claim":"IndexError","evidence":"s[0]","failure_scenario":"median([])"}'
for r in 1 2 3; do
  "$RL" snapshot >/dev/null; "$RL" run-checks >/dev/null; "$RL" brief --mode full --name x$r >/dev/null
  echo "{\"reviewer\":\"x$r\",\"round\":$r,\"verdict\":\"issues_found\",\"findings\":[$F1]}" > "$W/x$r.json"
  "$RL" ingest "$W/x$r.json" >/dev/null
  if [[ $r == 1 ]]; then "$RL" triage R1-1 accept >/dev/null; "$RL" fixed R1-1 >/dev/null; fi
  if [[ $r == 2 ]]; then expect "round-2 recurrence is a regression" "$("$RL" triage R2-1 duplicate --duplicate-of R1-1)" "regression"; "$RL" fixed R2-1 >/dev/null; fi
  if [[ $r == 3 ]]; then expect "second recurrence becomes a question" "$("$RL" triage R3-1 duplicate --duplicate-of R2-1)" "OSCILLATION"; fi
done

echo; echo "passed $pass, failed $fail"; [[ $fail -eq 0 ]]
