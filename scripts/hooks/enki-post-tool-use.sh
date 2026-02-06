#!/bin/bash
set -euo pipefail
# Enki Post-Tool-Use Hook
# Called after each tool execution in Claude Code
#
# Tracks edits and detects tier escalation

# Read input from stdin
INPUT=$(cat)

# Extract tool name and file path (P1-06: all vars quoted)
TOOL=$(echo "${INPUT}" | jq -r '.tool_name // ""')
FILE_PATH=$(echo "${INPUT}" | jq -r '.tool_input.file_path // ""')
CWD=$(echo "${INPUT}" | jq -r '.cwd // "."')

# Only process Edit/Write tools
if [[ ! "${TOOL}" =~ ^(Edit|Write|MultiEdit)$ ]]; then
    exit 0
fi

# Skip if no file path
if [[ -z "${FILE_PATH}" ]]; then
    exit 0
fi

# P1-11: Discover enki binary dynamically
ENKI_BIN="${ENKI_BIN:-}"
if [[ -z "${ENKI_BIN}" ]]; then
    if [[ -x "${CWD}/.venv/bin/enki" ]]; then
        ENKI_BIN="${CWD}/.venv/bin/enki"
    elif command -v enki &> /dev/null; then
        ENKI_BIN="$(command -v enki)"
    fi
fi

if [[ -z "${ENKI_BIN}" ]] || [[ ! -x "${ENKI_BIN}" ]]; then
    exit 0
fi

# Track the edit and check for escalation
"${ENKI_BIN}" session track-edit \
    --file "${FILE_PATH}" \
    --project "${CWD}" 2>/dev/null || true

# Post-hooks don't output anything
exit 0
