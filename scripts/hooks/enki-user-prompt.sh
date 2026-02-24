#!/bin/bash
HOOK_VERSION="2026-02-10-v3"
LOG="$HOME/.enki/hook-errors.log"
mkdir -p "$(dirname "$LOG")" 2>/dev/null || true
if ! (echo "" >> "$LOG") 2>/dev/null; then
    LOG="/tmp/enki-hook-errors.log"
    (echo "" >> "$LOG") 2>/dev/null || true
fi
# Enki User Prompt Submit Hook
# Called when user sends a message in Claude Code
#
# Input: JSON via stdin with prompt and cwd
# Output: JSON with optional context injection
#
# Purpose:
# 1. Auto-search on error patterns in user message
# 2. Inject relevant beads from Enki's knowledge base

ENKI_BIN="/home/partha/Desktop/Enki/.venv/bin/enki"

# Read input from stdin
INPUT=$(cat)

# Extract prompt and cwd
TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty')
PROMPT=$(echo "$INPUT" | jq -r '.prompt // ""')
CWD=$(echo "$INPUT" | jq -r '.cwd // "."')

# Skip if no prompt
if [[ -z "$PROMPT" ]]; then
    exit 0
fi

# Check if enki CLI is available
if [[ ! -x "$ENKI_BIN" ]]; then
    # Enki CLI not available - allow without context injection
    exit 0
fi

# === Error Pattern Detection ===
ERROR_PATTERNS="error|Error|ERROR|exception|Exception|traceback|Traceback|failed|Failed|TypeError|SyntaxError|NameError|AttributeError|ImportError|KeyError|ValueError|RuntimeError"

HAS_ERROR=false
if echo "$PROMPT" | grep -qiE "$ERROR_PATTERNS"; then
    HAS_ERROR=true
fi

# === Search for Relevant Knowledge ===
if [[ "$HAS_ERROR" == "true" ]]; then
    # Search for solutions to similar errors (limit query length)
    QUERY=$(echo "$PROMPT" | head -c 200)
    echo "$(date -Iseconds) [enki-user-prompt] tool=$TOOL_NAME" >> "$LOG" 2>/dev/null || true
    SEARCH_RESULT=$("$ENKI_BIN" recall "$QUERY" --type solution --limit 3 --project "$CWD" 2>>"$LOG" || true)
    
    if [[ -n "$SEARCH_RESULT" ]] && [[ "$SEARCH_RESULT" != "No results found." ]]; then
        # Output context injection
        jq -n --arg message "## Enki: Relevant Solutions

$SEARCH_RESULT" '{"message": $message}'
        exit 0
    fi
fi

# No context to inject
exit 0
