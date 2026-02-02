#!/bin/bash
# Enki Post-Tool-Use Hook
# Called after each tool execution in Claude Code
#
# Tracks edits and detects tier escalation

# Read input from stdin
INPUT=$(cat)

# Extract tool name and file path
TOOL=$(echo "$INPUT" | jq -r '.tool_name // ""')
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // ""')
CWD=$(echo "$INPUT" | jq -r '.cwd // "."')

# Only process Edit/Write tools
if [[ ! "$TOOL" =~ ^(Edit|Write|MultiEdit)$ ]]; then
    exit 0
fi

# Skip if no file path
if [[ -z "$FILE_PATH" ]]; then
    exit 0
fi

# Check if enki CLI is available
if ! command -v enki &> /dev/null; then
    exit 0
fi

# Track the edit and check for escalation
enki session track-edit \
    --file "$FILE_PATH" \
    --project "$CWD" 2>/dev/null || true

# Post-hooks don't output anything
exit 0
