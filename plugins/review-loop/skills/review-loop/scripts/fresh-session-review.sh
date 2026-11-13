#!/usr/bin/env bash
# Compatibility wrapper: the gate review, now run by headless-review.sh with any agent CLI.
# usage: fresh-session-review.sh [--model M] [--name gate] [--agent AGENT] [--budget-turns N] [--allow-mcp]
exec "$(cd "$(dirname "$0")" && pwd)/headless-review.sh" --mode gate "$@"
