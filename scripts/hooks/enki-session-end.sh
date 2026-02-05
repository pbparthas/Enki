#!/bin/bash
# Enki Session End Hook
# Called when Claude Code session ends (Stop hook)
#
# Replaces the old pattern of:
#   enki summary     ← thin 4-line state dump
#   enki maintain    ← decay weights (usually 0)
#
# Now does:
#   enki session end ← reflect + feedback loop + archive + summary table
#
# This is the counterpart to enki-session-start.sh

ENKI_BIN="/home/partha/Desktop/Enki/.venv/bin/enki"

# Read input from stdin (Claude Code passes JSON with cwd, etc.)
INPUT=$(cat)

CWD=$(echo "$INPUT" | jq -r '.cwd // "."')

# Check if enki CLI is available
if [[ ! -x "$ENKI_BIN" ]]; then
    exit 0
fi

# Check if there's an active session
if [[ ! -f "$CWD/.enki/SESSION_ID" ]]; then
    exit 0
fi

# =============================================================================
# RUN SESSION END (reflect + feedback loop + archive + summary)
# =============================================================================

"$ENKI_BIN" session end --project "$CWD" 2>&1

# =============================================================================
# MAINTENANCE (decay weights — lightweight, still useful)
# =============================================================================

"$ENKI_BIN" maintain 2>/dev/null || true

exit 0
