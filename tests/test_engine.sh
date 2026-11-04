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
if grep -q "JSON tags are a wire contract" "$b"; then bad "author pitfalls kept out of reviewer brief"; else ok "author pitfalls kept out of reviewer brief"; fi
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
expect "in-change finding located" "$(jq -r '.findings[] | select(.id=="R1-1") | .location' .review-loop/state.json)" "in-change"
expect "location recorded for guideline finding" "$(jq -r '.findings[] | select(.id=="R1-2") | .location' .review-loop/state.json)" "in-change"
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
echo '{"reviewer":"o","round":2,"verdict":"issues_found","findings":[{"severity":"minor","confidence":"medium","category":"correctness","file":"stats.py","line":2,"title":"mean of empty list","claim":"ZeroDivisionError","evidence":"len(xs)","failure_scenario":"mean([])"}]}' > "$W/o.json"
"$RL" brief --mode full --name o >/dev/null
expect "untouched file flagged outside change" "$("$RL" ingest "$W/o.json")" "[outside change]"
"$RL" triage "$(jq -r '.findings[] | select(.reviewer=="o") | .id' .review-loop/state.json)" defer --note "pre-existing" >/dev/null
echo '{"reviewer":"d2","round":2,"verdict":"issues_found","findings":[{"severity":"nit","confidence":"high","category":"style","file":"pkg/stats2.py","line":1,"title":"rename s","claim":"x","evidence":"x","failure_scenario":"x"}]}' > "$W/d2.json"
"$RL" brief --mode full --name d2 >/dev/null; "$RL" ingest "$W/d2.json" >/dev/null
nit_id="$(jq -r '.findings[] | select(.reviewer=="d2") | .id' .review-loop/state.json)"
expect "high-confidence nit rejectable without verifier" "$("$RL" triage "$nit_id" reject --note "style preference")" "rejected"
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

echo "private per-repo config, team review skill, symlinked rule files"
export REVIEW_LOOP_HOME="$W/home"; export REVIEW_LOOP_GUIDE_STORE=file; mkdir -p "$REVIEW_LOOP_HOME/repos" "$REVIEW_LOOP_HOME/learnings"
T2="$(mktemp -d)"; cd "$T2"; git init -q -b main; git config user.email t@t; git config user.name t
git remote add origin https://github.com/acme/widget.git
printf 'def load(m, k):\n    return m[k]\n' > lib.py
printf '# Rules\nCheck ok on map reads.\n' > AGENTS.md; ln -s AGENTS.md CLAUDE.md; mkdir .cursor; ln -s ../AGENTS.md .cursor/BUGBOT.md
mkdir -p .claude/skills/branch-review; printf -- '---\nname: branch-review\n---\nreview\n' > .claude/skills/branch-review/SKILL.md
git add -A; git commit -qm init; git checkout -qb feat
printf 'def load(m, k):\n    v = m[k]\n    return v\n' > lib.py; git commit -qam change
echo '{"review_skill": "branch-review", "gate": false, "max_rounds": 3}' > "$REVIEW_LOOP_HOME/repos/acme__widget.json"
out="$("$RL" init --session S9 --target feat --intent "Load values." 2>&1)"
expect "private config found" "$out" "acme__widget.json"
expect "review skill recorded" "$out" '"review_skill": "branch-review"'
"$RL" snapshot >/dev/null; "$RL" run-checks >/dev/null
b="$(jq -r .brief <<<"$("$RL" brief --mode full --name a)")"
expect "brief names team review skill" "$(cat "$b")" 'This team reviews with the `branch-review` skill'
expect "brief maps QUESTION severity" "$(cat "$b")" 'QUESTION -> `question`'
expect "brief forbids reading author lessons" "$(cat "$b")" "~/.claude/review-loop/"
if grep -q '`CLAUDE.md`\|BUGBOT' "$b"; then bad "symlinked rule files listed once"; else ok "symlinked rule files listed once"; fi

