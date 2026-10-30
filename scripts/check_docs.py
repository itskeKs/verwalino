#!/usr/bin/env python3
"""Check that relative Markdown links and image paths point to existing files.

Scope: every tracked ``*.md`` file (``git ls-files``; falls back to a directory
walk) except ``docs/archive/``, which is legacy material that is neither read
nor maintained. Links *into* the archive are fine as long as the file exists.

Checked: inline links and images ``[t](path)`` / ``![a](path)``, reference
definitions ``[id]: path`` and HTML ``href=`` / ``src=`` attributes. Skipped:
URLs with a scheme (``https:``, ``mailto:`` ...), links inside fenced code
blocks and inline code spans. Site-root links (``/x``) resolve from the repo
root.

Anchors (``file.md#heading``, ``#heading``) are only checked with ``--anchors``
(GitHub-style heading slugs, duplicate headings get ``-1``, ``-2`` ...).

Usage::

    python scripts/check_docs.py              # whole repo
    python scripts/check_docs.py README.md docs/   # given files / directories
    python scripts/check_docs.py --anchors

Exit code: 0 = all links resolve, 1 = broken links found, 2 = usage error.
Stdlib only.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parent.parent

# Directories whose Markdown is not checked (relative to the repo root, posix).
EXCLUDED_PREFIXES = ("docs/archive/",)
# Directory names never descended into by the fallback walk.
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".worktrees", "worktrees", "dist", "build"}

SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")
FENCE_RE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
CODE_SPAN_RE = re.compile(r"(`+)(?:(?!\1).)+?\1")
# [text](target "title") — target may be <...> or contain balanced parentheses.
INLINE_RE = re.compile(
    r"\]\(\s*(<[^>\n]*>|(?:[^()\s]|\([^()\s]*\))+)(?:\s+(?:\"[^\"]*\"|'[^']*'|\([^)]*\)))?\s*\)"
)
REFDEF_RE = re.compile(r"^\s{0,3}\[(?!\^)[^\]]+\]:\s*(<[^>]*>|\S+)")
HTML_ATTR_RE = re.compile(r"""\b(?:href|src)\s*=\s*(?:"([^"]*)"|'([^']*)')""", re.IGNORECASE)
HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$")
HTML_ANCHOR_RE = re.compile(r"""<a\s[^>]*\b(?:name|id)\s*=\s*["']([^"']+)["']""", re.IGNORECASE)


def tracked_markdown(root: Path) -> list[Path]:
    """All *.md files: tracked ones if git is available, else a directory walk."""
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", "*.md"],
            cwd=root,
            check=True,
            capture_output=True,
        ).stdout
        files = [root / p for p in out.decode().split("\0") if p]
        # A path deleted in the working tree is still listed by --cached.
        return sorted(p for p in files if p.is_file())
    except (OSError, subprocess.CalledProcessError):
        found = []
        for path in root.rglob("*.md"):
            if any(part in SKIP_DIRS for part in path.relative_to(root).parts):
                continue
            found.append(path)
        return sorted(found)


def expand(root: Path, args: list[str]) -> list[Path]:
    if not args:
        return tracked_markdown(root)
    files: list[Path] = []
    for arg in args:
        path = (Path.cwd() / arg).resolve()
        if path.is_dir():
            files.extend(p for p in tracked_markdown(root) if path in p.parents)
        elif path.is_file():
            files.append(path)
        else:
            print(f"check_docs: no such file or directory: {arg}", file=sys.stderr)
            raise SystemExit(2)
    return sorted(set(files))


def is_excluded(root: Path, path: Path) -> bool:
    try:
        rel = path.relative_to(root).as_posix()
    except ValueError:
        return False
    return rel.startswith(EXCLUDED_PREFIXES)


def iter_prose_lines(text: str):
    """Yield (line number, line) outside fenced code blocks, inline code blanked."""
    fence: str | None = None
    for number, line in enumerate(text.splitlines(), start=1):
        match = FENCE_RE.match(line)
        if match:
            marker = match.group(1)
            if fence is None:
                fence = marker[0] * 3
            elif marker[0] == fence[0] and len(marker) >= len(fence):
                fence = None
            continue
        if fence is not None:
            continue
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


