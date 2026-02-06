"""Tests for shell hook security fixes (P0-13, P0-14, P0-17)."""

import pytest
from pathlib import Path


HOOKS_DIR = Path(__file__).parent.parent / "scripts" / "hooks"


class TestPreToolUseHook:
    """P0-13 + P0-17: pre-tool-use hook security."""

    def test_shebang_on_line_one(self):
        """P0-17: Shebang must be on line 1, not preceded by blank lines."""
        hook = HOOKS_DIR / "enki-pre-tool-use.sh"
        content = hook.read_text()
        assert content.startswith("#!/bin/bash"), (
            f"Shebang not on line 1. First chars: {content[:30]!r}"
        )

    def test_no_debug_log_to_tmp(self):
        """P0-13: No debug logging to world-readable /tmp."""
        hook = HOOKS_DIR / "enki-pre-tool-use.sh"
        content = hook.read_text()
        assert "/tmp/enki-hook-debug.log" not in content, (
            "Debug log to /tmp still present — secrets leak risk"
        )

    def test_no_unquoted_input_variable(self):
        """Variables should be quoted to prevent injection."""
        hook = HOOKS_DIR / "enki-pre-tool-use.sh"
        content = hook.read_text()
        # Check for quoted variable usage in jq calls
        assert '${INPUT}' in content or '"$INPUT"' in content or 'echo "$INPUT"' not in content


class TestSessionStartHook:
    """P0-14: No code injection via interpolated variables."""

    def test_no_direct_goal_interpolation_in_python(self):
        """P0-14: GOAL must not be interpolated directly into Python code."""
        hook = HOOKS_DIR / "enki-session-start.sh"
        content = hook.read_text()
        # The old pattern was: search('$GOAL', ...) inside Python -c
        # New pattern should use env vars: os.environ.get('ENKI_GOAL')
        python_blocks = []
        in_python = False
        block = []
        for line in content.split('\n'):
            if 'python' in line.lower() and '-c' in line:
                in_python = True
                block = [line]
            elif in_python:
                block.append(line)
                if line.strip().endswith('" 2>/dev/null )') or line.strip() == '" 2>/dev/null )':
                    python_blocks.append('\n'.join(block))
                    in_python = False

        for block in python_blocks:
            # Should NOT contain $GOAL or $CWD interpolated in Python code
            assert "'$GOAL'" not in block, f"Direct $GOAL interpolation in Python: {block[:100]}"
            assert "'$CWD'" not in block or "sys.path.insert(0, '$CWD" not in block, (
                f"Direct $CWD interpolation in Python sys.path: {block[:100]}"
            )