echo "three outcomes, lessons, report order"
cat > "$W/t2a.json" <<'J'
{"reviewer":"a","round":1,"verdict":"issues_found","checks_run":[],"findings":[
 {"severity":"major","confidence":"high","category":"guideline","file":"lib.py","line":2,"title":"map read without ok","claim":"KeyError","evidence":"AGENTS.md","failure_scenario":"load({}, 'x')"},
 {"severity":"question","confidence":"medium","category":"maintainability","file":"lib.py","line":3,"title":"why a temp variable?","claim":"v is unused otherwise","evidence":"lib.py:3","failure_scenario":"n/a"}]}
J
"$RL" ingest "$W/t2a.json" >/dev/null
expect "doubtful needs a reason" "$("$RL" triage R1-2 doubtful 2>&1 || true)" "needs --note"
"$RL" triage R1-2 doubtful --note "Readability choice; the code alone cannot decide" >/dev/null
expect "doubtful gets default options" "$(jq -r '.findings[] | select(.id=="R1-2") | .options[0]' .review-loop/state.json)" "Keep the current design"
"$RL" triage R1-1 accept --note real >/dev/null
expect "fixed records miss cause" "$("$RL" fixed R1-1 --note "use .get" --cause not-covered --why "No rule on optional keys" --lesson "Check ok on every map read of optional keys")" "marked fixed"
expect "covered-* cause needs the rule it ignored" "$("$RL" miss R1-1 --cause covered-ignored --why x 2>&1 || true)" "needs --guide-ref"
expect "next round after fix" "$("$RL" status)" "NEXT: NEXT_ROUND"
"$RL" snapshot >/dev/null; "$RL" run-checks >/dev/null; "$RL" brief --mode delta --name b >/dev/null
echo '{"reviewer":"b","round":2,"verdict":"clean","checks_run":[],"findings":[]}' > "$W/t2b.json"; "$RL" ingest "$W/t2b.json" >/dev/null
expect "converged without gate" "$("$RL" status)" "NEXT: CONVERGED"
rep="$(cat "$("$RL" report)")"
d_line="$(grep -n 'Possible design differences' <<<"$rep" | cut -d: -f1)"; v_line="$(grep -n '## Valid: fixed' <<<"$rep" | cut -d: -f1)"
if [[ -n "$d_line" && -n "$v_line" && "$d_line" -lt "$v_line" ]]; then ok "design differences lead the report"; else bad "design differences lead the report"; fi
expect "report shows lesson" "$rep" "Lesson: Check ok on every map read"
expect "report shows why it was missed" "$rep" "Missed because: not-covered"
expect "report has not-valid section" "$rep" "## Not valid: code kept as is"
"$RL" finish --status converged >/dev/null
expect "init refuses to drop unsaved lessons" "$("$RL" init --session S10 2>&1 || true)" "lessons that were never saved"
lj="$(cat "$("$RL" export-learnings)")"
expect "export carries lesson" "$lj" "Check ok on every map read"
expect "export names private author guide" "$lj" "$REVIEW_LOOP_HOME/guides/acme__widget.md"
expect "export carries miss cause" "$lj" '"cause": "not-covered"'
expect "export lists miss taxonomy" "$lj" '"covered-ignored"'
expect "export lists design difference" "$lj" "why a temp variable?"

echo "SessionStart learnings for the author session"
expect "no learnings -> silent" "x$(echo "{\"cwd\":\"$T2\"}" | "$RL" learnings-context)" "x"
mkdir -p "$REVIEW_LOOP_HOME/guides"; printf '## Correctness patterns\n### Check ok on map reads\n' > "$REVIEW_LOOP_HOME/guides/acme__widget.md"
ctx="$(echo "{\"cwd\":\"$T2\"}" | "$RL" learnings-context)"
expect "learnings injected as additionalContext" "$ctx" '"hookEventName": "SessionStart"'
expect "learnings text present" "$ctx" "Check ok on map reads"

