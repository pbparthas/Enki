#!/bin/bash
# HOOK_VERSION=v4.0.1
LOG="$HOME/.enki/hook-errors.log"
mkdir -p "$(dirname "$LOG")" 2>/dev/null || true
if ! (echo "" >> "$LOG") 2>/dev/null; then
    LOG="/tmp/enki-hook-errors.log"
    (echo "" >> "$LOG") 2>/dev/null || true
fi
# hooks/pre-tool-use.sh — Most critical hook
# Layer 0 (bash fast-path) → Layer 0.5 → Layer 1 (Python)
set -euo pipefail

INPUT=$(cat)
TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty')

# ── Layer 0: Bash fast-path for protected files ──
if [[ "$TOOL_NAME" == "Write" || "$TOOL_NAME" == "Edit" || \
      "$TOOL_NAME" == "MultiEdit" || "$TOOL_NAME" == "NotebookEdit" ]]; then

    TARGET=$(echo "$INPUT" | jq -r '.tool_input.file_path // .tool_input.path // empty')
    BASENAME=$(basename "$TARGET" 2>>"$LOG" || echo "")

    case "$BASENAME" in
        session-start.sh|pre-tool-use.sh|post-tool-use.sh|\
        pre-compact.sh|post-compact.sh|session-end.sh|\
        uru.py|layer0.py|PERSONA.md|\
        _base.md|_coding_standards.md|\
        pm.md|architect.md|dba.md|dev.md|qa.md|\
        ui_ux.md|validator.md|reviewer.md|infosec.md|\
        devops.md|performance.md|researcher.md|em.md)
            echo '{"decision":"block","reason":"Layer 0: Protected file '"$BASENAME"'"}'
            exit 0
            ;;
    esac

    # Check if target is under ~/.enki/hooks/ or ~/.enki/prompts/
    RESOLVED=$(realpath "$TARGET" 2>>"$LOG" || echo "$TARGET")
    if [[ "$RESOLVED" == "$HOME/.enki/hooks/"* || "$RESOLVED" == "$HOME/.enki/prompts/"* ]]; then
        echo '{"decision":"block","reason":"Layer 0: Protected directory"}'
        exit 0
    fi
fi

if [[ "$TOOL_NAME" == "Bash" || "$TOOL_NAME" == "Task" ]]; then
    # Layer 0.5 DB checks are handled in Python gate logic (token-aware parsing).
    # Do not run substring checks here; they produce false positives for test code.
    :
fi

# ── Layer 1+: Python handles the rest ──
echo "$(date -Iseconds) [enki-pre-tool-use] tool=$TOOL_NAME" >> "$LOG" 2>/dev/null || true
RESULT=$(echo "$INPUT" | /home/partha/.enki-venv/bin/python -m enki.gates.uru --hook pre-tool-use 2>>"$LOG" || true)

if [[ -n "$RESULT" ]]; then
    DECISION=$(echo "$RESULT" | jq -r '.decision // empty' 2>>"$LOG" || true)
    if [[ "$DECISION" == "block" ]]; then
        REASON=$(echo "$RESULT" | jq -r '.reason // "Blocked by Enki gate."' 2>>"$LOG" || true)
        jq -n --arg reason "$REASON" '{
          hookSpecificOutput: {
            hookEventName: "PreToolUse",
            permissionDecision: "deny",
            permissionDecisionReason: $reason
          }
        }'
    fi
else
    echo "$(date -Iseconds) [enki-pre-tool-use] EMPTY RESULT tool=$TOOL_NAME" >> "$LOG" 2>/dev/null || true
    # Fail closed for mutations, open for reads
    if [[ "$TOOL_NAME" == "Write" || "$TOOL_NAME" == "Edit" || \
          "$TOOL_NAME" == "MultiEdit" || "$TOOL_NAME" == "NotebookEdit" || \
          "$TOOL_NAME" == "Bash" || "$TOOL_NAME" == "Task" ]]; then
        jq -n --arg reason "Uru unavailable. Blocking mutation for safety." '{
          hookSpecificOutput: {
            hookEventName: "PreToolUse",
            permissionDecision: "deny",
            permissionDecisionReason: $reason
          }
        }'
    fi
fi
