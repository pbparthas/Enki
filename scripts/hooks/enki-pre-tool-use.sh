#!/bin/bash
# Enki Pre-Tool-Use Hook
# Called before each tool execution in Claude Code
#
# Input: JSON via stdin with tool_name and tool_input
# Output: JSON with decision (allow/block) and optional reason

# Read input from stdin
INPUT=$(cat)

# Extract tool name and file path
TOOL=$(echo "$INPUT" | jq -r '.tool_name // ""')
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // ""')
AGENT_TYPE=$(echo "$INPUT" | jq -r '.tool_input.subagent_type // ""')
CWD=$(echo "$INPUT" | jq -r '.cwd // "."')

# Skip if no tool name
if [[ -z "$TOOL" ]]; then
    echo '{"decision": "allow"}'
    exit 0
fi

# Check if enki CLI is available
if ! command -v enki &> /dev/null; then
    # Fallback: allow if enki not installed
    echo '{"decision": "allow"}'
    exit 0
fi

# Run enki gate check
RESULT=$(enki gate check \
    --tool "$TOOL" \
    --file "$FILE_PATH" \
    --agent "$AGENT_TYPE" \
    --project "$CWD" \
    --json 2>/dev/null)

if [[ $? -eq 0 ]] && [[ -n "$RESULT" ]]; then
    echo "$RESULT"
else
    # Fallback: allow on error
    echo '{"decision": "allow"}'
fi
