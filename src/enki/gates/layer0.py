"""layer0.py — Layer 0 blocklist + Layer 0.5 DB protection + target extraction.

Layer 0: Pure filename blocklist. Protected files CC cannot edit. Period.
Layer 0.5: DB protection. CC cannot use sqlite3 binary or Python sqlite3 module
           to directly manipulate Enki databases.

Target extraction: Extracts WRITE TARGETS from bash commands. Does NOT match
against the entire command string. This prevents false positives like blocking:
    echo "Fixed bug in enforcement.py" > notes.md
where the target is notes.md, not enforcement.py.
"""

import re
import shlex
from pathlib import Path

ENKI_ROOT = Path.home() / ".enki"

# Layer 0 PROTECTED files — listed by basename.
# If a file's basename is here, it cannot be written by CC. Period.
LAYER0_PROTECTED = {
    # Hook scripts
    "session-start.sh",
    "pre-tool-use.sh",
    "post-tool-use.sh",
    "pre-compact.sh",
    "post-compact.sh",
    "session-end.sh",
    # Core enforcement
    "uru.py",
    "layer0.py",
    "abzu.py",
    # Identity
    "PERSONA.md",
    # Shared prompt templates
    "_base.md",
    "_coding_standards.md",
    # Agent prompts (13 files)
    "pm.md",
    "architect.md",
    "dba.md",
    "dev.md",
    "qa.md",
    "ui_ux.md",
    "validator.md",
    "reviewer.md",
    "infosec.md",
    "devops.md",
    "performance.md",
    "researcher.md",
    "em.md",
}

# Directories under ~/.enki/ that are fully protected by Layer 0
LAYER0_PROTECTED_PATHS = [
    ENKI_ROOT / "hooks",
    ENKI_ROOT / "prompts",
]


def is_layer0_protected(filepath: str) -> bool:
    """Check if file is Layer 0 protected. These CANNOT be written by CC."""
    path = Path(filepath).resolve()
    basename = path.name

    if basename in LAYER0_PROTECTED:
        return True

    for protected in LAYER0_PROTECTED_PATHS:
        try:
            path.relative_to(protected.resolve())
            return True
        except ValueError:
            continue

    # uru.db is also Layer 0 protected (enforcement DB)
    if path == (ENKI_ROOT / "uru.db").resolve():
        return True

    return False


def is_exempt(filepath: str, tool_name: str | None = None) -> bool:
    """Check if a file path is exempt from workflow gate checks.

    CRITICAL: This function must be fast (<1ms) and must NEVER
    produce false negatives (blocking legitimate infrastructure writes).
    False positives (allowing a code file through) are caught by
    the next gate check, so they're less dangerous.

    Returns True if the file should bypass Layer 1 gate checks.
    Returns False if the file needs full gate verification.

    Layer 0 protected files are handled BEFORE this function is called.
    If is_layer0_protected() returns True, the call is blocked
    regardless of what this function returns.
    """
    path = Path(filepath)

    # Category 1: Enki infrastructure (except Layer 0 protected)
    try:
        path.resolve().relative_to(ENKI_ROOT.resolve())
        return True
    except ValueError:
        pass

    # Category 2: Documentation (*.md outside src/)
    if path.suffix == ".md":
        parts = path.parts
        if "src" not in parts:
            return True

    # Category 3: Configuration files
    if path.name == "CLAUDE.md":
        return True
    if path.suffix in (".toml", ".yaml", ".yml"):
        parts = path.parts
        if "src" not in parts:
            return True

    # Category 4: .claude directory
    if ".claude" in path.parts:
        return True

    # Category 5: Git
    if ".git" in path.parts:
        return True

    return False


def extract_write_targets(command: str) -> list[str]:
    """Extract file paths being written to from a bash command.

    Returns list of file paths that are write targets.
    Returns empty list if no write targets detected (read-only command).

    IMPORTANT: Only extracts TARGETS, not mentions.
    'echo "enforcement.py" > notes.md' returns ['notes.md']
    'sed -i s/x/y/ enforcement.py' returns ['enforcement.py']
    'cat enforcement.py' returns [] (read-only)
    """
    targets = []

    # Split on semicolons and pipes (rough segmentation)
    segments = re.split(r"[;|]", command)

    for segment in segments:
        segment = segment.strip()
        if not segment:
            continue

        # Redirect operators: > >>
        redirect_match = re.findall(r">{1,2}\s*(\S+)", segment)
        targets.extend(redirect_match)

        # tee: target is the argument
        tee_match = re.search(r"\btee\s+(?:-a\s+)?(\S+)", segment)
        if tee_match:
            targets.append(tee_match.group(1))

        # sed -i: target is the LAST argument
        if re.search(r"\bsed\s+.*-i", segment):
            try:
                parts = shlex.split(segment)
                if parts:
                    targets.append(parts[-1])
            except ValueError:
                pass

        # cp, mv: target is the LAST argument
        if re.search(r"\b(cp|mv)\s+", segment):
            try:
                parts = shlex.split(segment)
                if len(parts) >= 3:
                    targets.append(parts[-1])
            except ValueError:
                pass

        # rm: target is all arguments after flags
        if re.search(r"\brm\s+", segment):
            try:
                parts = shlex.split(segment)
                for part in parts[1:]:
                    if not part.startswith("-"):
                        targets.append(part)
            except ValueError:
                pass

        # python -c with open(..., 'w'): block as suspicious
        if re.search(r"python.*-c", segment):
            if re.search(r"open\(.*['\"]w", segment):
                targets.append("__PYTHON_WRITE__")

    return targets


def extract_db_targets(command: str) -> list[str]:
    """Extract database files being targeted by bash commands.

    For Layer 0.5 — catches sqlite3 binary and Python sqlite3 module.
    Returns list of .db file paths being targeted.
    """
    targets = []

    # sqlite3 binary: sqlite3 path/to/file.db "..."
    sqlite_match = re.findall(r"\bsqlite3\s+(\S+\.db\S*)", command)
    targets.extend(sqlite_match)

    # Python sqlite3.connect
    connect_match = re.findall(
        r"sqlite3\.connect\([\"']([^\"']+)[\"']", command
    )
    targets.extend(connect_match)

    # File operations targeting .db files
    redirect_db = re.findall(r">{1,2}\s*(\S+\.db)", command)
    targets.extend(redirect_db)

    cp_mv_rm_db = re.findall(r"\b(?:cp|mv|rm)\s+.*?(\S+\.db)", command)
    targets.extend(cp_mv_rm_db)

    return [t for t in targets if t.endswith(".db")]
