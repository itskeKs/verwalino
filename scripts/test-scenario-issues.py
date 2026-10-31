"""Scenario filing contracts; all GitHub calls are recorded by fakes."""
from copy import deepcopy
import contextlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import scenario_issues as reporter


ROOT = Path(__file__).resolve().parents[1]
RUN = "https://github.com/example/project/actions/runs/101"


def scenario(**changes):
    return {
        "feature": "Bank statement import", "scenario": "Importing a statement adds entries",
        "tags": [], "outcome": "failed", "error": None,
        "steps": [{"keyword": "Then", "name": "the entries are visible", "outcome": "failed",
                   "error": {"type": "builtins.AssertionError", "message": "expected 2 entries, got 0"}}],
        "artifacts": ["artifacts/abc/last-response.json"], **changes,
    }


def report(*rows):
    return {"run": {"started": "2026-10-30T03:00:00+00:00", "finished": "2026-10-30T03:01:00+00:00",
                    "mode": "selfhost", "git_sha": "a" * 40}, "scenarios": list(rows)}


class FakeGitHubClient:
    def __init__(self):
        self.calls = []
        self.issues = []
        self.comments = {}

    def search(self, token):
        self.calls.append(("search", token))
        # Deliberately fuzzy, like GitHub search: the reporter must match the marker.
        return deepcopy(self.issues)

    def create(self, title, body, labels):
        self.calls.append(("create", title, body, labels))
        issue = {"number": len(self.issues) + 1, "state": "open", "body": body, "labels": labels}
        self.issues.append(issue)
        return deepcopy(issue)

    def list_comments(self, number):
        self.calls.append(("list_comments", number))
        return deepcopy(self.comments.get(number, []))

    def comment(self, number, body):
        self.calls.append(("comment", number, body))
        self.comments.setdefault(number, []).append({"body": body})

    def reopen(self, number):
        self.calls.append(("reopen", number))
        self.issues[number - 1]["state"] = "open"

    def add_labels(self, number, labels):
        self.calls.append(("add_labels", number, labels))
        self.issues[number - 1]["labels"].extend(labels)


