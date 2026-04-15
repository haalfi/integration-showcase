#!/bin/bash
# PreToolUse gate: block pushes to main + run typecheck (skipped for docs-only)

BRANCH=$(git branch --show-current 2>/dev/null)
if [ "$BRANCH" = "main" ]; then
  echo "Blocked: never push directly to main." >&2
  exit 2
fi

# Skip typecheck when no code files changed vs base
if ! git diff origin/main...HEAD --name-only 2>/dev/null | grep -qE '^(src/|tests/|scenarios/)'; then
  exit 0
fi

OUTPUT=$(hatch run typecheck 2>&1)
if [ $? -ne 0 ]; then
  echo "Blocked: typecheck failed. Fix before pushing:" >&2
  echo "$OUTPUT" >&2
  exit 2
fi
