#!/bin/bash
# hooks/session-start.sh — Initialize Uru + Abzu, inject full context
set -euo pipefail

INPUT=$(cat)
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // empty')

if [[ -z "$SESSION_ID" ]]; then
    SESSION_ID=$(python -c "import uuid; print(uuid.uuid4())")
fi

# Initialize enforcement state (Uru)
echo "$INPUT" | python -m enki.gates.uru --hook session-start 2>/dev/null || true

# Inject combined context: Uru enforcement + Abzu memory
CONTEXT=$(python -c "
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
    abzu_ctx = inject_session_start(
        session_id='$SESSION_ID',
        project=None,
        goal=None,
        tier='standard',
    )
    if abzu_ctx:
        parts.append(abzu_ctx)
except Exception:
    pass

print('\n'.join(parts))
" 2>/dev/null || echo "Uru: Enforcement context unavailable.")

echo "$CONTEXT"