class ScenarioIssueTests(unittest.TestCase):
    def setUp(self):
        self.client = FakeGitHubClient()
        self.output = io.StringIO()

    def file(self, data, run=RUN, **kwargs):
        return reporter.file_failures(data, self.client, run_url=run, root=ROOT, output=self.output, **kwargs)

    def test_canary_fingerprint_label_and_open_issue_deduplication(self):
        row = scenario(feature="canary", scenario="Hosted canary", tags=["canary"],
                       steps=[{"name": "tenant-ledger", "outcome": "failed", "error": {"type": "HTTP503"}}])
        self.assertEqual(reporter.fingerprint(row, row["steps"][0])[0], "canary::tenant-ledger::HTTP503")
        self.file(report(row))
        self.file(report(row), run=RUN + "2")
        self.assertEqual(len(self.client.issues), 1)
        issue = self.client.issues[0]
        self.assertIn("canary", issue["labels"])
        self.assertIn("scripts/canary.py", issue["body"])
        self.assertNotIn("make test-scenarios-report", issue["body"])
        self.assertEqual(len(self.client.comments[1]), 1)

    def test_structured_exception_class_ignores_volatile_messages_but_step_changes_identity(self):
        first = scenario()
        second = deepcopy(first)
        second["steps"][0]["error"]["message"] = "request c78a: /tmp/new-444.json at 2027-01-01"
        self.assertEqual(reporter.fingerprint(first, first["steps"][0]),
                         reporter.fingerprint(second, second["steps"][0]))
        second["steps"][0]["name"] = "the balance is visible"
        self.assertNotEqual(reporter.fingerprint(first, first["steps"][0]),
                            reporter.fingerprint(second, second["steps"][0]))

    def test_fallback_normalizes_ids_paths_timestamps_and_numbers(self):
        first = scenario()
        second = deepcopy(first)
        first["steps"][0]["error"] = {"message": (
            "request_id=req-ABCz id=opaqueX 5bf53bb8-1f9e-4aee-a09a-a47a3ae40e34 "
            "at 2026-10-30T03:12:34.123Z /tmp/run-123/a.json count 43\ntrace one")}
        second["steps"][0]["error"] = {"message": (
            "request_id=req-QWER id=opaqueY aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee "
            "at 2027-11-09T07:23:45+01:00 C:\\runs\\different.txt count 199\ntrace two")}
        self.assertEqual(reporter.fingerprint(first, first["steps"][0]),
                         reporter.fingerprint(second, second["steps"][0]))
        second["steps"][0]["error"] = {"message": "permission denied"}
        self.assertNotEqual(reporter.fingerprint(first, first["steps"][0]),
                            reporter.fingerprint(second, second["steps"][0]))

    def test_fallback_normalizes_relative_paths_and_space_separated_request_ids(self):
        first = scenario()
        second = deepcopy(first)
        first["steps"][0]["error"] = "request id req-ABC failed in backend/app/old.py:43 at 03:12:34"
        second["steps"][0]["error"] = "request id req-XYZ failed in reports/new.csv:67 at 11:45:59"
        self.assertEqual(reporter.fingerprint(first, first["steps"][0]),
                         reporter.fingerprint(second, second["steps"][0]))

    def test_new_failure_uses_template_marker_first_run_artifacts_and_reproduction(self):
        self.file(report(scenario()))
        issue, = self.client.issues
        fingerprint, token = reporter.fingerprint(scenario(), scenario()["steps"][0])
        body = issue["body"]
        self.assertIn(f"<!-- scenario-fingerprint: {token} -->", body.splitlines())
        self.assertIn(f"Fingerprint: `{fingerprint}`", body)
        for text in ("## What failed", "## Where", "## Reproduce", "## Acceptance",
                     "Bank statement import", "features/bank_statement_import.feature",
                     "Importing a statement adds entries", "Then the entries are visible",
                     "expected 2 entries, got 0", "First seen run:", RUN, "2026-10-30",
                     "mode: selfhost", "scenarios-selfhost-2026-10-30", "scenarios.json", "summary.md",
                     "artifacts/abc/last-response.json", "make test-scenarios-report", "importing_a_statement_adds_entries"):
            self.assertIn(text, body)
        self.assertNotIn("<scenario name>", body)
        self.assertEqual(issue["labels"], ["scenario"])

    def test_long_failure_keeps_head_and_tail_with_a_truncation_marker_inside_a_code_block(self):
        row = scenario(scenario="Importing a statement " + "with a long description " * 20)
        message = "A long response follows:\n```\n" + "entry=123 details " * 5000 + "\n```\nresponse tail"
        row["steps"][0]["error"]["message"] = message
        self.file(report(row))
        create = next(call for call in self.client.calls if call[0] == "create")
        self.assertLessEqual(len(create[1]), 240)
        self.assertLessEqual(len(create[2]), 65_536)
        self.assertNotIn(message, create[2])
        self.assertIn(message[:9000], create[2])
        self.assertIn(message[-9000:], create[2])
        omitted = len(reporter.error_text(row["steps"][0]["error"])) - reporter.ERROR_TEXT_LIMIT
        self.assertIn(f"\n… [truncated {omitted} characters; full text in the run artifact]\n", create[2])
        self.assertIn("````text\nbuiltins.AssertionError:", create[2])
        self.assertIn(row["scenario"], create[2])

    def test_error_text_at_the_limit_is_kept_in_full_and_one_extra_character_is_truncated(self):
        self.assertEqual(reporter.code_block("x" * 20_000), "```text\n" + "x" * 20_000 + "\n```")
        self.assertIn("\n… [truncated 1 characters; full text in the run artifact]\n",
                      reporter.code_block("x" * 20_001))

    def test_long_changed_error_comments_are_bounded_and_compare_the_full_error(self):
        self.file(report(scenario()))
        row = scenario()
        row["steps"][0]["error"]["message"] = "head\n" + "x" * 80_000 + "\ntail"
        self.file(report(row))
        body = self.client.comments[1][-1]["body"]
        self.assertLessEqual(len(body), 65_536)
        self.assertIn("head\n", body)
        self.assertIn("\ntail", body)
        self.assertIn("full text in the run artifact", body)
        message = row["steps"][0]["error"]["message"]
        row["steps"][0]["error"]["message"] = message[:40_000] + "y" + message[40_001:]
        self.file(report(row))
        self.assertIn("Step error changed:", self.client.comments[1][-1]["body"])
        self.assertNotEqual(body, self.client.comments[1][-1]["body"])
        self.file(report(row))
        self.assertNotIn("Step error changed:", self.client.comments[1][-1]["body"])

    def test_same_failure_across_runs_keeps_one_issue_and_two_recurrence_comments(self):
        self.file(report(scenario()))
        for number in (102, 103):
            self.file(report(scenario()), RUN.replace("101", str(number)))
        self.assertEqual(len(self.client.issues), 1)
        comments = self.client.comments[1]
        self.assertEqual(len(comments), 2)
        for comment, number in zip(comments, (102, 103)):
            self.assertIn(f"Failed again in run {RUN.replace('101', str(number))} (2026-10-30, mode selfhost)", comment["body"])
            self.assertNotIn("expected 2 entries", comment["body"])

    def test_changed_step_error_is_included_once_then_compared_to_latest_comment(self):
        self.file(report(scenario()))
        changed = scenario()
        changed["steps"][0]["error"]["message"] = "expected 2 entries, got 1"
        self.file(report(changed), RUN.replace("101", "102"))
        self.file(report(changed), RUN.replace("101", "103"))
        self.assertIn("expected 2 entries, got 1", self.client.comments[1][0]["body"])
        self.assertNotIn("expected 2 entries", self.client.comments[1][1]["body"])

    def test_passing_does_nothing_and_closed_failure_reopens_as_regression(self):
        self.file(report(scenario()))
        self.client.issues[0]["state"] = "closed"
        self.client.calls.clear()
        self.file(report(scenario(outcome="passed")))
        self.assertEqual(self.client.calls, [])
        self.file(report(scenario()), RUN.replace("101", "102"))
        self.assertEqual(self.client.issues[0]["state"], "open")
        self.assertIn("regression", self.client.issues[0]["labels"])
        self.assertIn(("reopen", 1), self.client.calls)
        self.assertIn(("add_labels", 1, ["regression"]), self.client.calls)
        self.assertIn("102", self.client.comments[1][0]["body"])
        self.assertEqual(len(self.client.issues), 1)

    def test_variants_deduplicate_before_search_and_combine_artifact_names(self):
        browser = scenario(tags=["browser"], artifacts=["artifacts/def/trace.zip"])
        self.file(report(scenario(), browser, *[scenario() for _ in range(100)]))
        self.assertEqual(sum(call[0] == "search" for call in self.client.calls), 1)
        self.assertEqual(len(self.client.issues), 1)
        self.assertIn("artifacts/def/trace.zip", self.client.issues[0]["body"])
        self.assertIn("artifacts/abc/last-response.json", self.client.issues[0]["body"])

    def test_fuzzy_results_do_not_win_over_exact_marker_and_unrelated_issue_is_ignored(self):
        self.file(report(scenario()))
        actual = deepcopy(self.client.issues[0])
        self.client.issues.insert(0, {"number": 55, "state": "open", "body": "scenario-fingerprint: fuzzy"})
        self.file(report(scenario()), RUN.replace("101", "102"))
        self.assertEqual(len(self.client.issues), 2)
        self.assertEqual(len(self.client.comments[actual["number"]]), 1)
        self.assertNotIn(55, self.client.comments)
        different = scenario(scenario="Another scenario")
        self.file(report(different))
        self.assertEqual(len(self.client.issues), 3)

    def test_known_flaky_and_skipped_scenarios_are_excluded_from_search(self):
        self.file(report(scenario(tags=["known-flaky"]), scenario(tags=["@known-flaky"]), scenario(outcome="skipped")))
        self.assertEqual(self.client.calls, [])

    def test_feature_area_tag_is_used_but_scenario_area_tag_is_not(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "features").mkdir()
            (root / ".github/ISSUE_TEMPLATE").mkdir(parents=True)
            (root / ".github/ISSUE_TEMPLATE/scenario-failure.md").write_text(
                (ROOT / ".github/ISSUE_TEMPLATE/scenario-failure.md").read_text())
            (root / "features/import.feature").write_text(
                "# A feature-level area\n@area:bank @browser\nFeature: Bank statement import\n"
                "  @area:other\n  Scenario: Importing a statement adds entries\n")
            reporter.file_failures(report(scenario()), self.client, run_url=RUN, root=root, output=self.output)
            self.assertEqual(self.client.issues[0]["labels"], ["scenario", "area:bank"])
        self.client = FakeGitHubClient()
        self.file(report(scenario(tags=["area:other"])))
        self.assertEqual(self.client.issues[0]["labels"], ["scenario"])

    def test_setup_failure_without_failed_step_uses_scenario_error(self):
        row = scenario(steps=[], error={"type": "RuntimeError", "message": "fixture setup failed"})
        self.file(report(row))
        self.assertIn("fixture setup failed", self.client.issues[0]["body"])
        self.assertIn("setup/teardown", self.client.issues[0]["body"])

    def test_outline_reproduction_selects_the_source_template_name(self):
        self.file(report(scenario(feature="Organization isolation", scenario="A user sees only the properties of their own organization")))
        self.assertIn("a_user_sees_only_the_records_of_their_own_organization", self.client.issues[0]["body"])
        self.assertNotIn("-k a_user_sees_only_the_properties", self.client.issues[0]["body"])

    def test_reproduction_uses_pytest_names_for_punctuation_and_leading_digits(self):
        self.file(report(scenario(scenario="123 A tenant's co-owner (again)")))
        self.assertIn("-k a_tenants_coowner_again", self.client.issues[0]["body"])

    def test_dry_run_plans_new_open_and_closed_without_any_writes(self):
        self.file(report(scenario()), dry_run=True)
        self.assertIn("create", self.output.getvalue())
        self.assertEqual([call[0] for call in self.client.calls], ["search"])
        self.file(report(scenario()))
        for state in ("open", "closed"):
            self.client.issues[0]["state"] = state
            self.client.calls.clear()
            self.file(report(scenario()), dry_run=True)
            self.assertTrue(set(call[0] for call in self.client.calls) <= {"search", "list_comments"})
        self.assertIn("reopen", self.output.getvalue())

    def test_new_issues_are_capped_with_one_summary_while_comments_and_reopenings_are_unlimited(self):
        existing = [scenario(scenario=f"Existing failure {number}") for number in range(12)]
        self.file(report(*existing[:10]))
        self.file(report(*existing[10:]))
        for issue in self.client.issues[:6]:
            issue["state"] = "closed"
        self.client.calls.clear()
        new = [scenario(scenario=f"New failure {number}") for number in range(12)]
        run = RUN.replace("101", "102")
        plan = self.file(report(*new, *existing), run)
        creates = [action for action in plan if action["action"] == "create"]
        self.assertEqual(len(creates), 11)
        self.assertEqual([action["title"] for action in creates[:10]],
                         [f"Scenario failure: New failure {number}" for number in range(10)])
        summary = creates[-1]
        self.assertEqual(summary["title"], "Nightly scenarios: 2 further failures not filed (run 102)")
        self.assertEqual(summary["labels"], ["scenario"])
        self.assertIn(run, summary["body"])
        for row in new[10:]:
            fingerprint, token = reporter.fingerprint(row, row["steps"][0])
            for text in (row["scenario"], fingerprint, token):
                self.assertIn(text, summary["body"])
        self.assertEqual(len(self.client.issues), 23)
        self.assertEqual(sum(call[0] == "comment" for call in self.client.calls), 12)
        self.assertEqual(sum(call[0] == "reopen" for call in self.client.calls), 6)
        self.assertEqual(sum(call[0] == "add_labels" for call in self.client.calls), 6)
        self.assertTrue(all(issue["state"] == "open" for issue in self.client.issues))

    def test_exactly_ten_new_issues_need_no_summary(self):
        plan = self.file(report(*[scenario(scenario=f"Failure {number}") for number in range(10)]))
        self.assertEqual(len(plan), 10)
        self.assertTrue(all(action["title"].startswith("Scenario failure:") for action in plan))

    def test_dry_run_caps_distinct_failures_and_includes_the_summary_without_writes(self):
        rows = [scenario(scenario=f"Failure {number}") for number in range(11)]
        plan = self.file(report(*rows, rows[-1]), dry_run=True)
        self.assertEqual(len(plan), 11)
        self.assertEqual(plan[-1]["title"], "Nightly scenarios: 1 further failures not filed (run 101)")
        self.assertEqual(len(self.client.calls), 11)
        self.assertTrue(all(call[0] == "search" for call in self.client.calls))
        self.assertEqual(self.client.issues, [])

    def test_cli_reads_report_and_mode_override_and_dry_run_without_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scenarios.json"
            path.write_text(json.dumps(report(scenario())))
            with patch.object(reporter, "GitHubClient", return_value=self.client) as client, contextlib.redirect_stdout(self.output):
                self.assertEqual(reporter.main(["--report", str(path), "--repo", "example/project",
                                                "--run-url", RUN, "--mode", "selfhost", "--dry-run"]), 0)
            client.assert_called_once_with("example/project")
        self.assertEqual(self.client.issues, [])
        self.assertIn("create", self.output.getvalue())


