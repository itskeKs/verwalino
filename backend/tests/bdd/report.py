"""Optional, portable scenario reports for local and nightly pytest-bdd runs."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

import pytest


def pytest_addoption(parser):
    parser.addoption("--scenario-report", metavar="DIR", help="Write scenarios.json, summary.md and failure artifacts")


def pytest_configure(config):
    directory = config.getoption("--scenario-report")
    if directory:
        config.pluginmanager.register(ScenarioReport(Path(directory)), "scenario-report")


def artifact_directory(item):
    reporter = item.config.pluginmanager.getplugin("scenario-report")
    if reporter is None:
        return None
    directory = reporter.directory / "artifacts" / hashlib.sha256(item.nodeid.encode()).hexdigest()[:16]
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def attach_artifact(item, path):
    reporter = item.config.pluginmanager.getplugin("scenario-report")
    if reporter is not None and item.nodeid in reporter.records:
        relative = Path(path).resolve().relative_to(reporter.directory).as_posix()
        artifacts = reporter.records[item.nodeid]["artifacts"]
        if relative not in artifacts:
            artifacts.append(relative)


def _now():
    return datetime.now(timezone.utc).isoformat()


def _error(exception):
    kind = type(exception)
    return {"type": f"{kind.__module__}.{kind.__qualname__}", "message": str(exception)}


def _git_sha():
    if os.environ.get("GITHUB_SHA"):
        return os.environ["GITHUB_SHA"]
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parents[3],
        env={key: value for key, value in os.environ.items() if key != "USER"},
        capture_output=True, text=True, check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else None


class ScenarioReport:
    def __init__(self, directory):
        self.directory = directory.resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        # Repeated local runs must not retain a prior failure's trace. Remove
        # only our known filenames in nodeid directories, preserving other files.
        for previous in (self.directory / "artifacts").glob("*"):
            if previous.is_symlink() or len(previous.name) != 16 or any(char not in "0123456789abcdef" for char in previous.name):
                continue
            for name in ("screenshot.png", "trace.zip", "last-response.json", "capture-error.txt"):
                (previous / name).unlink(missing_ok=True)
        self.run = {
            "started": _now(), "finished": None, "git_sha": _git_sha(),
            "mode": os.environ.get("VERWALINO_SCENARIO_MODE", "selfhost"),
            "target_url": os.environ.get("VERWALINO_TARGET_URL") or "http://127.0.0.1:13000",
            "runner": f"github-actions:{os.environ['GITHUB_RUN_ID']}" if os.environ.get("GITHUB_RUN_ID") else "local",
            "notes": [
                "Hosted mode is pending #18; only selfhost is implemented.",
                "Nightly self-host startup is unverified until the first GitHub runner run.",
            ],
        }
        self.records = {}
        self.order = {}
        self.step_indices = {}
        self.step_started = {}
        self.active_steps = {}
        self.worlds = {}

    @pytest.hookimpl(trylast=True)
    def pytest_collection_modifyitems(self, items):
        # Collection also includes scenarios skipped before any BDD hook runs.
        for item in items:
            template = getattr(item.obj, "__scenario__", None)
            if template is None:
                continue
            params = getattr(item, "callspec", None)
            scenario = template.render(params.params.get("_pytest_bdd_example", {}) if params else {})
            feature = scenario.feature
            tags = feature.tags | template.tags | (template.rule.tags if template.rule else set())
            if params:
                tags |= {mark.name for mark in params.marks}
            self.records[item.nodeid] = {
                "feature": feature.name, "scenario": scenario.name, "tags": sorted(tags),
                "outcome": "skipped", "duration_s": 0.0, "error": None,
                "steps": [
                    {"keyword": step.keyword, "name": step.name, "outcome": "skipped", "duration_s": 0.0, "error": None}
                    for step in scenario.steps
                ],
                "artifacts": [],
            }
            self.order[item.nodeid] = (feature.filename, template.line_number, item.nodeid)
            for index, step in enumerate(scenario.steps):
                self.step_indices[item.nodeid, step.line_number] = index

    def pytest_bdd_before_scenario(self, request, feature, scenario):
        self.records[request.node.nodeid]["outcome"] = "passed"

    def pytest_bdd_after_scenario(self, request, feature, scenario):
        if "world" in request.fixturenames:
            self.worlds[request.node.nodeid] = request.getfixturevalue("world")

    def pytest_bdd_before_step(self, request, feature, scenario, step, step_func):
        nodeid = request.node.nodeid
        self.active_steps[nodeid] = step
        self.step_started[nodeid] = time.monotonic()

    def _finish_step(self, item, step, outcome, error=None):
        nodeid = item.nodeid
        result = self.records[nodeid]["steps"][self.step_indices[nodeid, step.line_number]]
        started = self.step_started.pop(nodeid, None)
        result.update(outcome=outcome, error=error, duration_s=round(time.monotonic() - started, 6) if started else 0.0)
        self.active_steps.pop(nodeid, None)

    def pytest_bdd_after_step(self, request, feature, scenario, step, step_func, step_func_args):
        self._finish_step(request.node, step, "passed")

    def pytest_bdd_step_error(self, request, feature, scenario, step, step_func, step_func_args, exception):
        self._finish_step(request.node, step, "failed", _error(exception))

    def pytest_bdd_step_func_lookup_error(self, request, feature, scenario, step, exception):
        self._finish_step(request.node, step, "failed", _error(exception))

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_makereport(self, item, call):
        result = yield
        report = result.get_result()
        record = self.records.get(item.nodeid)
        if record is None:
            return
        record["duration_s"] = round(record["duration_s"] + report.duration, 6)
        if report.failed or report.skipped:
            outcome = "failed" if report.failed else "skipped"
            error = _error(call.excinfo.value) if call.excinfo else {"type": "pytest.Failure", "message": str(report.longrepr)}
            if record["outcome"] != "failed":
                record.update(outcome=outcome, error=error)
            step = self.active_steps.get(item.nodeid)
            if step is not None:
                self._finish_step(item, step, outcome, error)
            if report.failed:
                self._attach_response(item)
        elif report.when == "call" and record["outcome"] != "failed":
            record["outcome"] = "passed"

    def _attach_response(self, item):
        world = self.worlds.get(item.nodeid, item.funcargs.get("world"))
        response = getattr(world, "response", None)
        if response is None:
            return
        try:
            body = response.json()
        except ValueError:
            body = response.text
        path = artifact_directory(item) / "last-response.json"
        path.write_text(json.dumps({"call": world.call, "status_code": response.status_code, "body": body}, indent=2, ensure_ascii=False) + "\n")
        attach_artifact(item, path)

    def pytest_sessionfinish(self, session, exitstatus):
        self.run["finished"] = _now()
        scenarios = [self.records[nodeid] for nodeid in sorted(self.records, key=self.order.__getitem__)]
        for record in scenarios:
            if record["outcome"] == "failed" and "browser" in record["tags"]:
                record["artifacts"].extend(
                    path.relative_to(self.directory).as_posix()
                    for path in sorted((self.directory / "logs").glob("*.log"))
                )
            record["artifacts"].sort()
        (self.directory / "scenarios.json").write_text(json.dumps({"run": self.run, "scenarios": scenarios}, indent=2, ensure_ascii=False) + "\n")
        def cell(value):
            return str(value).replace("|", "\\|").replace("\n", " ")
        lines = ["# Scenario results", "", *self.run["notes"], "", "| Feature | Scenario | Outcome | Duration (s) |", "|---|---|---|---|"]
        lines.extend(
            f"| {cell(row['feature'])} | {cell(row['scenario'])} | {row['outcome']} | {row['duration_s']:.3f} |"
            for row in scenarios
        )
        (self.directory / "summary.md").write_text("\n".join(lines) + "\n")
