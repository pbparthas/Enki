"""Language detection and tree-sitter parser mapping."""

from pathlib import Path

# Extension to language name mapping
EXT_TO_LANGUAGE: dict[str, str] = {
    ".ts": "typescript", ".tsx": "typescript",
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript",
    ".py": "python",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".kt": "kotlin",
    ".swift": "swift",
    ".cs": "c_sharp",
    ".rb": "ruby",
    ".php": "php",
    ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp",
    ".c": "c",
    ".html": "html",
    ".css": "css",
    ".json": "json",
    ".yaml": "yaml", ".yml": "yaml",
    ".toml": "toml",
    ".md": "markdown",
    ".sh": "bash",
    ".sql": "sql",
}

# tree-sitter-languages supports these — verify before adding to EXT_TO_LANGUAGE
SUPPORTED_LANGUAGES = {
    "typescript", "javascript", "python", "go", "rust",
    "java", "kotlin", "swift", "c_sharp", "ruby", "php",
    "cpp", "c", "html", "css", "json", "yaml", "bash", "sql",
}


def detect_language(file_path: str) -> str | None:
    ext = Path(file_path).suffix.lower()
    lang = EXT_TO_LANGUAGE.get(ext)
    if lang and lang in SUPPORTED_LANGUAGES:
        return lang
    return None


def is_source_file(file_path: str) -> bool:
    """True if this file should be included in the graph."""
    path = Path(file_path)
    # Skip common non-source directories
    skip_dirs = {
        "node_modules", ".git", "__pycache__", ".venv", "venv",
        "dist", "build", ".next", ".cache", "coverage",
        ".worktrees", ".enki",
    }
    for part in path.parts:
        if part in skip_dirs:
            return False
    return detect_language(file_path) is not None