echo "author guide store (file mode; Meko is covered by tests/test_meko_live.sh)"
printf '## While writing\n### Errors\n- Return errors with context.\n' > "$W/g1.md"; printf -- '- R1-1 not-covered: added Errors item\n' > "$W/h1.md"
gp="$("$RL" guide-push --file "$W/g1.md" --history-file "$W/h1.md" --marker "review-loop:acme/widget:aaa:bbb")"
expect "guide saved locally in file mode" "$gp" '"saved": "file"'
expect "guide stamped with marker" "$(head -1 "$REVIEW_LOOP_HOME/guides/acme__widget.md")" "review-loop:acme/widget:aaa:bbb"
expect "history recorded" "$(cat "$REVIEW_LOOP_HOME/guides/acme__widget.history.md")" "not-covered: added Errors item"
expect "same loop not recorded twice" "$("$RL" guide-push --file "$W/g1.md" --history-file "$W/h1.md" --marker "review-loop:acme/widget:aaa:bbb")" "marker already recorded"
if [[ "$(grep -c 'author guide for' "$REVIEW_LOOP_HOME/guides/acme__widget.md")" == 1 ]]; then ok "stamp not duplicated on re-save"; else bad "stamp not duplicated on re-save"; fi
expect "guide-pull reads local copy" "$("$RL" guide-pull)" '"source": "file"'
expect "hook serves saved guide" "$(echo "{\"cwd\":\"$T2\"}" | "$RL" learnings-context)" "Return errors with context"

echo "PR comments in the same thread"
"$RL" reopen --pr 7 >/dev/null; "$RL" snapshot --kind pr >/dev/null
pi="$("$RL" pr-ingest --from-file "$HERE/tests/fixtures/pr-comments.json")"
expect "resolved and own comments skipped" "$pi" "4 open comment(s)"
expect "tagged AI warning -> major" "$pi" "major    @ai-bot"
expect "untagged inline -> major" "$pi" "major    @bob"
expect "PR-level question -> question" "$pi" "question @carol"
expect "re-ingest skips seen comments" "$("$RL" pr-ingest --from-file "$HERE/tests/fixtures/pr-comments.json")" "0 open comment(s)"
ids=($(jq -r '.findings[] | select(.reviewer=="github") | .id' .review-loop/state.json))
"$RL" triage "${ids[0]}" accept --note real >/dev/null
expect "not-valid major PR comment needs verifier" "$("$RL" triage "${ids[1]}" reject --note naming 2>&1 || true)" "requires an independent verifier"
"$RL" triage "${ids[1]}" reject --verifier not-real --note "load matches the module's naming convention" >/dev/null
"$RL" triage "${ids[2]}" doubtful --note "The cache layer is a deliberate design choice" >/dev/null
"$RL" triage "${ids[3]}" accept --note real >/dev/null
expect "accepted PR comments must be applied" "$("$RL" status)" "NEXT: FIX"
expect "fix without cause nudges for diagnosis" "$("$RL" fixed "${ids[0]}" "${ids[3]}" --note "error returned; test added")" "no --cause"
expect "miss analysis after the fact" "$("$RL" miss "${ids[0]}" --cause covered-ignored --guide-ref "AGENTS.md: fail loudly" --why "Rule was clear; not applied in a new helper")" "missed because covered-ignored"
expect "PR fixes go back through fresh review" "$("$RL" status)" "NEXT: NEXT_ROUND"
dr="$("$RL" pr-replies)"; drafts="$(cat .review-loop/pr-replies.md)"
expect "drafts posted nothing" "$dr" "Nothing was posted"
expect "fixed reply drafted" "$drafts" "[AI] Fixed: error returned"
expect "not-valid reply drafted" "$drafts" "Keeping this as is"
expect "doubtful reply drafted" "$drafts" "Possible design difference"
expect "post refused before push" "$("$RL" pr-replies --post 2>&1 || true)" "refusing to post"
cd "$T"; rm -rf "$T2"

echo "comment severity formats seen on real PRs"
sev="$(python3 -c "
import sys; sys.path.insert(0, '$(dirname "$RL")'); import rl
for b in ['**minor** — breaks a rule', '[AI][Warning] **x**', '[AI][Suggestion] **y**', 'High Severity bug', 'Why here?', 'Rename it.']:
    print(rl.comment_severity(b, True), rl.first_line(b))")"
expect "bold severity prefix" "$sev" "minor breaks a rule"
expect "[AI][Warning] tag" "$sev" "major x"
expect "[AI][Suggestion] tag" "$sev" "minor y"
expect "bugbot-style severity" "$sev" "major High Severity bug"
expect "untagged question" "$sev" "question Why here?"
expect "untagged inline request" "$sev" "major Rename it."

echo; echo "passed $pass, failed $fail"; [[ $fail -eq 0 ]]
