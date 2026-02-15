#!/bin/bash
# hooks/post-compact.sh — Re-inject Uru + Abzu context after compaction
set -euo pipefail

INPUT=$(cat)

# Re-inject combined context
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
    parts.append('Uru: Enforcement context unavailable after compaction.')

# Abzu post-compact injection (with budget)
try:
    from enki.memory.abzu import inject_post_compact
    from enki.gates.uru import _get_session_id
    session_id = _get_session_id()
    if session_id:
        abzu_ctx = inject_post_compact(session_id=session_id, tier='standard')
        if abzu_ctx:
            parts.append(abzu_ctx)
except Exception:
    pass

print('\n'.join(parts))
" 2>/dev/null || echo "Uru: Enforcement context unavailable after compaction.")

echo "$CONTEXT"
