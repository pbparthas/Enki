#!/bin/bash
# Enki Post-Compact Hook
# Called after context compaction in Claude Code
#
# Injects Enki context back into the conversation

# Read input from stdin
INPUT=$(cat)

CWD=$(echo "$INPUT" | jq -r '.cwd // "."')

ENKI_DIR="$CWD/.enki"

# Check if enki directory exists
if [[ ! -d "$ENKI_DIR" ]]; then
    exit 0
fi

# Check if enki CLI is available
if ! command -v enki &> /dev/null; then
    echo "## Enki Context (Post-Compaction)"
    echo ""
    echo "Enki CLI not available. Session state may be incomplete."
    exit 0
fi

# Build context injection
echo "## Enki Context Restored (Post-Compaction)"
echo ""

# Current session state
PHASE=$(cat "$ENKI_DIR/PHASE" 2>/dev/null || echo 'intake')
GOAL=$(cat "$ENKI_DIR/GOAL" 2>/dev/null || echo '')
TIER=$(cat "$ENKI_DIR/TIER" 2>/dev/null || echo 'unknown')

echo "**Phase**: $PHASE"
echo "**Tier**: $TIER"
if [[ -n "$GOAL" ]]; then
    echo "**Goal**: $GOAL"
else
    echo "**Goal**: (not set - use enki_goal to set)"
fi
echo ""

# Active orchestration status
ORCH_STATUS=$(enki orchestration --json 2>/dev/null)
if [[ -n "$ORCH_STATUS" ]] && [[ $(echo "$ORCH_STATUS" | jq -r '.active // false') == "true" ]]; then
    SPEC=$(echo "$ORCH_STATUS" | jq -r '.spec // "unknown"')
    PROGRESS=$(echo "$ORCH_STATUS" | jq -r '.tasks.progress // 0')
    PROGRESS_PCT=$(echo "$PROGRESS * 100" | bc 2>/dev/null || echo "0")

    echo "### Active Orchestration"
    echo "- Spec: $SPEC"
    echo "- Progress: ${PROGRESS_PCT%.*}%"

    NEXT=$(enki next --json 2>/dev/null | jq -r '.message // ""')
    if [[ -n "$NEXT" ]]; then
        echo "- Next: $NEXT"
    fi
    echo ""
fi

# Recent activity from RUNNING.md (last 10 lines)
if [[ -f "$ENKI_DIR/RUNNING.md" ]]; then
    echo "### Recent Activity"
    echo '```'
    tail -10 "$ENKI_DIR/RUNNING.md" 2>/dev/null | grep -v "^$" | head -8
    echo '```'
    echo ""
fi

# Relevant beads for current context
if [[ -n "$GOAL" ]]; then
    RELEVANT=$(enki recall "$GOAL" --limit 3 2>/dev/null | head -20)
    if [[ -n "$RELEVANT" ]] && [[ "$RELEVANT" != "No results found." ]]; then
        echo "### Relevant Knowledge"
        echo "$RELEVANT" | head -15
        echo ""
    fi
fi

# Enforcement gates reminder
echo "### Enforcement Gates (Active)"
echo "- Gate 1: Goal required before editing code"
echo "- Gate 2: Spec required before spawning agents"
echo ""
echo "---"
echo "*Context restored from .enki/ files. Continue where you left off.*"
