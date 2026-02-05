#!/bin/bash
# Enki Pre-Compact Hook
# Called before context compaction in Claude Code
#
# Reads the JSONL transcript directly and produces a digest.
# No dependency on `enki` CLI being on PATH.
#
# The digest is deterministic: same transcript → same digest.
# It uses regex extraction only — no AI summarization.

# Read input from stdin
INPUT=$(cat)

CWD=$(echo "$INPUT" | jq -r '.cwd // "."')
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // ""')
TRANSCRIPT_PATH=$(echo "$INPUT" | jq -r '.transcript_path // ""')

ENKI_DIR="$CWD/.enki"
mkdir -p "$ENKI_DIR"

# Record compaction event
TIMESTAMP=$(date +%Y-%m-%d\ %H:%M:%S)
echo "[$TIMESTAMP] COMPACT: Context compaction triggered (session: $SESSION_ID)" >> "$ENKI_DIR/RUNNING.md"

# Save basic state (always — fallback if transcript extraction fails)
{
    echo "SESSION_ID=$SESSION_ID"
    echo "PHASE=$(cat "$ENKI_DIR/PHASE" 2>/dev/null || echo 'intake')"
    echo "GOAL=$(cat "$ENKI_DIR/GOAL" 2>/dev/null || echo '')"
    echo "TIER=$(cat "$ENKI_DIR/TIER" 2>/dev/null || echo 'unknown')"
    echo "TIMESTAMP=$TIMESTAMP"
} > "$ENKI_DIR/.pre-compact-state"

# =============================================================================
# TRANSCRIPT DIGEST
# =============================================================================
# transcript.py lives alongside this hook or in src/enki/
# It reads the .jsonl file and produces a fixed-format markdown digest
# using ONLY mechanical extraction (regex, counts, paths).

if [[ -n "$TRANSCRIPT_PATH" ]] && [[ -f "$TRANSCRIPT_PATH" ]]; then
    # Find transcript.py — check common locations
    SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
    TRANSCRIPT_PY=""

    for candidate in \
        "$SCRIPT_DIR/transcript.py" \
        "$CWD/src/enki/transcript.py" \
        "$CWD/scripts/hooks/transcript.py" \
        "$HOME/.claude/hooks/transcript.py"; do
        if [[ -f "$candidate" ]]; then
            TRANSCRIPT_PY="$candidate"
            break
        fi
    done

    if [[ -n "$TRANSCRIPT_PY" ]]; then
        # Find Python — try venv first, then system
        PYTHON=""
        for py_candidate in \
            "$CWD/.venv/bin/python" \
            "$CWD/.venv/bin/python3" \
            "$(which python3 2>/dev/null)" \
            "$(which python 2>/dev/null)"; do
            if [[ -x "$py_candidate" ]]; then
                PYTHON="$py_candidate"
                break
            fi
        done

        if [[ -n "$PYTHON" ]]; then
            # Generate digest and save to .enki/
            # transcript.py output is deterministic — no AI judgment involved
            DIGEST=$("$PYTHON" "$TRANSCRIPT_PY" "$TRANSCRIPT_PATH" "$ENKI_DIR" 2>/dev/null)

            if [[ -n "$DIGEST" ]] && [[ ${#DIGEST} -gt 50 ]]; then
                echo "$DIGEST" > "$ENKI_DIR/.compact-digest"
                echo "[$TIMESTAMP] COMPACT: Digest saved ($(echo "$DIGEST" | wc -c) bytes)" >> "$ENKI_DIR/RUNNING.md"
            else
                echo "[$TIMESTAMP] COMPACT: Digest extraction failed or empty" >> "$ENKI_DIR/RUNNING.md"
            fi
        else
            echo "[$TIMESTAMP] COMPACT: No Python found for transcript extraction" >> "$ENKI_DIR/RUNNING.md"
        fi
    else
        echo "[$TIMESTAMP] COMPACT: transcript.py not found" >> "$ENKI_DIR/RUNNING.md"
    fi
else
    echo "[$TIMESTAMP] COMPACT: No transcript_path provided or file missing" >> "$ENKI_DIR/RUNNING.md"
fi

# Pre-compact hooks don't output to stdout
exit 0
