"""The OpenAI SDK is imported only inside the declarations package."""

from __future__ import annotations

import ast
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1] / "app"


def _openai_import_lines(path: Path) -> list[int]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    lines: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            lines.extend(
                node.lineno
                for alias in node.names
                if alias.name == "openai" or alias.name.startswith("openai.")
            )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == "openai" or module.startswith("openai."):
                lines.append(node.lineno)
    return lines


def test_openai_sdk_is_imported_only_in_declarations() -> None:
    offenders: list[str] = []
    for path in sorted(APP_ROOT.rglob("*.py")):
        relative = path.relative_to(APP_ROOT)
        if relative.parts[0] == "declarations":
            continue
        offenders.extend(f"{relative}:{line}" for line in _openai_import_lines(path))
    assert offenders == []
