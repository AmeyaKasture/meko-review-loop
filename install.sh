#!/usr/bin/env bash
# Install Meko Review Loop for a coding agent.
#
# usage: ./install.sh --agent claude|codex|cursor|gemini|agents [--project DIR] [--dir SKILLS_DIR]
#   claude   installs the Claude Code plugin (skills, reviewer subagents, hooks)
#   others   copies the skills into the agent's skills directory:
#              agents -> ~/.agents/skills     codex  -> ~/.codex/skills
#              cursor -> ~/.cursor/skills     gemini -> ~/.gemini/skills
#            --project DIR installs into DIR/<same subpath> instead of your home directory;
#            --dir sets the skills directory explicitly (for any other agent).
# Re-run it to update. Skills are renamed review-loop, review-loop-setup, review-loop-respond,
# and review-loop-exit, since agents without plugin namespaces would see bare "setup" and "exit".
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
PLUGIN="$SRC/plugins/review-loop"
AGENT=""; PROJECT=""; DIR=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --agent) AGENT="$2"; shift 2;;
    --project) PROJECT="$(cd "$2" && pwd)"; shift 2;;
    --dir) DIR="$2"; shift 2;;
    -h|--help) sed -n '2,13p' "$0"; exit 0;;
    *) echo "unknown arg $1" >&2; exit 2;;
  esac
done
[[ -n "$AGENT" ]] || { sed -n '2,13p' "$0"; exit 2; }
command -v python3 >/dev/null || { echo "python3 is required" >&2; exit 1; }
command -v git >/dev/null || { echo "git is required" >&2; exit 1; }

if [[ "$AGENT" == claude ]]; then
  scope=user; [[ -n "$PROJECT" ]] && scope=project
  (cd "${PROJECT:-.}" && claude plugin marketplace add "$SRC" --scope "$scope" && \
     claude plugin install review-loop@meko-review-loop --scope "$scope")
  echo "Installed the Claude Code plugin ($scope scope). Start a new session, then run /review-loop:setup."
  exit 0
fi

if [[ -z "$DIR" ]]; then
  case "$AGENT" in
    agents) sub=.agents/skills;; codex) sub=.codex/skills;; cursor) sub=.cursor/skills;; gemini) sub=.gemini/skills;;
    *) echo "unknown agent '$AGENT'; use --dir SKILLS_DIR for other agents" >&2; exit 2;;
  esac
  DIR="${PROJECT:-$HOME}/$sub"
fi
mkdir -p "$DIR"
DIR="$(cd "$DIR" && pwd)"

copy_skill() {  # source skill dir name, installed name
  local from="$PLUGIN/skills/$1" to="$DIR/$2"
  rm -rf "$to"; cp -R "$from" "$to"
  find "$to" -name '__pycache__' -prune -exec rm -rf {} +
  # Concrete paths instead of Claude Code's variables; namespaced names and cross-references.
  find "$to" -name '*.md' -print0 | while IFS= read -r -d '' f; do
    python3 - "$f" "$to" "$2" <<'PY'
import re, sys
path, skill_dir, name = sys.argv[1:]
s = open(path).read()
s = s.replace("${CLAUDE_SKILL_DIR}", skill_dir)
for old, new in (("exit", "review-loop-exit"), ("respond", "review-loop-respond"), ("setup", "review-loop-setup")):
    s = s.replace(f"/../{old}/", f"/../{new}/")
    s = s.replace(f"/review-loop:{old}", f"the {new} skill")
s = s.replace("/review-loop:review-loop", "the review-loop skill")
if path.endswith("/SKILL.md"):
    s = re.sub(r"^name: .*$", f"name: {name}", s, count=1, flags=re.M)
open(path, "w").write(s)
PY
  done
}
copy_skill review-loop review-loop
copy_skill setup review-loop-setup
copy_skill respond review-loop-respond
copy_skill exit review-loop-exit
# Reviewer and verifier instructions, used by headless-review.sh and by agents with subagents.
mkdir -p "$DIR/review-loop/agents"; cp "$PLUGIN"/agents/*.md "$DIR/review-loop/agents/"
echo "$SRC" > "$DIR/review-loop/.installed-from"
chmod +x "$DIR/review-loop/scripts/"*.py "$DIR/review-loop/scripts/"*.sh "$DIR/review-loop/scripts/stores/"*.py

cat <<EOF
Installed into $DIR:
  review-loop          the loop
  review-loop-setup    set up a repository (run this next, inside the repository)
  review-loop-respond  answer PR review comments
  review-loop-exit     revise your author guide

Next: in your agent, inside the repository, ask it to use the review-loop-setup skill
(or: "Follow $DIR/review-loop-setup/SKILL.md").
EOF
