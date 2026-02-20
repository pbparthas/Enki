"""Tests for Uru tool input inspection."""

from unittest.mock import patch

from enki.gates.uru import check_pre_tool_use, inspect_tool_input


def test_blocks_enforcement_file_edit():
    result = inspect_tool_input(
        "Edit",
        {"path": "src/enki/gates/uru.py", "content": "..."},
    )
    assert result.blocked is True


def test_blocks_hook_modification():
    result = inspect_tool_input(
        "Write",
        {"path": "/home/user/.claude/hooks/pre-tool-use.sh", "content": "..."},
    )
    assert result.blocked is True


def test_blocks_sanitization_file_edit():
    result = inspect_tool_input(
        "Edit",
        {"path": "src/enki/sanitization.py", "content": "..."},
    )
    assert result.blocked is True


def test_blocks_suspicious_bash_rm_hooks():
    result = inspect_tool_input(
        "Bash",
        {"command": "rm -rf ~/.claude/hooks/"},
    )
    assert result.blocked is True


def test_blocks_suspicious_bash_sed_uru():
    result = inspect_tool_input(
        "Bash",
        {"command": "sed -i 's/x/y/' src/enki/gates/uru.py"},
    )
    assert result.blocked is True


def test_allows_normal_file_edit():
    result = inspect_tool_input(
        "Edit",
        {"path": "src/app/main.py", "content": "..."},
    )
    assert result.blocked is False


def test_allows_normal_bash_command():
    result = inspect_tool_input(
        "Bash",
        {"command": "ls -la /tmp"},
    )
    assert result.blocked is False


def test_blocks_verification_file_edit():
    result = inspect_tool_input(
        "Edit",
        {"path": "src/enki/verification.py", "content": "..."},
    )
    assert result.blocked is True


def test_reasoning_and_tool_input_both_checked():
    result = check_pre_tool_use(
        "Edit",
        {"path": "src/app/main.py", "content": "..."},
        reasoning_text="Let's disable uru enforcement before this change.",
    )
    assert result["decision"] == "block"


def test_violation_logged_on_block():
    with patch("enki.gates.uru._log_enforcement") as mock_log:
        result = check_pre_tool_use(
            "Edit",
            {"path": "src/enki/gates/uru.py", "content": "..."},
        )
    assert result["decision"] == "block"
    assert mock_log.called
