#!/bin/bash
# Called by settings.json PostToolUse hook after Edit/Write
FILE="$1"
if [[ "$FILE" == *.py ]]; then
  ruff check --fix "$FILE" 2>/dev/null || true
fi
