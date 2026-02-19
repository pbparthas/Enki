#!/bin/bash
# hooks/session-start.sh — Initialize Uru + Abzu, inject full context
set -euo pipefail

INPUT=$(cat)
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // empty')
PROJECT=$(echo "$INPUT" | jq -r '.project // empty')
GOAL=$(echo "$INPUT" | jq -r '.goal // empty')
TIER=$(echo "$INPUT" | jq -r '.tier // empty')

if [[ -z "$SESSION_ID" ]]; then
    SESSION_ID=$(/home/partha/.enki-venv/bin/python -c "import uuid; print(uuid.uuid4())")
fi
if [[ -z "$PROJECT" && -f "$HOME/.enki/PROJECT" ]]; then
    PROJECT=$(cat "$HOME/.enki/PROJECT")
fi
if [[ -z "$GOAL" && -f "$HOME/.enki/GOAL" ]]; then
    GOAL=$(cat "$HOME/.enki/GOAL")
fi
if [[ -z "$TIER" && -f "$HOME/.enki/TIER" ]]; then
    TIER=$(cat "$HOME/.enki/TIER")
fi
if [[ -z "$TIER" ]]; then
    TIER="standard"
fi

# Initialize enforcement state (Uru)
echo "$INPUT" | /home/partha/.enki-venv/bin/python -m enki.gates.uru --hook session-start 2>/dev/null || true

# Inject combined context: Uru enforcement + Abzu memory
CONTEXT=$(/home/partha/.enki-venv/bin/python -c "
import sys
sys.path.insert(0, 'src')

parts = []

# Uru enforcement context
try:
    from enki.gates.uru import inject_enforcement_context
    uru_ctx = inject_enforcement_context()
    if uru_ctx:
        parts.append(uru_ctx)
except Exception:
    parts.append('Uru: Enforcement context unavailable.')

# Abzu memory context (persona + beads + last summary)
try:
    from enki.memory.abzu import inject_session_start
    project = '$PROJECT' or None
    goal = '$GOAL' or None
    tier = '$TIER' or 'standard'
    abzu_ctx = inject_session_start(project, goal, tier)
    if abzu_ctx:
        parts.append(abzu_ctx)
except Exception:
    pass

print('\n'.join(parts))
" 2>/dev/null || echo "Uru: Enforcement context unavailable.")

echo "$CONTEXT"
