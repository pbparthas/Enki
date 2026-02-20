"""Tests for hook versioning and deployment."""

from pathlib import Path

from enki.hook_versioning import (
    EXPECTED_HOOK_VERSIONS,
    HookVersionResult,
    check_hook_versions,
    deploy_hooks,
    format_hook_warning,
)


def _write_hook(path: Path, version: str | None, mode: int = 0o755) -> None:
    lines = ["#!/bin/bash"]
    if version is not None:
        lines.append(f"# HOOK_VERSION={version}")
    else:
        lines.append("# no version marker")
    lines.append("echo ok")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    path.chmod(mode)


def test_all_hooks_current(tmp_path):
    hooks_dir = tmp_path / "hooks"
    hooks_dir.mkdir()
    for hook, version in EXPECTED_HOOK_VERSIONS.items():
        _write_hook(hooks_dir / hook, version)

    result = check_hook_versions(str(hooks_dir))
    assert result.all_current is True
    assert result.mismatches == []
    assert result.missing == []


def test_detects_version_mismatch(tmp_path):
    hooks_dir = tmp_path / "hooks"
    hooks_dir.mkdir()
    for hook, version in EXPECTED_HOOK_VERSIONS.items():
        _write_hook(hooks_dir / hook, "v0.0.1" if hook.endswith("pre-tool-use.sh") else version)

    result = check_hook_versions(str(hooks_dir))
    assert result.all_current is False
    assert any(m["hook"] == "enki-pre-tool-use.sh" for m in result.mismatches)


def test_detects_missing_hook(tmp_path):
    hooks_dir = tmp_path / "hooks"
    hooks_dir.mkdir()
    for hook, version in EXPECTED_HOOK_VERSIONS.items():
        if hook == "enki-session-end.sh":
            continue
        _write_hook(hooks_dir / hook, version)

    result = check_hook_versions(str(hooks_dir))
    assert result.all_current is False
    assert "enki-session-end.sh" in result.missing


def test_handles_hook_without_version_line(tmp_path):
    hooks_dir = tmp_path / "hooks"
    hooks_dir.mkdir()
    for hook, version in EXPECTED_HOOK_VERSIONS.items():
        _write_hook(hooks_dir / hook, None if hook == "enki-session-start.sh" else version)

    result = check_hook_versions(str(hooks_dir))
    mismatch = [m for m in result.mismatches if m["hook"] == "enki-session-start.sh"]
    assert mismatch
    assert mismatch[0]["deployed_version"] is None


def test_deploy_copies_hooks(tmp_path):
    source = tmp_path / "src_hooks"
    target = tmp_path / "dst_hooks"
    source.mkdir()
    for hook, version in EXPECTED_HOOK_VERSIONS.items():
        _write_hook(source / hook, version)

    deployed = deploy_hooks(str(source), str(target))
    assert len(deployed) == len(EXPECTED_HOOK_VERSIONS)
    for hook in EXPECTED_HOOK_VERSIONS:
        assert (target / hook).exists()


def test_deploy_preserves_permissions(tmp_path):
    source = tmp_path / "src_hooks"
    target = tmp_path / "dst_hooks"
    source.mkdir()
    hook = "enki-session-start.sh"
    _write_hook(source / hook, EXPECTED_HOOK_VERSIONS[hook], mode=0o750)
    for name, version in EXPECTED_HOOK_VERSIONS.items():
        if name == hook:
            continue
        _write_hook(source / name, version)

    deploy_hooks(str(source), str(target))
    src_mode = (source / hook).stat().st_mode & 0o777
    dst_mode = (target / hook).stat().st_mode & 0o777
    assert dst_mode == src_mode


def test_warning_message_format():
    warning = format_hook_warning(
        HookVersionResult(
            all_current=False,
            mismatches=[{"hook": "enki-pre-tool-use.sh"}],
            missing=["enki-session-end.sh"],
        )
    )
    assert warning.startswith("Hooks outdated:")
    assert "enki-pre-tool-use.sh" in warning
    assert "Run `enki hooks deploy` to update." in warning
