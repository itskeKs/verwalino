#!/usr/bin/env python3
"""Attach Cursor rule bodies as Codex context, without enforcement or state.

Adapted from coddy-agent's .codex/hooks/attach_rules.py.
Supports flat frontmatter with description, block-list globs and alwaysApply.
Malformed rules are skipped; malformed payloads and unexpected failures exit 0.

MIT License

Copyright (c) 2026 Pavel Rykov

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
RULES_DIR = REPO_ROOT / ".cursor/rules"
PATCH_FILE_RE = re.compile(r"^\*\*\*\s+(?:Add|Update|Delete)\s+File:\s*(.+?)\s*$", re.M)
PATCH_MOVE_RE = re.compile(r"^\*\*\*\s+Move to:\s*(.+?)\s*$", re.M)
PATH_FIELDS = {"file", "file_path", "fileName", "filePath", "path", "paths",
               "source", "destination", "destination_path", "old_path", "new_path"}


class Rule:
    def __init__(self, path: Path, globs: list[str], always: bool, body: str):
        self.path = path
        self.globs = globs
        self.always = always
        self.body = body


def scalar(value: str) -> str:
    """Decode a plain or quoted string in the supported frontmatter subset."""
    if value.startswith('"'):
        decoded = json.loads(value)
        if not isinstance(decoded, str):
            raise ValueError("Expected a string")
        return decoded
    if value.startswith("'"):
        if len(value) < 2 or not value.endswith("'"):
            raise ValueError("Unterminated string")
        return value[1:-1].replace("''", "'")
    if not value or any(char in value for char in "[]{}#\n"):
        raise ValueError("Unsupported scalar")
    return value


def parse_rule(path: Path) -> Rule:
    """Parse the repository's block-list convention without a YAML dependency."""
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0] != "---":
        raise ValueError("Missing frontmatter")
    end = lines.index("---", 1)
    fields = {}
    in_globs = False
    for line in lines[1:end]:
        if not line.strip():
            continue
        if in_globs and line.startswith("  - "):
            pattern = scalar(line[4:].strip())
            if not pattern:
                raise ValueError("Empty glob")
            fields["globs"].append(pattern)
            continue
        key, separator, value = line.partition(":")
        if not separator or key in fields:
            raise ValueError("Invalid or duplicate field")
        value = value.strip()
        in_globs = key == "globs"
        if key == "globs" and not value:
            fields[key] = []
        elif key == "alwaysApply" and value in {"true", "false"}:
            fields[key] = value == "true"
        elif key == "description":
            fields[key] = scalar(value)
        else:
            raise ValueError("Unsupported frontmatter")
    if not fields.get("description") or "alwaysApply" not in fields:
        raise ValueError("Missing description or activation")
    globs = fields.get("globs", [])
    always = fields["alwaysApply"]
    body = "\n".join(lines[end + 1:]).strip()
    if not body or (not always and not globs):
        raise ValueError("Empty body or scope")
    return Rule(path, globs, always, body)


def load_rules() -> list[Rule]:
    rules = []
    for path in sorted(RULES_DIR.glob("*.mdc")):
        try:
            rules.append(parse_rule(path))
        except (OSError, ValueError):
            # A broken topic must not suppress context from other valid rules.
            continue
    return rules


def glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Keep * within a segment; **/ also matches zero directories."""
    out = []
    index = 0
    while index < len(pattern):
        if pattern.startswith("**/", index):
            out.append("(?:.*/)?")
            index += 3
        elif pattern.startswith("**", index):
            out.append(".*")
            index += 2
        elif pattern[index] == "*":
            out.append("[^/]*")
            index += 1
        elif pattern[index] == "?":
            out.append("[^/]")
            index += 1
        else:
            out.append(re.escape(pattern[index]))
            index += 1
    return re.compile("^" + "".join(out) + "$")


def patched_paths(tool_input: object) -> list[str]:
    """Collect patch headers and known structured edit fields inside the repo."""
    candidates = []

    def walk(node: object, key: str = "") -> None:
        if isinstance(node, str):
            if key in PATH_FIELDS:
                candidates.append(node)
            candidates.extend(PATCH_FILE_RE.findall(node))
            candidates.extend(PATCH_MOVE_RE.findall(node))
        elif isinstance(node, list):
            for item in node:
                walk(item, key)
        elif isinstance(node, dict):
            for child_key, item in node.items():
                walk(item, child_key)

    walk(tool_input)
    root = REPO_ROOT.resolve()
    found = set()
    for raw in candidates:
        if not raw.strip() or "\x00" in raw:
            continue
        path = Path(raw.strip())
        candidate = path if path.is_absolute() else root / path
        try:
            relative = candidate.resolve().relative_to(root)
        except (OSError, RuntimeError, ValueError):
            continue
        if relative.parts:
            found.add(relative.as_posix())
    return sorted(found)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            return 0
        event = payload.get("hook_event_name")
        if event == "SessionStart":
            selected = [rule for rule in load_rules() if rule.always]
        elif event == "PreToolUse":
            if payload.get("tool_name") not in {"apply_patch", "Edit", "Write"}:
                return 0
            paths = patched_paths(payload.get("tool_input"))
            selected = [rule for rule in load_rules() if not rule.always
                        and any(glob_to_regex(glob).match(path)
                                for glob in rule.globs for path in paths)]
        else:
            return 0
        if selected:
            chunks = ["Project rules for this context (guidance only; AGENTS.md remains in force)."]
            for rule in selected:
                relative = rule.path.relative_to(REPO_ROOT).as_posix()
                chunks.append(f"<!-- from {relative} -->\n\n{rule.body}")
            json.dump({"hookSpecificOutput": {"hookEventName": event,
                                            "additionalContext": "\n\n".join(chunks)}}, sys.stdout)
        return 0
    except Exception:
        # Rules are context, never permission to block an edit or a session.
        return 0


if __name__ == "__main__":
    sys.exit(main())
