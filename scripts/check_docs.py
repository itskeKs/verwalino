#!/usr/bin/env python3
"""Check relative links and images in tracked Markdown outside docs/archive/.

Check inline links, reference definitions and HTML href/src attributes. Ignore
URLs, fragments, fenced code blocks and inline code; resolve /paths from the repo.
"""

import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parent.parent
SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")
FENCE_RE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
CODE_SPAN_RE = re.compile(r"(`+)(?:(?!\1).)+?\1")
INLINE_RE = re.compile(
    r"\]\(\s*(<[^>\n]*>|(?:[^()\s]|\([^()\s]*\))+)(?:\s+(?:\"[^\"]*\"|'[^']*'|\([^)]*\)))?\s*\)"
)
REFDEF_RE = re.compile(r"^\s{0,3}\[(?!\^)[^\]]+\]:\s*(<[^>]*>|\S+)")
HTML_ATTR_RE = re.compile(r"""\b(?:href|src)\s*=\s*(?:"([^"]*)"|'([^']*)')""", re.IGNORECASE)


def tracked_markdown(root: Path) -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "-z", "--", "*.md"], cwd=root, check=True, capture_output=True,
    ).stdout
    return sorted(
        root / name for name in out.decode().split("\0")
        if name and not name.startswith("docs/archive/") and (root / name).is_file()
    )


def iter_prose_lines(text: str):
    """Yield (line number, line) outside fenced code blocks, inline code blanked."""
    fence = None
    for number, line in enumerate(text.splitlines(), start=1):
        match = FENCE_RE.match(line)
        if match:
            marker = match.group(1)
            if fence is None:
                fence = marker
            elif (marker[0] == fence[0] and len(marker) >= len(fence)
                  and not line[match.end():].strip()):
                fence = None
            continue
        if fence is None:
            yield number, CODE_SPAN_RE.sub(lambda m: " " * len(m.group(0)), line)


def extract_targets(text: str):
    """Yield (line number, raw target) for every link-like reference."""
    for number, line in iter_prose_lines(text):
        for match in INLINE_RE.finditer(line):
            yield number, match.group(1)
        ref = REFDEF_RE.match(line)
        if ref:
            yield number, ref.group(1)
        for match in HTML_ATTR_RE.finditer(line):
            yield number, match.group(1) if match.group(1) is not None else match.group(2)


def check_file(root: Path, path: Path) -> list[str]:
    problems = []
    rel = path.relative_to(root).as_posix()
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        return [f"{rel}: cannot read file ({error})"]
    for line, raw in extract_targets(text):
        target = raw.strip().removeprefix("<").removesuffix(">").strip()
        if not target or SCHEME_RE.match(target) or target.startswith("//"):
            continue
        target_path = unquote(target.partition("#")[0].partition("?")[0])
        if not target_path:
            continue
        resolved = root / target_path.lstrip("/") if target_path.startswith("/") else path.parent / target_path
        resolved = Path(os.path.normpath(resolved))
        if not resolved.exists():
            shown = resolved.relative_to(root).as_posix() if root in resolved.parents else str(resolved)
            problems.append(f"{rel}:{line}: broken link '{raw.strip()}' (no such file: {shown})")
    return problems


def main() -> int:
    files = tracked_markdown(ROOT)
    problems = [problem for path in files for problem in check_file(ROOT, path)]
    for problem in problems:
        print(problem)
    if problems:
        return 1
    print(f"check_docs: OK ({len(files)} Markdown files, links)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
