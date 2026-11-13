#!/usr/bin/env bash
# Example external store for the review-loop author guide: a private git repository with one file
# per repository and one commit per approved version. Copy it somewhere on your PATH and add to
# ~/.review-loop/settings.json:
#   {"guide_store": "auto",
#    "guide_pull_cmd": "git-store.sh pull",
#    "guide_push_cmd": "git-store.sh push"}
# rl.py sets RL_REPO_SLUG for both; push also gets RL_GUIDE_FILE, RL_HISTORY_FILE (may be empty)
# and RL_MARKER. Pull prints the guide (nothing if there is none yet); push prints a version id.
# Give the store a remote (`git -C <dir> remote add origin ...`) to sync it between machines.
set -euo pipefail
STORE="${GUIDE_STORE_DIR:-$HOME/.review-loop-guides}"
name="${RL_REPO_SLUG//\//__}"
case "${1:-}" in
  pull)
    if [[ -d "$STORE/.git" ]] && git -C "$STORE" remote | grep -q .; then
      git -C "$STORE" pull -q --ff-only >/dev/null 2>&1 || true
    fi
    cat "$STORE/$name.md" 2>/dev/null || true
    ;;
  push)
    [[ -d "$STORE/.git" ]] || git init -q "$STORE"
    cp "$RL_GUIDE_FILE" "$STORE/$name.md"
    if [[ -n "${RL_HISTORY_FILE:-}" ]]; then
      { printf '\n## %s\n\n' "${RL_MARKER:-}"; cat "$RL_HISTORY_FILE"; } >> "$STORE/$name.history.md"
    fi
    git -C "$STORE" add -A
    git -C "$STORE" commit -q -m "guide: $RL_REPO_SLUG ${RL_MARKER:-}" >/dev/null || true
    if git -C "$STORE" remote | grep -q .; then git -C "$STORE" push -q >/dev/null 2>&1 || true; fi
    git -C "$STORE" rev-parse --short HEAD
    ;;
  *)
    echo "usage: $0 pull|push" >&2
    exit 2
    ;;
esac
