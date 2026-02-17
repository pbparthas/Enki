#!/bin/bash
# hooks/pre-tool-use.sh — Most critical hook
# Layer 0 (bash fast-path) → Layer 0.5 → Layer 1 (Python)
set -euo pipefail

INPUT=$(cat)
TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty')

# ── Layer 0: Bash fast-path for protected files ──
if [[ "$TOOL_NAME" == "Write" || "$TOOL_NAME" == "Edit" || \
      "$TOOL_NAME" == "MultiEdit" || "$TOOL_NAME" == "NotebookEdit" ]]; then

    TARGET=$(echo "$INPUT" | jq -r '.tool_input.file_path // .tool_input.path // empty')
    BASENAME=$(basename "$TARGET" 2>/dev/null || echo "")

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
    RESOLVED=$(realpath "$TARGET" 2>/dev/null || echo "$TARGET")
    if [[ "$RESOLVED" == "$HOME/.enki/hooks/"* || "$RESOLVED" == "$HOME/.enki/prompts/"* ]]; then
        echo '{"decision":"block","reason":"Layer 0: Protected directory"}'
        exit 0
    fi
fi

if [[ "$TOOL_NAME" == "Bash" || "$TOOL_NAME" == "Task" ]]; then
    CMD=$(echo "$INPUT" | jq -r '.tool_input.command // empty')

    # Layer 0.5: sqlite3 targeting .db files
    if echo "$CMD" | grep -qP 'sqlite3\s+\S*\.db'; then
        echo '{"decision":"block","reason":"Layer 0.5: Direct DB manipulation. Use Enki tools."}'
        exit 0
    fi
    if echo "$CMD" | grep -qP 'sqlite3\.connect'; then
        echo '{"decision":"block","reason":"Layer 0.5: Direct DB manipulation. Use Enki tools."}'
        exit 0
    fi
fi

# ── Layer 1+: Python handles the rest ──
RESULT=$(echo "$INPUT" | /home/partha/.enki-venv/bin/python -m enki.gates.uru --hook pre-tool-use 2>/dev/null)

if [[ -n "$RESULT" ]]; then
    echo "$RESULT"
else
    # Fail closed for mutations, open for reads
    if [[ "$TOOL_NAME" == "Write" || "$TOOL_NAME" == "Edit" || \
          "$TOOL_NAME" == "MultiEdit" || "$TOOL_NAME" == "NotebookEdit" || \
          "$TOOL_NAME" == "Bash" || "$TOOL_NAME" == "Task" ]]; then
        echo '{"decision":"block","reason":"Uru unavailable. Blocking mutation for safety."}'
    else
        echo '{"decision":"allow"}'
    fi
fi
