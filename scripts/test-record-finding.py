"""Record-finding contracts; all GitHub calls are recorded by fakes."""

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def _load_record_finding():
    """Load record-finding.py through importlib.util.spec_from_file_location."""
    spec = importlib.util.spec_from_file_location(
        "record_finding", ROOT / "scripts" / "record-finding.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeGitHubClient:
    """Minimal GitHub client that records calls without network."""

    def __init__(self):
        self.calls = []
        self.issues = []  # list of dicts: number, state, body, labels
        self.comments = {}  # number -> list of {"body": str}

    def search(self, token):
        self.calls.append(("search", token))
        return [i for i in self.issues if i["state"].lower() == "open"]

    def get_issue(self, number):
        self.calls.append(("get_issue", number))
        for issue in self.issues:
            if issue["number"] == number:
                return issue
        return {"number": number, "state": "open", "body": "", "labels": ["bundle"]}

    def list_comments(self, number):
        self.calls.append(("list_comments", number))
        return self.comments.get(number, [])

    def comment(self, number, body):
        self.calls.append(("comment", number, body))
        self.comments.setdefault(number, []).append({"body": body})

    def create(self, title, body, labels):
        self.calls.append(("create", title, body, labels))
        issue = {
            "number": len(self.issues) + 1,
            "state": "open",
            "body": body,
            "labels": labels,
        }
        self.issues.append(issue)
        return issue

    def reopen(self, number):
        self.calls.append(("reopen", number))
        for issue in self.issues:
            if issue["number"] == number:
                issue["state"] = "open"

    def add_labels(self, number, labels):
        self.calls.append(("add_labels", number, labels))


class RecordFindingTests(unittest.TestCase):
    """Contract tests for record-finding."""

    def setUp(self):
        self.mod = _load_record_finding()
        self.client = FakeGitHubClient()
        self.output = io.StringIO()

    def _parse_line(self, text):
        """Parse a single finding line into (class_, screen_file, what, sha, pr)."""
        return self.mod.parse_finding_line(text)

    def test_valid_line_parses_correctly(self):
        line = "- [ ] AuthError | tenant-list:42 | missing tenant id field | abc123def456 | PR #726"
        result = self._parse_line(line)
        self.assertEqual(result["class_"], "AuthError")
        self.assertEqual(result["screen_file"], "tenant-list:42")
        self.assertEqual(result["what"], "missing tenant id field")
        self.assertEqual(result["sha"], "abc123def456")
        self.assertEqual(result["pr"], 726)

    def test_line_with_question_mark_class(self):
        line = "- [ ] ? | dashboard:10 | unexpected null value | 1234567890abcdef | PR #700"
        result = self._parse_line(line)
        self.assertEqual(result["class_"], "?")

    def test_invalid_line_format_raises(self):
        line = "bad line format"
        with self.assertRaises(ValueError):
            self._parse_line(line)

    def test_duplicate_key_is_skipped(self):
        """A line whose key already occurs on the issue is not posted again."""
        issue_number = 1
        existing_key = "AuthError | tenant-list:42"
        # Pre-populate the issue with an existing comment carrying the key.
        self.client.comments[issue_number] = [
            {"body": f"- [ ] {existing_key} | something else | abc123def456 | PR #726"}
        ]
        self.client.issues.append(
            {"number": issue_number, "state": "open", "body": "", "labels": ["bundle"]}
        )

        # Record a finding with the same key but different line text.
        new_line = "- [ ] AuthError | tenant-list:43 | new observation | abc123def456 | PR #726"
        posted = self.mod.record_finding(
            self.client, issue_number, key=existing_key, line=new_line, output=self.output
        )
        self.assertFalse(posted)
        # Only one comment should exist (the pre-existing one).
        self.assertEqual(len(self.client.comments[issue_number]), 1)

    def test_empty_key_refused(self):
        """An empty key is refused."""
        issue_number = 1
        self.client.issues.append(
            {"number": issue_number, "state": "open", "body": "", "labels": ["bundle"]}
        )

        with self.assertRaises(ValueError):
            self.mod.record_finding(
                self.client, issue_number, key="", line="- [ ] ? | dash:1 | hi | abc1234567890 | PR #726",
                output=self.output,
            )

    def test_closed_issue_refused(self):
        """A closed issue (not open) is refused."""
        issue_number = 1
        self.client.issues.append(
            {"number": issue_number, "state": "closed", "body": "", "labels": ["bundle"]}
        )

        with self.assertRaises(ValueError):
            self.mod.record_finding(
                self.client, issue_number, key="AuthError | x:1",
                line="- [ ] AuthError | x:1 | hi | abc1234567890 | PR #726",
                output=self.output,
            )

    def test_issue_without_required_label_refused(self):
        """An issue without bundle, inbox or task label is refused."""
        issue_number = 1
        self.client.issues.append(
            {"number": issue_number, "state": "open", "body": "", "labels": ["feature"]}
        )

        with self.assertRaises(ValueError):
            self.mod.record_finding(
                self.client, issue_number, key="AuthError | x:1",
                line="- [ ] AuthError | x:1 | hi | abc1234567890 | PR #726",
                output=self.output,
            )

    def test_prints_counts_per_issue(self):
        """Print shows how many lines were posted and how many were already there."""
        issue_number = 1
        self.client.issues.append(
            {"number": issue_number, "state": "open", "body": "", "labels": ["bundle"]}
        )
        # Pre-populate with one comment (already there).
        self.client.comments[issue_number] = [{"body": "- [ ] AuthError | x:1 | old | abc1234567890 | PR #726"}]

        self.mod.record_finding(
            self.client, issue_number, key="AuthError | x:1",
            line="- [ ] AuthError | x:2 | new | abc1234567890 | PR #726", output=self.output
        )

        text = self.output.getvalue()
        self.assertIn("posted", text.lower())
        self.assertIn("already", text.lower())

    def test_real_line_with_space_in_class(self):
        """A real inbox line with a space in the class field parses."""
        line = "- [ ] flaky test | frontend vitest inside the Docker image build (scripts/Dockerfile:12) | 1 of 2100 tests failed | d4dc9099 | PR #652"
        result = self.mod.parse_finding_line(line)
        self.assertEqual(result["class_"], "flaky test")
        self.assertIn("Dockerfile:12", result["screen_file"])

    def test_real_line_with_space_in_screen(self):
        """A real inbox line with a space in the screen/file field parses."""
        line = "- [ ] ? | Einstellungen: AI card save | text | 38ed3469 | PR #654"
        result = self.mod.parse_finding_line(line)
        self.assertEqual(result["class_"], "?")
        self.assertEqual(result["what"], "text")

    def test_line_with_four_fields_raises(self):
        """A line with only four fields (missing one separator) raises ValueError."""
        line = "- [ ] AuthError | tenant-list:42 | abc123def456 | PR #726"
        with self.assertRaises(ValueError):
            self.mod.parse_finding_line(line)

    def test_line_without_checkbox_marker_raises(self):
        """A line without '- [ ] ' prefix raises ValueError."""
        line = "AuthError | tenant-list:42 | something | abc123def456 | PR #726"
        with self.assertRaises(ValueError):
            self.mod.parse_finding_line(line)

    def test_line_with_invalid_sha_raises(self):
        """A line whose SHA field is not hex raises ValueError."""
        line = "- [ ] AuthError | tenant-list:42 | something | xyz | PR #726"
        with self.assertRaises(ValueError):
            self.mod.parse_finding_line(line)

    def test_key_in_issue_body_not_posted(self):
        """The key occurs only in the issue body: not posted."""
        issue_number = 1
        self.client.issues.append(
            {"number": issue_number, "state": "open", "body": "tracked: missing tenant id",
             "labels": ["bundle"]}
        )

        posted = self.mod.record_finding(
            self.client, issue_number, key="missing tenant id",
            line="- [ ] ? | settings:1 | tracked | abc1234567890 | PR #700", output=self.output
        )
        self.assertFalse(posted)

    def test_key_in_second_line_of_two_line_comment_not_posted(self):
        """The key is a phrase in the 'what' field of the second line of a two-line comment: not posted."""
        issue_number = 1
        self.client.comments[issue_number] = [
            {"body": "- [ ] ? | dash:1 | first line | abc1234567890 | PR #700\n- [ ] ? | dash:1 | second line | abc1234567890 | PR #700"}
        ]
        self.client.issues.append(
            {"number": issue_number, "state": "open", "body": "", "labels": ["bundle"]}
        )

        posted = self.mod.record_finding(
            self.client, issue_number, key="dash:1",
            line="- [ ] ? | dash:2 | new line | abc1234567890 | PR #700", output=self.output
        )
        self.assertFalse(posted)

    def test_key_not_found_is_posted(self):
        """The key occurs nowhere: posted."""
        issue_number = 1
        self.client.issues.append(
            {"number": issue_number, "state": "open", "body": "", "labels": ["bundle"]}
        )
        self.client.comments[issue_number] = [
            {"body": "- [ ] ? | dash:1 | other key | abc1234567890 | PR #700"}
        ]

        posted = self.mod.record_finding(
            self.client, issue_number, key="new key",
            line="- [ ] ? | dash:2 | new line | abc1234567890 | PR #700", output=self.output
        )
        self.assertTrue(posted)
        comment_calls = [c for c in self.client.calls if c[0] == "comment"]
        self.assertEqual(comment_calls, [("comment", 1, "- [ ] ? | dash:2 | new line | abc1234567890 | PR #700")])
        self.assertEqual(self.output.getvalue().strip(), "Issue #1: 1 posted, 0 already there.")

    def test_list_comments_uses_api_with_pagination(self):
        """list_comments runs gh api with --paginate and --slurp, returns all pages."""
        def fake_run(argv, **kwargs):
            # Verify argv contains expected values
            self.assertIn("api", argv)
            self.assertIn("--method", argv)
            self.assertIn("GET", argv)
            self.assertIn("repos/itskeKs/verwalino/issues/651/comments", " ".join(argv))
            self.assertIn("--paginate", argv)
            self.assertIn("--slurp", argv)

            return subprocess.CompletedProcess(
                argv, 0,
                stdout=json.dumps([
                    [{"body": "a"}],
                    [{"body": "b"}],
                ]),
                stderr=""
            )

        with patch.object(self.mod.subprocess, "run", side_effect=fake_run):
            client = self.mod.GitHubClient()
            result = client.list_comments(651)

        self.assertEqual(len(result), 2)
        bodies = [c["body"] for c in result]
        self.assertIn("a", bodies)
        self.assertIn("b", bodies)

    def test_list_comments_patch_asserts_argv_and_result(self):
        """Same patch asserts exact argv of comment(), get_issue() and list_comments()."""
        call_args = []

        def fake_run(argv, **kwargs):
            call_args.append(list(argv))
            if "issue" in argv and "view" in argv:
                return subprocess.CompletedProcess(
                    argv, 0,
                    stdout=json.dumps({"number": 651, "body": "", "state": "open", "labels": []}),
                    stderr=""
                )
            elif "api" in argv and "GET" in argv:
                return subprocess.CompletedProcess(
                    argv, 0,
                    stdout=json.dumps([{"body": ""}]),
                    stderr=""
                )
            elif "api" in argv and "POST" in argv:
                return subprocess.CompletedProcess(
                    argv, 0, stdout="", stderr=""
                )
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

        with patch.object(self.mod.subprocess, "run", side_effect=fake_run):
            client = self.mod.GitHubClient()
            issue = client.get_issue(651)
            self.assertEqual(issue["number"], 651)

            comments = client.list_comments(651)
            self.assertEqual(comments, [{"body": ""}])

            client.comment(651, "a'b\"c\nd")

        # Verify get_issue exact argv
        get_issue_calls = [a for a in call_args if "issue" in a and "view" in a]
        self.assertEqual(get_issue_calls, [["gh", "issue", "view", "651", "-R", "itskeKs/verwalino", "--json", "body,labels,state,number"]])

        # Verify list_comments exact argv
        list_comments_calls = [a for a in call_args if "api" in a and "GET" in a]
        self.assertEqual(list_comments_calls, [["gh", "api", "--method", "GET", "repos/itskeKs/verwalino/issues/651/comments", "-f", "per_page=100", "--paginate", "--slurp"]])

        # Verify comment exact argv
        comment_calls = [a for a in call_args if "api" in a and "POST" in a]
        self.assertEqual(comment_calls, [["gh", "api", "--method", "POST", "repos/itskeKs/verwalino/issues/651/comments", "-f", "body=a'b\"c\nd"]])

    def test_file_two_new_rows_single_comment(self):
        """Two new rows for issue 651 produce exactly one comment with both lines."""
        with tempfile.TemporaryDirectory() as tmp:
            tsv_path = Path(tmp) / "findings.tsv"
            line1 = "- [ ] AuthError | x:1 | a | abc1234567890 | PR #700"
            line2 = "- [ ] PaymentError | y:2 | b | abc1234567890 | PR #700"
            tsv_path.write_text(f"651\tkey1\t{line1}\n651\tkey2\t{line2}\n")

            with patch.object(self.mod, "GitHubClient", return_value=self.client):
                with contextlib.redirect_stdout(io.StringIO()):
                    result = self.mod.main(["--file", str(tsv_path)])

            # Exactly one comment call with body exactly equal to "<line1>\n<line2>"
            comment_calls = [c for c in self.client.calls if c[0] == "comment"]
            self.assertEqual(len(comment_calls), 1)
            body = comment_calls[0][2]
            self.assertEqual(body, f"{line1}\n{line2}")

            # Exactly one get_issue and one list_comments for 651
            get_issue_calls = [c for c in self.client.calls if c[0] == "get_issue" and c[1] == 651]
            self.assertEqual(len(get_issue_calls), 1)
            list_comments_calls = [c for c in self.client.calls if c[0] == "list_comments" and c[1] == 651]
            self.assertEqual(len(list_comments_calls), 1)

    def test_file_two_identical_rows_one_comment(self):
        """Two identical rows in one file produce one comment holding the line once."""
        with tempfile.TemporaryDirectory() as tmp:
            tsv_path = Path(tmp) / "findings.tsv"
            tsv_path.write_text("651\tkey1\t- [ ] AuthError | x:1 | a | abc1234567890 | PR #700\n651\tkey1\t- [ ] AuthError | x:1 | a | abc1234567890 | PR #700\n")

            with patch.object(self.mod, "GitHubClient", return_value=self.client):
                with contextlib.redirect_stdout(io.StringIO()):
                    result = self.mod.main(["--file", str(tsv_path)])

            comment_calls = [c for c in self.client.calls if c[0] == "comment"]
            self.assertEqual(len(comment_calls), 1)

    def test_file_two_issues_one_comment_each(self):
        """Rows for two issues produce one comment per issue."""
        with tempfile.TemporaryDirectory() as tmp:
            tsv_path = Path(tmp) / "findings.tsv"
            tsv_path.write_text("651\tkey1\t- [ ] AuthError | x:1 | a | abc1234567890 | PR #700\n652\tkey2\t- [ ] PaymentError | y:2 | b | abc1234567890 | PR #700\n")

            with patch.object(self.mod, "GitHubClient", return_value=self.client):
                with contextlib.redirect_stdout(io.StringIO()):
                    result = self.mod.main(["--file", str(tsv_path)])

            comment_calls = [c for c in self.client.calls if c[0] == "comment"]
            self.assertEqual(len(comment_calls), 2)

    def test_prints_counts_per_issue_exact(self):
        """Print shows exact text Issue #1: 0 posted, 1 already there."""
        issue_number = 1
        self.client.issues.append(
            {"number": issue_number, "state": "open", "body": "", "labels": ["bundle"]}
        )
        # Pre-populate with one comment (already there).
        self.client.comments[issue_number] = [{"body": "- [ ] AuthError | x:1 | old | abc1234567890 | PR #726"}]

        self.mod.record_finding(
            self.client, issue_number, key="AuthError | x:1",
            line="- [ ] AuthError | x:2 | new | abc1234567890 | PR #726", output=self.output
        )

        text = self.output.getvalue()
        self.assertEqual(text.strip(), "Issue #1: 0 posted, 1 already there.")

    def test_file_prints_counts_per_issue(self):
        """File mode prints one total for all issues."""
        with tempfile.TemporaryDirectory() as tmp:
            tsv_path = Path(tmp) / "findings.tsv"
            # One new row and one already-present row for issue 651.
            tsv_path.write_text("651\tkey1\t- [ ] AuthError | x:1 | a | abc1234567890 | PR #700\n651\tkey1\t- [ ] AuthError | x:1 | a | abc1234567890 | PR #700\n")

            with patch.object(self.mod, "GitHubClient", return_value=self.client):
                captured = io.StringIO()
                with patch("sys.stdout", captured):
                    result = self.mod.main(["--file", str(tsv_path)])

            output_text = captured.getvalue()
            self.assertIn("Issue #651: 1 posted, 1 already there", output_text)

    def test_cli_processes_single_key_line(self):
        """CLI with --key and --line processes a single finding."""
        issue_number = 1
        self.client.calls.clear()
        self.client.issues.append(
            {"number": issue_number, "state": "open", "body": "", "labels": ["bundle"]}
        )

        captured = io.StringIO()
        with patch.object(self.mod, "GitHubClient", return_value=self.client):
            with contextlib.redirect_stdout(captured):
                result = self.mod.main(
                    [str(issue_number), "--key", "AuthError | x:1",
                     "--line", "- [ ] AuthError | x:2 | hi | abc1234567890 | PR #726"]
                )
        self.assertEqual(result, 0)
        comment_calls = [c for c in self.client.calls if c[0] == "comment"]
        self.assertEqual(comment_calls, [("comment", 1, "- [ ] AuthError | x:2 | hi | abc1234567890 | PR #726")])
        self.assertEqual(captured.getvalue().strip(), "Issue #1: 1 posted, 0 already there.")

    def test_cli_refuses_invalid_line(self):
        """CLI refuses a line that does not match the format."""
        issue_number = 1
        self.client.issues.append(
            {"number": issue_number, "state": "open", "body": "", "labels": ["bundle"]}
        )

        with patch.object(self.mod, "GitHubClient", return_value=self.client):
            with contextlib.redirect_stdout(io.StringIO()):
                result = self.mod.main(
                    [str(issue_number), "--key", "AuthError | x:1",
                     "--line", "bad line"]
                )
        self.assertEqual(result, 1)

    def test_cli_processes_file_tsv(self):
        """CLI with --file reads several rows from a TSV."""
        issue_number = 1
        self.client.issues.append(
            {"number": issue_number, "state": "open", "body": "", "labels": ["bundle"]}
        )

        with tempfile.TemporaryDirectory() as tmp:
            tsv_path = Path(tmp) / "findings.tsv"
            tsv_path.write_text("1\tAuthError | x:1\t- [ ] AuthError | x:1 | a | abc1234567890 | PR #726\n1\tPaymentError | y:2\t- [ ] PaymentError | y:2 | b | abc1234567890 | PR #726\n")

            with patch.object(self.mod, "GitHubClient", return_value=self.client):
                with contextlib.redirect_stdout(io.StringIO()):
                    result = self.mod.main(
                        ["--file", str(tsv_path)]
                    )
        self.assertEqual(result, 0)

    def test_cli_header_row_refused(self):
        """A file whose first row is 'issue<TAB>key<TAB>line' followed by one valid row returns 1."""
        with tempfile.TemporaryDirectory() as tmp:
            tsv_path = Path(tmp) / "findings.tsv"
            tsv_path.write_text("issue\tkey\tline\n1\tAuthError | x:1\t- [ ] AuthError | x:1 | a | abc1234567890 | PR #726\n")

            with patch.object(self.mod, "GitHubClient", return_value=self.client):
                result = self.mod.main(["--file", str(tsv_path)])

            self.assertEqual(result, 1)
            # No gh calls at all.
            self.assertEqual(len(self.client.calls), 0)

    def test_file_three_rows_per_issue_count(self):
        """Three rows: two for 651 (one duplicate key), one for 652; per-issue 'already there' is correct."""
        with tempfile.TemporaryDirectory() as tmp:
            tsv_path = Path(tmp) / "findings.tsv"
            line_a = "- [ ] AuthError | x:1 | a | abc1234567890 | PR #700"
            line_b = "- [ ] PaymentError | y:2 | b | abc1234567890 | PR #700"
            tsv_path.write_text(f"651\tk1\t{line_a}\n651\tk1\t{line_a}\n652\tk2\t{line_b}\n")

            fake_client = FakeGitHubClient()
            fake_client.issues.append({"number": 651, "state": "open", "body": "", "labels": ["bundle"]})
            fake_client.issues.append({"number": 652, "state": "open", "body": "", "labels": ["bundle"]})

            output = io.StringIO()
            with patch.object(self.mod, "GitHubClient", return_value=fake_client):
                with contextlib.redirect_stdout(output):
                    result = self.mod.main(["--file", str(tsv_path)])

            self.assertEqual(result, 0)
            # Issue #651: 1 posted, 1 already there (duplicate k1 within file).
            # Issue #652: 1 posted, 0 already there (no duplicates).
            lines = output.getvalue().strip().split("\n")
            self.assertEqual(len(lines), 2)
            self.assertIn("Issue #651: 1 posted, 1 already there.", lines)
            self.assertIn("Issue #652: 1 posted, 0 already there.", lines)

    def test_file_key_in_comment_not_posted(self):
        """(a) issue 651 has a comment 'foo x:1 bar'; file has row 651<TAB>x:1<TAB><valid line>; no comment call."""
        with tempfile.TemporaryDirectory() as tmp:
            tsv_path = Path(tmp) / "findings.tsv"
            line_text = "- [ ] AuthError | x:1 | a | abc1234567890 | PR #700"
            tsv_path.write_text(f"651\tx:1\t{line_text}\n")

            fake_client = FakeGitHubClient()
            fake_client.issues.append({"number": 651, "state": "open", "body": "", "labels": ["bundle"]})
            fake_client.comments[651] = [{"body": "foo x:1 bar"}]

            output = io.StringIO()
            with patch.object(self.mod, "GitHubClient", return_value=fake_client):
                with contextlib.redirect_stdout(output):
                    result = self.mod.main(["--file", str(tsv_path)])

            self.assertEqual(result, 0)
            comment_calls = [c for c in fake_client.calls if c[0] == "comment"]
            self.assertEqual(len(comment_calls), 0)
            self.assertIn("Issue #651: 0 posted, 1 already there.", output.getvalue())

    def test_file_key_in_body_not_posted(self):
        """(b) issue 651 body contains 'x:1', no comments; same row; no comment call."""
        with tempfile.TemporaryDirectory() as tmp:
            tsv_path = Path(tmp) / "findings.tsv"
            line_text = "- [ ] AuthError | x:1 | a | abc1234567890 | PR #700"
            tsv_path.write_text(f"651\tx:1\t{line_text}\n")

            fake_client = FakeGitHubClient()
            fake_client.issues.append({"number": 651, "state": "open", "body": "some text x:1 more", "labels": ["bundle"]})

            output = io.StringIO()
            with patch.object(self.mod, "GitHubClient", return_value=fake_client):
                with contextlib.redirect_stdout(output):
                    result = self.mod.main(["--file", str(tsv_path)])

            self.assertEqual(result, 0)
            comment_calls = [c for c in fake_client.calls if c[0] == "comment"]
            self.assertEqual(len(comment_calls), 0)
            self.assertIn("Issue #651: 0 posted, 1 already there.", output.getvalue())

    def test_file_one_comment_posted(self):
        """(c) issue 651 has one comment containing 'x:1'; file has two rows; exactly one comment call."""
        with tempfile.TemporaryDirectory() as tmp:
            tsv_path = Path(tmp) / "findings.tsv"
            line_a = "- [ ] AuthError | x:1 | a | abc1234567890 | PR #700"
            line_b = "- [ ] PaymentError | y:2 | b | abc1234567890 | PR #700"
            tsv_path.write_text(f"651\tx:1\t{line_a}\n651\ty:2\t{line_b}\n")

            fake_client = FakeGitHubClient()
            fake_client.issues.append({"number": 651, "state": "open", "body": "", "labels": ["bundle"]})
            fake_client.comments[651] = [{"body": "foo x:1 bar"}]

            output = io.StringIO()
            with patch.object(self.mod, "GitHubClient", return_value=fake_client):
                with contextlib.redirect_stdout(output):
                    result = self.mod.main(["--file", str(tsv_path)])

            self.assertEqual(result, 0)
            comment_calls = [c for c in fake_client.calls if c[0] == "comment"]
            self.assertEqual(len(comment_calls), 1)
            self.assertEqual(comment_calls[0][2], line_b)
            self.assertIn("Issue #651: 1 posted, 1 already there.", output.getvalue())

    def test_file_closed_bundle_issue_refused(self):
        """(d) issue 7 is closed and labelled 'bundle': refused, exit 1, no comment on 7."""
        with tempfile.TemporaryDirectory() as tmp:
            tsv_path = Path(tmp) / "findings.tsv"
            line_text = "- [ ] AuthError | x:1 | a | abc1234567890 | PR #700"
            tsv_path.write_text(f"7\tkey1\t{line_text}\n")

            fake_client = FakeGitHubClient()
            fake_client.issues.append({"number": 7, "state": "closed", "body": "", "labels": ["bundle"]})

            output = io.StringIO()
            with patch.object(self.mod, "GitHubClient", return_value=fake_client):
                with contextlib.redirect_stdout(output):
                    result = self.mod.main(["--file", str(tsv_path)])

            self.assertEqual(result, 1)
            comment_on_7 = [c for c in fake_client.calls if c[0] == "comment" and c[1] == 7]
            self.assertEqual(len(comment_on_7), 0)

    def test_file_open_feature_only_refused(self):
        """(e) issue 7 is open and labelled 'feature' only: refused, exit 1, no comment on 7."""
        with tempfile.TemporaryDirectory() as tmp:
            tsv_path = Path(tmp) / "findings.tsv"
            line_text = "- [ ] AuthError | x:1 | a | abc1234567890 | PR #700"
            tsv_path.write_text(f"7\tkey1\t{line_text}\n")

            fake_client = FakeGitHubClient()
            fake_client.issues.append({"number": 7, "state": "open", "body": "", "labels": ["feature"]})

            output = io.StringIO()
            with patch.object(self.mod, "GitHubClient", return_value=fake_client):
                with contextlib.redirect_stdout(output):
                    result = self.mod.main(["--file", str(tsv_path)])

            self.assertEqual(result, 1)
            comment_on_7 = [c for c in fake_client.calls if c[0] == "comment" and c[1] == 7]
            self.assertEqual(len(comment_on_7), 0)

    def test_file_bad_line_exits_1_no_gh_calls(self):
        """A file with one valid row and one 'bad line' exits 1 and no gh calls."""
        with tempfile.TemporaryDirectory() as tmp:
            tsv_path = Path(tmp) / "findings.tsv"
            tsv_path.write_text("651\tkey1\t- [ ] AuthError | x:1 | a | abc1234567890 | PR #700\n652\tkey2\tbad line\n")

            with patch.object(self.mod, "GitHubClient", return_value=self.client):
                result = self.mod.main(["--file", str(tsv_path)])

            self.assertEqual(result, 1)
            # No gh calls at all (no get_issue, no list_comments, no comment).
            self.assertEqual(len(self.client.calls), 0)

    def test_file_two_column_row_exits_1(self):
        """A row with only two columns is refused and exits 1."""
        with tempfile.TemporaryDirectory() as tmp:
            tsv_path = Path(tmp) / "findings.tsv"
            tsv_path.write_text("651\tkey1\n")

            with patch.object(self.mod, "GitHubClient", return_value=self.client):
                result = self.mod.main(["--file", str(tsv_path)])

            self.assertEqual(result, 1)
            self.assertEqual(len(self.client.calls), 0)

    def test_file_issue_column_with_hash_exits_1(self):
        """A row with issue column '#651' (starts with #) is refused."""
        with tempfile.TemporaryDirectory() as tmp:
            tsv_path = Path(tmp) / "findings.tsv"
            tsv_path.write_text("#651\tkey1\t- [ ] AuthError | x:1 | a | abc1234567890 | PR #700\n")

            with patch.object(self.mod, "GitHubClient", return_value=self.client):
                result = self.mod.main(["--file", str(tsv_path)])

            self.assertEqual(result, 1)
            self.assertEqual(len(self.client.calls), 0)

    def test_file_issue_column_non_digits_exits_1(self):
        """A row with issue column 'x651' (non-digits) exits 1."""
        with tempfile.TemporaryDirectory() as tmp:
            tsv_path = Path(tmp) / "findings.tsv"
            tsv_path.write_text("x651\tkey1\t- [ ] AuthError | x:1 | a | abc1234567890 | PR #700\n")

            with patch.object(self.mod, "GitHubClient", return_value=self.client):
                result = self.mod.main(["--file", str(tsv_path)])

            self.assertEqual(result, 1)
            self.assertEqual(len(self.client.calls), 0)

    def test_file_empty_key_exits_1(self):
        """A row with key of only spaces is refused."""
        with tempfile.TemporaryDirectory() as tmp:
            tsv_path = Path(tmp) / "findings.tsv"
            tsv_path.write_text("651\t   \t- [ ] AuthError | x:1 | a | abc1234567890 | PR #700\n")

            with patch.object(self.mod, "GitHubClient", return_value=self.client):
                result = self.mod.main(["--file", str(tsv_path)])

            self.assertEqual(result, 1)
            self.assertEqual(len(self.client.calls), 0)

    def test_file_closed_issue_and_valid_row_exits_1(self):
        """A file with one row for a closed issue 7 and one valid row for open bundle issue 651."""
        with tempfile.TemporaryDirectory() as tmp:
            tsv_path = Path(tmp) / "findings.tsv"
            tsv_path.write_text("7\tkey1\t- [ ] AuthError | x:1 | a | abc1234567890 | PR #700\n651\tkey2\t- [ ] PaymentError | y:2 | b | abc1234567890 | PR #700\n")

            fake_client = FakeGitHubClient()
            fake_client.issues.append({"number": 7, "state": "closed", "body": "", "labels": ["feature"]})
            fake_client.issues.append({"number": 651, "state": "open", "body": "", "labels": ["bundle"]})

            with patch.object(self.mod, "GitHubClient", return_value=fake_client):
                result = self.mod.main(["--file", str(tsv_path)])

            self.assertEqual(result, 1)
            # No comment on issue 7 (closed).
            comment_on_7 = [c for c in fake_client.calls if c[0] == "comment" and c[1] == 7]
            self.assertEqual(len(comment_on_7), 0)
            # One comment on issue 651.
            comment_on_651 = [c for c in fake_client.calls if c[0] == "comment" and c[1] == 651]
            self.assertEqual(len(comment_on_651), 1)

    def test_file_gh_error_exits_1(self):
        """A gh error on the second issue raises exit 1, but other issues are still processed."""
        with tempfile.TemporaryDirectory() as tmp:
            tsv_path = Path(tmp) / "findings.tsv"
            tsv_path.write_text("651\tkey1\t- [ ] AuthError | x:1 | a | abc1234567890 | PR #700\n9\tkey2\t- [ ] PaymentError | y:2 | b | abc1234567890 | PR #700\n")

            fake_client = FakeGitHubClient()
            fake_client.issues.append({"number": 651, "state": "open", "body": "", "labels": ["bundle"]})
            fake_client.issues.append({"number": 9, "state": "open", "body": "", "labels": ["bundle"]})

            def fake_get_issue(number):
                if number == 9:
                    raise subprocess.CalledProcessError(1, ["gh", "issue", "view", str(number)])
                # Delegate to the original get_issue method.
                return FakeGitHubClient.get_issue(fake_client, number)

            fake_client.get_issue = fake_get_issue

            with patch.object(self.mod, "GitHubClient", return_value=fake_client):
                result = self.mod.main(["--file", str(tsv_path)])

            self.assertEqual(result, 1)
            # Issue 651 was still processed (one comment).
            comment_on_651 = [c for c in fake_client.calls if c[0] == "comment" and c[1] == 651]
            self.assertEqual(len(comment_on_651), 1)

    def test_single_key_of_spaces_refused(self):
        """Single form --key/--line with key of only spaces is refused."""
        issue_number = 1
        self.client.issues.append(
            {"number": issue_number, "state": "open", "body": "", "labels": ["bundle"]}
        )

        with patch.object(self.mod, "GitHubClient", return_value=self.client):
            with contextlib.redirect_stdout(io.StringIO()):
                result = self.mod.main(
                    [str(issue_number), "--key", "   ",
                     "--line", "- [ ] AuthError | x:1 | hi | abc1234567890 | PR #726"]
                )
        self.assertEqual(result, 1)
        self.assertEqual(len(self.client.calls), 0)

    def test_single_closed_issue_refused(self):
        """Single form with a closed issue returns 1, prints refusal, no comment call."""
        self.client.issues.append(
            {"number": 7, "state": "closed", "body": "", "labels": ["bundle"]}
        )

        captured = io.StringIO()
        with patch.object(self.mod, "GitHubClient", return_value=self.client):
            with contextlib.redirect_stdout(captured):
                result = self.mod.main(
                    ["7", "--key", "k",
                     "--line", "- [ ] ? | x:1 | a | abc1234 | PR #700"]
                )

        self.assertEqual(result, 1)
        comment_calls = [c for c in self.client.calls if c[0] == "comment"]
        self.assertEqual(len(comment_calls), 0)

    def test_single_issue_without_required_label_refused(self):
        """Single form with an open issue lacking bundle/inbox/task returns 1, no comment call."""
        self.client.issues.append(
            {"number": 7, "state": "open", "body": "", "labels": ["feature"]}
        )

        captured = io.StringIO()
        with patch.object(self.mod, "GitHubClient", return_value=self.client):
            with contextlib.redirect_stdout(captured):
                result = self.mod.main(
                    ["7", "--key", "k",
                     "--line", "- [ ] ? | x:1 | a | abc1234 | PR #700"]
                )

        self.assertEqual(result, 1)
        comment_calls = [c for c in self.client.calls if c[0] == "comment"]
        self.assertEqual(len(comment_calls), 0)


if __name__ == "__main__":
    import contextlib

    unittest.main()