def slugify(heading: str) -> str:
    """GitHub-style heading anchor."""
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", heading)  # links/images -> text
    text = re.sub(r"<[^>]+>", "", text)  # inline HTML
    text = text.replace("`", "").replace("*", "")
    text = text.strip().lower()
    text = re.sub(r"[^\w\- ]", "", text)  # \w is unicode-aware (umlauts stay)
    return text.replace(" ", "-")


_anchor_cache: dict[Path, set[str]] = {}


def anchors_of(path: Path) -> set[str]:
    if path in _anchor_cache:
        return _anchor_cache[path]
    anchors: set[str] = set()
    seen: dict[str, int] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        _anchor_cache[path] = anchors
        return anchors
    fence: str | None = None
    for line in text.splitlines():
        fm = FENCE_RE.match(line)
        if fm:
            marker = fm.group(1)
            if fence is None:
                fence = marker[0] * 3
            elif marker[0] == fence[0] and len(marker) >= len(fence):
                fence = None
            continue
        if fence is not None:
            continue
        for am in HTML_ANCHOR_RE.finditer(line):
            anchors.add(am.group(1))
        hm = HEADING_RE.match(line)
        if hm:
            slug = slugify(hm.group(1))
            count = seen.get(slug, 0)
            seen[slug] = count + 1
            anchors.add(slug if count == 0 else f"{slug}-{count}")
    _anchor_cache[path] = anchors
    return anchors


def check_file(root: Path, path: Path, check_anchors: bool) -> list[str]:
    problems: list[str] = []
    rel = path.relative_to(root).as_posix() if root in path.parents else str(path)
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        return [f"{rel}: cannot read file ({error})"]
    for line, raw in extract_targets(text):
        target = raw.strip()
        if target.startswith("<") and target.endswith(">"):
            target = target[1:-1].strip()
        if not target or SCHEME_RE.match(target) or target.startswith("//"):
            continue
        target_path, _, fragment = target.partition("#")
        target_path = unquote(target_path.partition("?")[0])
        fragment = unquote(fragment)
        if not target_path:
            resolved = path  # same-file anchor
        elif target_path.startswith("/"):
            resolved = root / target_path.lstrip("/")
        else:
            resolved = path.parent / target_path
        resolved = Path(resolved)
        # normpath-style resolution without requiring the path to exist.
        resolved = Path(*[p for p in _normalize(resolved.parts)])
        if not resolved.exists():
            shown = resolved.relative_to(root).as_posix() if root in resolved.parents else str(resolved)
            problems.append(f"{rel}:{line}: broken link '{raw.strip()}' (no such file: {shown})")
            continue
        if check_anchors and fragment and resolved.is_file() and resolved.suffix.lower() == ".md":
            if fragment.lower() not in {a.lower() for a in anchors_of(resolved)}:
                problems.append(f"{rel}:{line}: broken anchor '{raw.strip()}' (no heading '#{fragment}' in {resolved.name})")
    return problems


def _normalize(parts: tuple[str, ...]) -> list[str]:
    out: list[str] = []
    for part in parts:
        if part == "..":
            if out and out[-1] not in ("/", ".."):
                out.pop()
            else:
                out.append(part)
        elif part != ".":
            out.append(part)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("paths", nargs="*", help="Markdown files or directories (default: whole repo)")
    parser.add_argument("--anchors", action="store_true", help="also verify #fragment targets in Markdown files")
    args = parser.parse_args(argv)

    files = [p for p in expand(ROOT, args.paths) if not is_excluded(ROOT, p)]
    problems: list[str] = []
    for path in files:
        problems.extend(check_file(ROOT, path, args.anchors))

    for problem in problems:
        print(problem)
    checked = "links and anchors" if args.anchors else "links"
    if problems:
        print(f"check_docs: {len(problems)} problem(s) in {len(files)} Markdown files ({checked})", file=sys.stderr)
        return 1
    print(f"check_docs: OK ({len(files)} Markdown files, {checked})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
