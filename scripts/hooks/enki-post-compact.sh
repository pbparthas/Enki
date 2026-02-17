#!/bin/bash
# hooks/post-compact.sh — Re-inject Uru + Abzu context after compaction
set -euo pipefail

INPUT=$(cat)

# Extract session_id from stdin JSON
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // empty' 2>/dev/null || echo "")

# Re-inject combined context with full persona + state + history
CONTEXT=$(/home/partha/.enki-venv/bin/python -c "
import sys
sys.path.insert(0, 'src')

session_id = '${SESSION_ID}'

# Try full injection first
try:
    if not session_id:
        from enki.gates.uru import _get_session_id
        session_id = _get_session_id()

    from enki.memory.abzu import inject_post_compact
    result = inject_post_compact(session_id=session_id, tier='standard')
    if result:
        print(result)
    else:
        # Minimal fallback: just enforcement state
        from enki.gates.uru import inject_enforcement_context
        print(inject_enforcement_context())
except Exception:
    # Last resort fallback
    try:
        from enki.gates.uru import inject_enforcement_context
        print(inject_enforcement_context())
    except Exception:
        print('Enki: Context unavailable after compaction. Continue with current task.')
" 2>/dev/null || echo "Enki: Context unavailable after compaction. Continue with current task.")

echo "$CONTEXT"
