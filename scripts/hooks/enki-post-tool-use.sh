#!/bin/bash
# hooks/post-tool-use.sh — Nudges + logging (non-blocking)
set -euo pipefail

INPUT=$(cat)

RESULT=$(echo "$INPUT" | /home/partha/.enki-venv/bin/python -m enki.gates.uru --hook post-tool-use 2>/dev/null)

if [[ -n "$RESULT" ]]; then
    echo "$RESULT"
else
    echo '{"decision":"allow"}'
fi
