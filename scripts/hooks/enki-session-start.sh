#!/bin/bash
# Enki Session Start Hook
# Called when Claude Code session starts
#
# Initializes session state and injects relevant context

# Read input from stdin
INPUT=$(cat)

# Extract prompt and cwd
PROMPT=$(echo "$INPUT" | jq -r '.prompt // ""')
CWD=$(echo "$INPUT" | jq -r '.cwd // "."')

# Check if enki CLI is available
if ! command -v enki &> /dev/null; then
    exit 0
fi

# Initialize session
enki session start --project "$CWD" 2>/dev/null || true

# Session start hooks should not output JSON
# Context injection happens via other mechanisms
exit 0