class NightlyWorkflowTests(unittest.TestCase):
    def test_issue_writer_checkout_does_not_persist_credentials(self):
        workflow = (ROOT / ".github/workflows/nightly-scenarios.yaml").read_text()
        job = workflow.split("\n  scenarios:\n", 1)[1]
        self.assertIn("\n      issues: write\n", job)
        checkout = job.split("      - name: Checkout repository\n", 1)[1].split("\n      - name:", 1)[0]
        self.assertIn("uses: actions/checkout@v6", checkout)
        self.assertIn("\n        with:\n          persist-credentials: false", checkout)


class GitHubTransportTests(unittest.TestCase):
    def test_search_requests_respect_the_thirty_per_minute_limit(self):
        clock = [0.0]
        requests = []

        def sleep(seconds):
            clock[0] += seconds

        def respond(*args, **kwargs):
            requests.append(clock[0])
            return subprocess.CompletedProcess([], 0, stdout='[{"items": []}]', stderr="")

        with patch.object(reporter.time, "monotonic", side_effect=lambda: clock[0]), \
                patch.object(reporter.time, "sleep", side_effect=sleep), \
                patch.object(reporter.subprocess, "run", side_effect=respond):
            client = reporter.GitHubClient("example/project")
            for number in range(31):
                client.search(str(number))
        self.assertGreaterEqual(requests[30] - requests[0], 60)
        self.assertTrue(all(right - left >= 2 for left, right in zip(requests, requests[1:])))

    def test_searches_and_writes_share_request_spacing(self):
        clock = [0.0]
        requests = []

        def sleep(seconds):
            clock[0] += seconds

        def respond(*args, **kwargs):
            requests.append(clock[0])
            return subprocess.CompletedProcess([], 0, stdout='[{"items": []}]', stderr="")

        with patch.object(reporter.time, "monotonic", side_effect=lambda: clock[0]), \
                patch.object(reporter.time, "sleep", side_effect=sleep), \
                patch.object(reporter.subprocess, "run", side_effect=respond):
            client = reporter.GitHubClient("example/project")
            client.search("first")
            client.comment(1, "again")
            client.search("second")
            client.reopen(1)
            client.search("third")
        self.assertTrue(all(right - left >= 1 for left, right in zip(requests, requests[1:])))
        self.assertGreaterEqual(requests[2] - requests[0], 2)
        self.assertGreaterEqual(requests[4] - requests[2], 2)

    def test_required_missing_labels_are_created_once_before_attachment(self):
        replies = ['[[{"name":"scenario"}]]', '{"name":"regression"}', '[]', '[]']
        with patch.object(reporter.subprocess, "run", side_effect=[
            subprocess.CompletedProcess([], 0, stdout=value, stderr="") for value in replies
        ]) as run, patch.object(reporter.time, "sleep"):
            client = reporter.GitHubClient("example/project")
            client.add_labels(3, ["regression"])
            client.add_labels(4, ["regression"])
        calls = run.call_args_list
        self.assertEqual(len(calls), 4)
        self.assertIn("repos/example/project/labels", calls[0].args[0])
        self.assertIn("repos/example/project/labels", calls[1].args[0])
        self.assertEqual(json.loads(calls[1].kwargs["input"])["name"], "regression")
        self.assertIn("repos/example/project/issues/3/labels", calls[2].args[0])
        self.assertIn("repos/example/project/issues/4/labels", calls[3].args[0])

    def test_transport_searches_all_states_and_paginates_then_writes_json_without_milestone(self):
        replies = [json.dumps([{"items": [{"number": 1}]}, {"items": [{"number": 2}]}]),
                   '[[{"name":"scenario"}, {"name":"regression"}]]', '{"number": 3}',
                   '[[{"body": "first"}], [{"body": "second"}]]', '{}', '{}', '{}']
        with patch.object(reporter.subprocess, "run", side_effect=[
            subprocess.CompletedProcess([], 0, stdout=value, stderr="") for value in replies
        ]) as run, patch.object(reporter.time, "sleep"):
            client = reporter.GitHubClient("example/project")
            self.assertEqual(client.search("abcd"), [{"number": 1}, {"number": 2}])
            self.assertEqual(client.create("title", "body", ["scenario"]), {"number": 3})
            self.assertEqual(client.list_comments(3), [{"body": "first"}, {"body": "second"}])
            client.reopen(3)
            client.add_labels(3, ["regression"])
            client.comment(3, "again")
        calls = run.call_args_list
        search = calls[0].args[0]
        self.assertIn("--paginate", search)
        self.assertIn("--slurp", search)
        self.assertIn("in:body", " ".join(search))
        self.assertIn("label:scenario", " ".join(search))
        self.assertNotIn("is:open", " ".join(search))
        self.assertEqual(json.loads(calls[2].kwargs["input"]), {"title": "title", "body": "body", "labels": ["scenario"]})
        self.assertIn("repos/example/project/issues/3/comments", calls[-1].args[0])
        self.assertEqual(json.loads(calls[-1].kwargs["input"]), {"body": "again"})
        self.assertEqual(json.loads(calls[4].kwargs["input"]), {"state": "open"})
        self.assertEqual(json.loads(calls[5].kwargs["input"]), {"labels": ["regression"]})
        for call in calls:
            self.assertEqual(call.args[0][:2], ["gh", "api"])
            self.assertNotIn("USER", call.kwargs["env"])

    def test_transport_failures_are_not_misreported_as_empty_search_results(self):
        with patch.object(reporter.subprocess, "run", side_effect=subprocess.CalledProcessError(1, ["gh", "api"])):
            with self.assertRaises(subprocess.CalledProcessError):
                reporter.GitHubClient("example/project").search("abcd")


if __name__ == "__main__":
    unittest.main(verbosity=2)
