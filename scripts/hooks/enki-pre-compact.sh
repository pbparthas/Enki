#!/bin/bash
# Enki Pre-Compact Hook
# Called before context compaction in Claude Code
#
# Saves session state so it can be restored after compaction

# Read input from stdin
INPUT=$(cat)

CWD=$(echo "$INPUT" | jq -r '.cwd // "."')
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // ""')

# Check if enki CLI is available
if ! command -v enki &> /dev/null; then
    exit 0
fi

# Save pre-compact snapshot
ENKI_DIR="$CWD/.enki"
mkdir -p "$ENKI_DIR"

# Record compaction event
TIMESTAMP=$(date +%Y-%m-%d\ %H:%M:%S)
echo "[$TIMESTAMP] COMPACT: Context compaction triggered" >> "$ENKI_DIR/RUNNING.md"

# Save current session state for restoration
{
    echo "SESSION_ID=$SESSION_ID"
    echo "PHASE=$(cat "$ENKI_DIR/PHASE" 2>/dev/null || echo 'intake')"
    echo "GOAL=$(cat "$ENKI_DIR/GOAL" 2>/dev/null || echo '')"
    echo "TIER=$(cat "$ENKI_DIR/TIER" 2>/dev/null || echo 'unknown')"
    echo "TIMESTAMP=$TIMESTAMP"
} > "$ENKI_DIR/.pre-compact-state"

# Pre-compact hooks don't output anything
exit 0
