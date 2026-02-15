#!/bin/bash
# hooks/pre-compact.sh — Save enforcement state + Abzu pre-compact summary
set -euo pipefail

INPUT=$(cat)

# Uru: Log enforcement state snapshot
python -c "
import sys
sys.path.insert(0, 'src')
from enki.gates.uru import _get_session_id, _log_enforcement
session_id = _get_session_id()
_log_enforcement('pre-compact', 'system', None, None, 'snapshot', 'Pre-compact state capture')
" 2>/dev/null || true

# Abzu: Save pre-compact summary for injection after compaction
python -c "
import sys
sys.path.insert(0, 'src')
try:
    from enki.memory.abzu import update_pre_compact_summary
    from enki.gates.uru import _get_session_id
    session_id = _get_session_id()
    if session_id:
        update_pre_compact_summary(
            session_id=session_id,
            project=None,
            operational_state='Pre-compact checkpoint',
            conversational_state='Session in progress',
        )
except Exception:
    pass
" 2>/dev/null || true

echo '{"decision":"allow"}'
