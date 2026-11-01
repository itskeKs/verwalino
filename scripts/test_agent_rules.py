"""Agent-rule contracts and Codex hook tests; stdlib only, no database."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / ".codex/hooks/attach_rules.py"
TOPICS = {"language", "migrations", "api-layer", "engine", "gobd",
          "frontend-ui", "testing", "selfhost", "docs",
          "architecture", "domain", "ai-pipeline", "session-cleanup"}


def read_rule(path: Path) -> tuple[dict, str]:
    """Validate the deliberately small frontmatter format used in both trees."""
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0] != "---":
        raise ValueError(f"{path}: missing frontmatter")
    end = lines.index("---", 1)
    header = {}
    list_key = None
    for line in lines[1:end]:
        if line.startswith("  - ") and list_key:
            item = json.loads(line[4:])
            if not isinstance(item, str) or not item:
                raise ValueError(f"{path}: invalid path")
            header[list_key].append(item)
            continue
        key, separator, value = line.partition(":")
        if not separator or key in header:
            raise ValueError(f"{path}: invalid field")
        value = value.strip()
        list_key = key if key in {"paths", "globs"} else None
        if list_key:
            if value:
                raise ValueError(f"{path}: use a block list")
            header[key] = []
        elif key == "alwaysApply":
            if value not in {"true", "false"}:
                raise ValueError(f"{path}: invalid activation")
            header[key] = value == "true"
        elif key == "description" and value:
            header[key] = value
        else:
            raise ValueError(f"{path}: unknown or empty field")
    return header, "\n".join(lines[end + 1:]).strip()


class AgentRuleTests(unittest.TestCase):
    def run_hook(self, payload, *, root=ROOT, raw=False):
        result = subprocess.run(
            [sys.executable, str(root / ".codex/hooks/attach_rules.py")],
            input=payload if raw else json.dumps(payload), text=True,
            capture_output=True, cwd=root,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        if not result.stdout:
            return ""
        output = json.loads(result.stdout)
        self.assertEqual(set(output), {"hookSpecificOutput"})
        specific = output["hookSpecificOutput"]
        self.assertEqual(specific["hookEventName"], payload["hook_event_name"])
        return specific["additionalContext"]

    def attached_topics(self, payload, **kwargs):
        context = self.run_hook(payload, **kwargs)
        return set(re.findall(r"<!-- from \.cursor/rules/([\w-]+)\.mdc -->", context))

    def temporary_repo(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        hook = root / ".codex/hooks/attach_rules.py"
        hook.parent.mkdir(parents=True)
        shutil.copyfile(HOOK, hook)
        (root / ".cursor/rules").mkdir(parents=True)
        return root

    def test_every_topic_has_an_equivalent_body_and_activation_in_both_hosts(self):
        claude = {path.stem: path for path in (ROOT / ".claude/rules").glob("*.md")}
        cursor = {path.stem: path for path in (ROOT / ".cursor/rules").glob("*.mdc")}
        self.assertTrue(TOPICS <= set(claude))
        self.assertEqual(set(claude), set(cursor))
        for topic in claude:
            with self.subTest(topic=topic):
                left, left_body = read_rule(claude[topic])
                right, right_body = read_rule(cursor[topic])
                self.assertEqual(set(left), {"description"} | ({"paths"} if "paths" in left else set()))
                self.assertEqual(set(right), {"description", "alwaysApply"} | ({"globs"} if "globs" in right else set()))
                self.assertEqual(left["description"], right["description"])
                self.assertTrue(left_body)
                self.assertEqual(left_body, right_body)
                self.assertEqual("paths" not in left, right["alwaysApply"])
                self.assertEqual(left.get("paths", []), right.get("globs", []))
                if not right["alwaysApply"]:
                    self.assertTrue(right["globs"])

    def test_claude_md_is_a_symlink_to_the_common_policy(self):
        link = ROOT / "CLAUDE.md"
        self.assertTrue(link.is_symlink(), "CLAUDE.md must be a symlink, not a copy")
        self.assertEqual(os.readlink(link), "AGENTS.md")
        self.assertEqual(link.read_text(encoding="utf-8"), (ROOT / "AGENTS.md").read_text(encoding="utf-8"))

    def test_session_cleanup_policy_is_shared_by_all_hosts(self):
        required = (
            "Every session removes what it created when its task is merged, closed or abandoned.",
            "Before a new task, check for and remove your own leftovers.",
            "Remove your own worktrees and local branches after the PR is merged or closed;",
            "never discard uncommitted work.",
            "Remove your own separately authorized acceptance/verifier stacks completely:",
            "containers, volumes (including anonymous volumes), networks and local images used only by that project.",
            "Stop your own dev servers, free their ports and terminate every background process you started, including installer-harness children.",
            "Remove scratch files and temporary artefacts you wrote",
            "remove only your own inactive lock files.",
            "Never touch other sessions' resources:",
            "A matching name or label alone does not establish ownership. Never prune Docker globally.",
            "Leave production (`docker-compose.prod.yml`, project `hausverwaltung` on the owner's Mac, `verwalino` on the hosted server) and shared test project `infra` untouched.",
            "Never remove `/tmp/verwalino-testdb.lock` while another run holds it.",
            "The only repair is starting the shared test DB with `docker compose -f infra/docker-compose.declaration-test.yml up -d postgres` when it is down, while holding its otherwise free lock; never restart a running DB.",
            "Report cleanup in one line in the status/report, never in a repository file.",
        )
        agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        for relative in ("AGENTS.md", ".claude/rules/session-cleanup.md", ".cursor/rules/session-cleanup.mdc"):
            with self.subTest(path=relative):
                path = ROOT / relative
                self.assertTrue(path.is_file(), f"{relative}: missing cleanup rule")
                body = agents if relative == "AGENTS.md" else read_rule(path)[1]
                normalized = " ".join(body.split())
                for sentence in required:
                    self.assertTrue(sentence in normalized, f"{relative}: missing cleanup contract: {sentence}")
                if relative != "AGENTS.md":
                    section = agents.split("## Session cleanup\n", 1)[1].split("\n## ", 1)[0].strip()
                    self.assertEqual(body.split("\n", 1)[1].strip(), section)

    def test_hooks_configuration_wires_both_events_to_the_adapter(self):
        config = json.loads((ROOT / ".codex/hooks.json").read_text())
        self.assertEqual(set(config["hooks"]), {"SessionStart", "PreToolUse"})
        expected = 'python3 "$(git rev-parse --show-toplevel)/.codex/hooks/attach_rules.py"'
        for event, names in (("SessionStart", ("startup", "resume", "clear", "compact", "fork")),
                             ("PreToolUse", ("apply_patch", "Edit", "Write"))):
            entries = config["hooks"][event]
            self.assertEqual(len(entries), 1)
            for name in names:
                self.assertIsNotNone(re.fullmatch(entries[0]["matcher"], name))
            self.assertIsNone(re.fullmatch(entries[0]["matcher"], "Bash"))
            command, = entries[0]["hooks"]
            self.assertEqual(command["type"], "command")
            self.assertEqual(command["command"], expected)

    def test_session_start_returns_only_the_always_on_rules(self):
        expected = {path.stem for path in (ROOT / ".cursor/rules").glob("*.mdc")
                    if read_rule(path)[0]["alwaysApply"]}
        self.assertEqual(expected, {"language", "docs", "session-cleanup"})
        for source in ("startup", "resume", "clear", "compact", "fork"):
            with self.subTest(source=source):
                payload = {"hook_event_name": "SessionStart", "source": source}
                self.assertEqual(self.attached_topics(payload), expected)
                self.assertIn("The software is German", self.run_hook(payload))
                self.assertIn("Every session removes what it created", self.run_hook(payload))

    def test_a_migration_patch_attaches_migrations_and_not_frontend_rules(self):
        payload = {"hook_event_name": "PreToolUse", "tool_name": "apply_patch",
                   "tool_input": {"patch": "*** Begin Patch\n*** Add File: backend/migrations/versions/x.py\n+pass\n*** End Patch"}}
        context = self.run_hook(payload)
        self.assertIn(".cursor/rules/migrations.mdc", context)
        self.assertIn("Never change a committed migration", context)
        self.assertNotIn(".cursor/rules/frontend-ui.mdc", context)
        self.assertNotIn(".cursor/rules/language.mdc", context)

    def test_a_patch_from_a_subdirectory_resolves_paths_from_the_session_cwd(self):
        payload = {"hook_event_name": "PreToolUse", "tool_name": "apply_patch",
                   "cwd": str(ROOT / "frontend"),
                   "tool_input": {"patch": "*** Begin Patch\n*** Update File: src/App.tsx\n@@\n-old\n+new\n*** End Patch"}}
        self.assertEqual(self.attached_topics(payload), {"frontend-ui"})

    def test_each_area_is_reachable_by_its_representative_path(self):
        cases = {
            "backend/app/api/auth.py": "api-layer",
            "backend/app/engine/rounding.py": "engine",
            "backend/app/services/payment_service.py": "gobd",
            "frontend/src/pages/LoginPage.tsx": "frontend-ui",
            "backend/tests/bdd/test_organization_isolation.py": "testing",
            "features/organization_isolation.feature": "testing",
            "scripts/test_agent_rules.py": "testing",
            "infra/docker-compose.declaration-test.yml": "selfhost",
            "scripts/self-host.sh": "selfhost",
            "backend/app/main.py": "architecture",
            "backend/app/services/settlement_service.py": "domain",
            "frontend/src/pages/DashboardPage.tsx": "domain",
            "backend/app/declarations/pipeline.py": "ai-pipeline",
            "backend/app/core/invoice_ocr.py": "ai-pipeline",
        }
        for path, topic in cases.items():
            with self.subTest(path=path):
                self.assertTrue((ROOT / path).is_file())
                payload = {"hook_event_name": "PreToolUse", "tool_name": "Edit",
                           "tool_input": {"file_path": path}}
                self.assertIn(topic, self.attached_topics(payload))

    def test_patch_add_update_delete_and_move_headers_select_both_areas(self):
        for operation in ("Add", "Update", "Delete"):
            with self.subTest(operation=operation):
                patch = f"*** Begin Patch\n*** {operation} File: backend/migrations/versions/x.py\n*** Move to: frontend/src/x.tsx\n*** End Patch"
                payload = {"hook_event_name": "PreToolUse", "tool_name": "apply_patch",
                           "tool_input": patch}
                self.assertEqual(self.attached_topics(payload), {"migrations", "domain", "frontend-ui"})

    def test_structured_write_paths_accept_absolute_paths_inside_the_repo(self):
        payload = {"hook_event_name": "PreToolUse", "tool_name": "Write",
                   "tool_input": {"file_path": str(ROOT / "frontend/src/x.tsx")}}
        self.assertEqual(self.attached_topics(payload), {"frontend-ui"})

    def test_unrelated_and_outside_paths_attach_nothing(self):
        for path in ("unrelated.txt", "../frontend/src/x.tsx", "/outside/frontend/src/x.tsx", "bad\x00path"):
            with self.subTest(path=path):
                payload = {"hook_event_name": "PreToolUse", "tool_name": "Edit",
                           "tool_input": {"file_path": path}}
                self.assertEqual(self.run_hook(payload), "")

    def test_a_single_star_stays_in_its_directory_and_double_star_includes_direct_children(self):
        root = self.temporary_repo()
        rules = root / ".cursor/rules"
        for topic, glob in (("direct", "frontend/src/*.tsx"), ("recursive", "frontend/src/**/*.tsx")):
            (rules / f"{topic}.mdc").write_text(
                f'---\ndescription: Example\nglobs:\n  - "{glob}"\nalwaysApply: false\n---\n{topic}\n')
        for path, expected in (("frontend/src/App.tsx", {"direct", "recursive"}),
                               ("frontend/src/pages/App.tsx", {"recursive"})):
            payload = {"hook_event_name": "PreToolUse", "tool_name": "Edit",
                       "tool_input": {"file_path": path}}
            self.assertEqual(self.attached_topics(payload, root=root), expected)

    def test_malformed_frontmatter_is_skipped_without_blocking_valid_rules(self):
        root = self.temporary_repo()
        rules = root / ".cursor/rules"
        shutil.copyfile(ROOT / ".cursor/rules/migrations.mdc", rules / "migrations.mdc")
        malformed = (
            "No frontmatter", "---\ndescription: Missing end\nalwaysApply: true",
            "---\ndescription: Broken list\nglobs: [\nalwaysApply: true\n---\nWrong",
            "---\ndescription: Bad bool\nalwaysApply: yes\n---\nWrong",
            '---\ndescription: Bad quote\nglobs:\n  - "unterminated\nalwaysApply: false\n---\nWrong',
            "---\ndescription: Duplicate\nalwaysApply: false\nalwaysApply: true\n---\nWrong",
        )
        payload = {"hook_event_name": "PreToolUse", "tool_name": "Edit",
                   "tool_input": {"file_path": "backend/migrations/versions/x.py"}}
        for content in malformed:
            with self.subTest(content=content):
                (rules / "broken.mdc").write_text(content)
                self.assertEqual(self.attached_topics(payload, root=root), {"migrations"})
                self.assertEqual(self.run_hook({"hook_event_name": "SessionStart"}, root=root), "")

    def test_missing_rules_and_malformed_or_unsupported_payloads_fail_open(self):
        for raw in ("not json", "[]", "null", '"text"', '{"hook_event_name": "Unknown"}',
                    '{"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"path": "frontend/src/x.tsx"}}'):
            with self.subTest(raw=raw):
                self.assertEqual(self.run_hook(raw, raw=True), "")
        root = self.temporary_repo()
        (root / ".cursor/rules").rmdir()
        self.assertEqual(self.run_hook({"hook_event_name": "SessionStart"}, root=root), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
