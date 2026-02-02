#!/bin/bash
# Enki Session Start Hook
# Called when Claude Code session starts
#
# Initializes session state, injects relevant context,
# and shows Ereshkigal review reminder if overdue

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

# Check if Ereshkigal review is overdue (7+ days since last review)
REVIEW_STATUS=$(enki report status --json 2>/dev/null)
if [[ $? -eq 0 ]] && [[ -n "$REVIEW_STATUS" ]]; then
    IS_OVERDUE=$(echo "$REVIEW_STATUS" | jq -r '.is_overdue // false')

    if [[ "$IS_OVERDUE" == "true" ]]; then
        DAYS_SINCE=$(echo "$REVIEW_STATUS" | jq -r '.days_since_review // "unknown"')
        BLOCKED=$(echo "$REVIEW_STATUS" | jq -r '.blocked_count // 0')
        EVASIONS=$(echo "$REVIEW_STATUS" | jq -r '.evasion_count // 0')
        FPS=$(echo "$REVIEW_STATUS" | jq -r '.false_positive_count // 0')

        # Output reminder as stderr (won't affect hook JSON response)
        cat >&2 << REMINDER

┌─────────────────────────────────────────┐
│ 📋 Enki Weekly Review Due               │
│                                         │
│ Last review: $DAYS_SINCE days ago
│ Blocked: $BLOCKED | Evasions: $EVASIONS | FPs: $FPS
│                                         │
│ Run: enki report weekly                 │
└─────────────────────────────────────────┘

REMINDER
    fi
fi

# Session start hooks should not output JSON
# Context injection happens via other mechanisms
exit 0
