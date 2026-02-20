"""Hook version checks and deployment utilities."""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path

EXPECTED_HOOK_VERSIONS = {
    "enki-session-start.sh": "v4.0.1",
    "enki-pre-tool-use.sh": "v4.0.1",
    "enki-post-tool-use.sh": "v4.0.1",
    "enki-pre-compact.sh": "v4.0.1",
    "enki-session-end.sh": "v4.0.1",
}

VERSION_RE = re.compile(r"HOOK_VERSION\s*=\s*([A-Za-z0-9._-]+)")


@dataclass
class HookVersionResult:
    all_current: bool
    mismatches: list[dict]
    missing: list[str]


def _read_hook_version(hook_path: Path) -> str | None:
    """Read version marker from hook file (expected on line 2, fallback full scan)."""
    try:
        lines = hook_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None

    if len(lines) >= 2:
        match = VERSION_RE.search(lines[1])
        if match:
            return match.group(1)

    for line in lines:
        match = VERSION_RE.search(line)
        if match:
            return match.group(1)
    return None


def check_hook_versions(hooks_dir: str = "~/.claude/hooks/") -> HookVersionResult:
    """Compare deployed hook versions against expected versions."""
    root = Path(hooks_dir).expanduser()
    mismatches: list[dict] = []
    missing: list[str] = []

    for hook, expected in EXPECTED_HOOK_VERSIONS.items():
        hook_path = root / hook
        if not hook_path.exists():
            missing.append(hook)
            continue

        deployed = _read_hook_version(hook_path)
        if deployed != expected:
            mismatches.append(
                {
                    "hook": hook,
                    "deployed_version": deployed,
                    "expected_version": expected,
                }
            )

    return HookVersionResult(
        all_current=(not mismatches and not missing),
        mismatches=mismatches,
        missing=missing,
    )


def format_hook_warning(result: HookVersionResult) -> str:
    """Build warning text for stale/missing deployed hooks."""
    stale = [m["hook"] for m in result.mismatches]
    items = stale + result.missing
    if not items:
        return ""
    names = ", ".join(sorted(items))
    return f"Hooks outdated: {names}. Run `enki hooks deploy` to update."


def deploy_hooks(source_dir: str, target_dir: str = "~/.claude/hooks/") -> list[str]:
    """Copy expected hooks from source to target, preserving permissions."""
    src_root = Path(source_dir).expanduser()
    dst_root = Path(target_dir).expanduser()
    dst_root.mkdir(parents=True, exist_ok=True)

    deployed: list[str] = []
    for hook in EXPECTED_HOOK_VERSIONS:
        src = src_root / hook
        if not src.exists():
            continue
        dst = dst_root / hook
        shutil.copy2(src, dst)
        deployed.append(hook)
    return deployed
