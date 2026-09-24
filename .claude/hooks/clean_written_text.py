#!/usr/bin/env python3
"""PostToolUse hook: strip invisible Unicode from text files the agent writes.

Uses the vendored scripts from the clean-user-facing-text skill (Layer A only).
No service dependency — runs deterministic cleanup directly.

Exit codes follow the PostToolUse contract:
  0 = nothing to say
  2 = stderr is shown to the model
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

SKILL_SCRIPTS = Path(__file__).resolve().parent.parent / "skills" / "clean-user-facing-text" / "scripts"
sys.path.insert(0, str(SKILL_SCRIPTS))

from text_unicode import clean_text  # noqa: E402

FILE_WRITING_TOOLS = frozenset({"Write", "Edit", "MultiEdit", "NotebookEdit"})

TEXT_EXTENSIONS = frozenset({
    ".txt", ".md", ".markdown", ".rst", ".html", ".htm", ".xml",
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf",
    ".py", ".js", ".ts", ".jsx", ".tsx", ".css", ".scss",
    ".sh", ".bash", ".zsh", ".fish",
    ".sql", ".graphql", ".gql",
    ".tex", ".ltx", ".bib",
    ".csv", ".tsv",
    ".eml", ".msg",
})

MAX_FILE_BYTES = 10 * 1024 * 1024

EXIT_QUIET = 0
EXIT_SHOW_MODEL = 2


def target_path(payload: dict) -> Path | None:
    if payload.get("tool_name") not in FILE_WRITING_TOOLS:
        return None
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    raw = tool_input.get("file_path") or tool_input.get("notebook_path")
    if not isinstance(raw, str) or not raw.strip():
        return None
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = Path(payload.get("cwd") or Path.cwd()) / path
    return path


def main() -> int:
    raw = sys.stdin.read()
    if not raw.strip():
        return EXIT_QUIET
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return EXIT_QUIET
    if not isinstance(payload, dict):
        return EXIT_QUIET

    path = target_path(payload)
    if path is None or not path.is_file():
        return EXIT_QUIET
    if path.suffix.lower() not in TEXT_EXTENSIONS:
        return EXIT_QUIET
    if path.stat().st_size > MAX_FILE_BYTES:
        return EXIT_QUIET

    try:
        original = path.read_text(encoding="utf-8", errors="surrogateescape")
    except (OSError, UnicodeDecodeError):
        return EXIT_QUIET

    cleaned, stats = clean_text(original, normalize_spaces=False)
    if cleaned == original:
        return EXIT_QUIET

    path.write_text(cleaned, encoding="utf-8")

    removed = stats.get("removed_count", 0)
    replaced = stats.get("replaced_count", 0)
    parts = []
    if removed:
        parts.append(f"{removed} invisible character(s) removed")
    if replaced:
        parts.append(f"{replaced} homoglyph(s) replaced")
    summary = ", ".join(parts) or "Unicode cleaned"

    output = {
        "systemMessage": f"watermarks-remover: cleaned {path.name} in place ({summary})",
        "hookSpecificOutput": {"hookEventName": "PostToolUse"},
        "additionalContext": (
            f"The watermarks-remover hook stripped invisible Unicode from {path} "
            f"after the write ({summary}). The file on disk no longer matches "
            "what was written; re-read it before editing again."
        ),
    }
    print(json.dumps(output))
    print(f"watermarks-remover: {path.name}: {summary}", file=sys.stderr)
    return EXIT_SHOW_MODEL


if __name__ == "__main__":
    raise SystemExit(main())
