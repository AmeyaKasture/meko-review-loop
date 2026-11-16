#!/usr/bin/env bash
# Install Meko Review Loop for your coding agents. No clone needed:
#
#   curl -fsSL https://raw.githubusercontent.com/AmeyaKasture/meko-review-loop/main/install.sh | bash
#
# With no options it installs for every agent CLI it finds (claude, codex, cursor-agent, gemini),
# or into ~/.agents/skills if it finds none. Re-run it to update.
#
# Options (after "bash -s --" when piped):
#   --agent LIST     claude, codex, cursor, gemini, agents (comma-separated) instead of detecting
#   --project DIR    install into one repository instead of your home directory
#   --dir DIR        the skills directory for an agent not listed above
#
#   claude   installs the Claude Code plugin (skills, reviewer subagents, hooks)
#   others   copy the skills into <home or project>/.<agent>/skills as review-loop,
#            review-loop-setup, review-loop-respond, and review-loop-exit
#
# Environment: REVIEW_LOOP_REPO (owner/name), REVIEW_LOOP_REF (branch or tag, default main),
# REVIEW_LOOP_TARBALL (archive URL; overrides both).
set -euo pipefail
REPO="${REVIEW_LOOP_REPO:-AmeyaKasture/meko-review-loop}"
REF="${REVIEW_LOOP_REF:-main}"
TARBALL="${REVIEW_LOOP_TARBALL:-https://codeload.github.com/$REPO/tar.gz/$REF}"

usage() { cat <<'EOF'
Usage: install.sh [--agent claude,codex,cursor,gemini,agents] [--project DIR] [--dir DIR]

  curl -fsSL https://raw.githubusercontent.com/AmeyaKasture/meko-review-loop/main/install.sh | bash
  curl -fsSL https://raw.githubusercontent.com/AmeyaKasture/meko-review-loop/main/install.sh | bash -s -- --agent codex

With no --agent, installs for every agent CLI found on PATH (claude, codex, cursor-agent,
gemini), or into ~/.agents/skills if none is found. Re-run to update.

  --agent LIST   comma-separated: claude, codex, cursor, gemini, agents
  --project DIR  install into one repository instead of your home directory
  --dir DIR      skills directory for any other agent (implies one non-Claude install)
EOF
}

AGENTS=""; PROJECT=""; DIR=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --agent) AGENTS="$2"; shift 2;;
    --project) PROJECT="$(cd "$2" && pwd)"; shift 2;;
    --dir) DIR="$2"; shift 2;;
    -h|--help) usage; exit 0;;
    *) echo "unknown option $1" >&2; usage >&2; exit 2;;
  esac
done
command -v python3 >/dev/null || { echo "python3 is required" >&2; exit 1; }
command -v git >/dev/null || { echo "git is required" >&2; exit 1; }

if [[ -z "$AGENTS" ]]; then
  if [[ -n "$DIR" ]]; then AGENTS=agents
  else
    for pair in claude:claude codex:codex cursor-agent:cursor gemini:gemini; do
      command -v "${pair%%:*}" >/dev/null && AGENTS="${AGENTS:+$AGENTS,}${pair##*:}"
    done
    AGENTS="${AGENTS:-agents}"
    echo "Installing for: ${AGENTS//,/, } (use --agent to choose)"
  fi
fi

# Source: this checkout when run from one, otherwise only the plugin, from a GitHub archive.
SELF="${BASH_SOURCE[0]:-}"
if [[ -n "$SELF" && -f "$(dirname "$SELF")/plugins/review-loop/.claude-plugin/plugin.json" ]]; then
  SRC="$(cd "$(dirname "$SELF")" && pwd)"; ORIGIN="$SRC"; LOCAL=1
else
  command -v curl >/dev/null || { echo "curl is required" >&2; exit 1; }
  TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
  curl -fsSL "$TARBALL" | tar -xzf - -C "$TMP"
  SRC="$(dirname "$(dirname "$(ls -d "$TMP"/*/plugins/review-loop)")")"; ORIGIN="$REPO@$REF"; LOCAL=0
fi
PLUGIN="$SRC/plugins/review-loop"

install_claude() {
  command -v claude >/dev/null || { echo "claude is not on PATH" >&2; return 1; }
  local scope=user source="$REPO"
  [[ -n "$PROJECT" ]] && scope=project
  [[ $LOCAL == 1 ]] && source="$SRC"
  (
    cd "${PROJECT:-.}"
    if claude plugin marketplace list --json 2>/dev/null | grep -q '"name": *"meko-review-loop"'; then
      claude plugin marketplace update meko-review-loop >/dev/null
    elif [[ $LOCAL == 1 ]]; then
      claude plugin marketplace add "$source" --scope "$scope" >/dev/null
    else
      claude plugin marketplace add "$source" --scope "$scope" --sparse .claude-plugin plugins >/dev/null
    fi
    claude plugin install review-loop@meko-review-loop --scope "$scope" >/dev/null
  )
  echo "Claude Code: plugin installed ($scope scope). In a new session, run /review-loop:setup."
  if claude plugin list 2>/dev/null | grep -q 'review-loop@review-loop'; then
    echo "  An older install, review-loop@review-loop, is also present. Remove it with:"
    echo "    claude plugin uninstall review-loop@review-loop && claude plugin marketplace remove review-loop"
  fi
}

copy_skill() {  # destination dir, source skill name, installed name
  local from="$PLUGIN/skills/$2" to="$1/$3"
  rm -rf "$to"; cp -R "$from" "$to"
  find "$to" -name '__pycache__' -prune -exec rm -rf {} +
  # Concrete paths instead of Claude Code's variables; namespaced names and cross-references.
  find "$to" -name '*.md' -print0 | while IFS= read -r -d '' f; do
    python3 - "$f" "$to" "$3" <<'PY'
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

install_skills() {  # agent name, optional explicit directory
  local agent="$1" dir="${2:-}" sub
  if [[ -z "$dir" ]]; then
    case "$agent" in
      agents) sub=.agents/skills;; codex) sub=.codex/skills;; cursor) sub=.cursor/skills;; gemini) sub=.gemini/skills;;
      *) echo "unknown agent '$agent'; use --dir SKILLS_DIR for other agents" >&2; return 2;;
    esac
    dir="${PROJECT:-$HOME}/$sub"
  fi
  mkdir -p "$dir"; dir="$(cd "$dir" && pwd)"
  copy_skill "$dir" review-loop review-loop
  copy_skill "$dir" setup review-loop-setup
  copy_skill "$dir" respond review-loop-respond
  copy_skill "$dir" exit review-loop-exit
  # Reviewer and verifier instructions, used by headless-review.sh and by agents with subagents.
  mkdir -p "$dir/review-loop/agents"; cp "$PLUGIN"/agents/*.md "$dir/review-loop/agents/"
  echo "$ORIGIN" > "$dir/review-loop/.installed-from"
  chmod +x "$dir/review-loop/scripts/"*.py "$dir/review-loop/scripts/"*.sh "$dir/review-loop/scripts/stores/"*.py
  echo "$agent: skills installed in $dir. Ask your agent to use the review-loop-setup skill."
}

IFS=, read -r -a list <<< "$AGENTS"
for agent in "${list[@]}"; do
  if [[ "$agent" == claude ]]; then install_claude
  else install_skills "$agent" "$DIR"
  fi
done
echo "Next: open your agent inside a repository and run the setup skill."
