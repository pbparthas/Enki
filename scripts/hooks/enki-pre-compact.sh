#!/bin/bash
# hooks/pre-compact.sh — Save enforcement state + Abzu pre-compact summary
set -euo pipefail

INPUT=$(cat)

# Extract fields from stdin JSON
TRANSCRIPT_PATH=$(echo "$INPUT" | jq -r '.transcript_path // empty' 2>/dev/null || echo "")
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // empty' 2>/dev/null || echo "")
CWD=$(echo "$INPUT" | jq -r '.cwd // empty' 2>/dev/null || echo "")

# Uru: Log enforcement state snapshot
/home/partha/.enki-venv/bin/python -c "
import sys
sys.path.insert(0, 'src')
from enki.gates.uru import _get_session_id, _log_enforcement
session_id = _get_session_id()
_log_enforcement('pre-compact', 'system', None, None, 'snapshot', 'Pre-compact state capture')
" 2>/dev/null || true

# Abzu: Save pre-compact summary with real JSONL extraction
/home/partha/.enki-venv/bin/python -c "
import sys
sys.path.insert(0, 'src')
try:
    from enki.memory.abzu import update_pre_compact_summary
    from enki.gates.uru import _get_session_id
    session_id = '${SESSION_ID}' or _get_session_id()
    transcript_path = '${TRANSCRIPT_PATH}'
    if session_id:
        if transcript_path:
            update_pre_compact_summary(
                session_id=session_id,
                project='${CWD}' or None,
                transcript_path=transcript_path,
            )
        else:
            update_pre_compact_summary(
                session_id=session_id,
                project='${CWD}' or None,
                operational_state='Pre-compact checkpoint (no transcript)',
                conversational_state='Session in progress',
            )
except Exception:
    pass
" 2>/dev/null || true

echo '{"decision":"allow"}'
