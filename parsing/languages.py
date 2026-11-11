"""Language detection by filename/extension."""
from __future__ import annotations

from pathlib import PurePosixPath

EXT_LANG = {
    ".py": "python", ".pyi": "python", ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript",
    ".cjs": "javascript", ".ts": "typescript", ".tsx": "typescript", ".java": "java", ".go": "go",
    ".rs": "rust", ".c": "c", ".h": "c", ".cpp": "cpp", ".cc": "cpp", ".hpp": "cpp", ".cs": "csharp",
    ".rb": "ruby", ".php": "php", ".kt": "kotlin", ".swift": "swift", ".md": "markdown",
    ".markdown": "markdown", ".rst": "markdown", ".yaml": "yaml", ".yml": "yaml", ".json": "json",
    ".toml": "toml", ".ini": "ini", ".cfg": "ini", ".sh": "bash", ".sql": "sql", ".html": "html",
    ".css": "css", ".txt": "text", ".tf": "terraform", ".env": "dotenv",
}
CODE_LANGS = {"python", "javascript", "typescript", "java", "go", "rust", "c", "cpp", "csharp", "ruby",
              "php", "kotlin", "swift", "bash", "sql"}
DOC_LANGS = {"markdown", "text"}
CONFIG_LANGS = {"yaml", "json", "toml", "ini", "dockerfile", "terraform", "dotenv"}


def detect_language(path: str) -> str:
    p = PurePosixPath(path)
    name = p.name.lower()
    if name == "dockerfile" or name.startswith("dockerfile."):
        return "dockerfile"
    if name.startswith(".env"):
        return "dotenv"
    if name in {"procfile", "makefile"}:
        return "text"
    return EXT_LANG.get(p.suffix.lower(), "text")
