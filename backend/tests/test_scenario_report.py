"""The report contract runs real Gherkin without a database or application."""
import json
from pathlib import Path

import pytest

pytest_plugins = ["pytester"]


@pytest.fixture(scope="session", autouse=True)
def run_migrations():
    """These tests exercise only the pytest plugin."""


@pytest.fixture(autouse=True)
def inject_test_engine():
    """No application engine is needed."""


def test_report_records_steps_failures_skips_and_portable_artifacts(pytester, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).resolve().parents[1]))
    monkeypatch.setenv("GITHUB_SHA", "123abc")
    monkeypatch.setenv("VERWALINO_SCENARIO_MODE", "selfhost")
    monkeypatch.setenv("VERWALINO_TARGET_URL", "http://127.0.0.1:18080")
    pytester.makefile(".feature", tiny="""
        @contract
        Feature: Report contract
          Scenario: A passing scenario
            Given a response exists
            Then the result passes

          @failure @browser
          Scenario: A failing scenario
            Given a response exists
            Then the result fails
            And the result passes

          @unavailable
          Scenario: A skipped scenario
            Then the result passes
    """)
    pytester.makepyfile("""
        from types import SimpleNamespace
        import httpx
        import pytest
        from pytest_bdd import given, then, scenarios

        scenarios('tiny.feature')

        @pytest.fixture
        def world():
            return SimpleNamespace(
                response=httpx.Response(422, json={'detail': 'synthetic failure'}),
                call='POST /api/example',
            )

        @given('a response exists')
        def response_exists(world):
            pass

        @then('the result passes')
        def passes():
            pass

        @then('the result fails')
        def fails():
            raise ValueError('synthetic failure')

    """)
    pytester.makeconftest("""
        import pytest

        def pytest_sessionstart(session):
            directory = session.config.pluginmanager.getplugin('scenario-report').directory / 'logs'
            directory.mkdir()
            for name in ('backend.log', 'vite.log'):
                (directory / name).write_text('synthetic server log')

        def pytest_collection_modifyitems(items):
            for item in items:
                if item.get_closest_marker('unavailable'):
                    item.add_marker(pytest.mark.skip(reason='external isolation needs two organizations'))
    """)
    destination = pytester.path / "report"
    result = pytester.runpytest_subprocess(
        "-p", "tests.bdd.report", "--scenario-report", str(destination), "-q",
    )
    result.assert_outcomes(passed=1, failed=1, skipped=1)
    report = json.loads((destination / "scenarios.json").read_text())
    run = report["run"]
    assert {"started", "finished", "git_sha", "mode", "target_url", "runner"} <= run.keys()
    assert run["started"] <= run["finished"]
    assert (run["git_sha"], run["mode"], run["target_url"]) == (
        "123abc", "selfhost", "http://127.0.0.1:18080",
    )
    assert any("#18" in note for note in run["notes"])
    assert any("unverified" in note.lower() for note in run["notes"])
    scenarios = report["scenarios"]
    assert [row["scenario"] for row in scenarios] == [
        "A passing scenario", "A failing scenario", "A skipped scenario",
    ]
    assert [row["outcome"] for row in scenarios] == ["passed", "failed", "skipped"]
    for row in scenarios:
        assert row["feature"] == "Report contract"
        assert "contract" in row["tags"]
        assert row["tags"] == sorted(row["tags"])
        assert row["duration_s"] >= 0
        for step in row["steps"]:
            assert {"keyword", "name", "outcome", "duration_s", "error"} == step.keys()
            assert step["duration_s"] >= 0
    failed = scenarios[1]
    assert [step["outcome"] for step in failed["steps"]] == ["passed", "failed", "skipped"]
    assert failed["steps"][1]["error"] == {"type": "builtins.ValueError", "message": "synthetic failure"}
    assert len(failed["artifacts"]) == 3
    for artifact in failed["artifacts"]:
        assert not Path(artifact).is_absolute()
        assert (destination / artifact).is_file()
    response_path = next(path for path in failed["artifacts"] if path.endswith("last-response.json"))
    response = json.loads((destination / response_path).read_text())
    assert response["body"] == {"detail": "synthetic failure"}
    assert "two organizations" in scenarios[2]["error"]["message"]
    summary = (destination / "summary.md").read_text()
    for row in scenarios:
        lines = [line for line in summary.splitlines() if row["scenario"] in line]
        assert len(lines) == 1
        assert row["feature"] in lines[0] and row["outcome"] in lines[0]


def test_report_includes_setup_errors_missing_steps_and_rendered_examples(pytester, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).resolve().parents[1]))
    pytester.makefile(".feature", errors="""
        Feature: Early failures
          Scenario Outline: A value of <value> is visible
            Then the value is <value>
            Examples:
              | value |
              | one   |
              | two   |
          Scenario: Setup fails
            Then the value is one
          Scenario: A step is missing
            Then an undefined step runs
    """)
    pytester.makepyfile("""
        import pytest
        from pytest_bdd import then, parsers, scenarios
        scenarios('errors.feature')
        @pytest.fixture(autouse=True)
        def setup(request):
            if request.node.name == 'test_setup_fails':
                raise RuntimeError('setup failed')
        @then(parsers.parse('the value is {value}'))
        def visible(value):
            pass
    """)
    destination = pytester.path / "report"
    result = pytester.runpytest_subprocess("-p", "tests.bdd.report", "--scenario-report", str(destination), "-q")
    result.assert_outcomes(passed=2, failed=1, errors=1)
    rows = json.loads((destination / "scenarios.json").read_text())["scenarios"]
    assert [row["scenario"] for row in rows[:2]] == ["A value of one is visible", "A value of two is visible"]
    assert rows[2]["outcome"] == rows[3]["outcome"] == "failed"
    assert rows[2]["error"]["type"] == "builtins.RuntimeError"
    assert rows[3]["steps"][0]["error"]["type"].endswith(".StepDefinitionNotFoundError")


@pytest.mark.parametrize("failed,screenshot_error", [(False, False), (True, False), (True, True)])
def test_browser_capture_keeps_trace_and_screenshot_only_on_failure(tmp_path, monkeypatch, failed, screenshot_error):
    """Exercise fixture teardown without requiring a browser installation in unit CI."""
    import asyncio
    from types import SimpleNamespace
    from tests.bdd import browser as browser_module
    from tests.bdd.report import ScenarioReport

    class Tracing:
        async def stop(self, path=None):
            if path:
                Path(path).write_bytes(b"trace")

    class Page:
        async def screenshot(self, path, full_page):
            if screenshot_error:
                from playwright.async_api import Error
                raise Error("Page is closed")
            Path(path).write_bytes(b"screenshot")

    class Context:
        tracing = Tracing()
        async def close(self):
            pass

    class Browser:
        def __init__(self, session, world):
            self.errors = []
            self.context = Context()
            self.page = Page()
        async def start(self, *, trace):
            assert trace
        def run(self, coroutine):
            return asyncio.run(coroutine)

    reporter = ScenarioReport(tmp_path)
    reporter.records["scenario"] = {"outcome": "failed" if failed else "passed", "artifacts": []}
    config = SimpleNamespace(pluginmanager=SimpleNamespace(getplugin=lambda name: reporter))
    item = SimpleNamespace(nodeid="scenario", config=config)
    monkeypatch.setattr(browser_module, "BrowserWorld", Browser)
    fixture = browser_module.browser_world.__wrapped__(None, None, SimpleNamespace(node=item, config=config))
    next(fixture)
    with pytest.raises(StopIteration):
        next(fixture)
    paths = reporter.records["scenario"]["artifacts"]
    expected = (["capture-error.txt", "trace.zip"] if screenshot_error else ["screenshot.png", "trace.zip"]) if failed else []
    assert sorted(Path(path).name for path in paths) == expected
    assert all((tmp_path / path).is_file() for path in paths)
    assert len(list(tmp_path.rglob("trace.zip"))) == int(failed)


def test_reusing_a_report_directory_clears_previous_failure_artifacts(tmp_path):
    from types import SimpleNamespace
    from tests.bdd.report import ScenarioReport, artifact_directory
    reporter = ScenarioReport(tmp_path)
    item = SimpleNamespace(nodeid="a-failed-scenario", config=SimpleNamespace(
        pluginmanager=SimpleNamespace(getplugin=lambda name: reporter),
    ))
    directory = artifact_directory(item)
    for name in ("screenshot.png", "trace.zip", "last-response.json", "capture-error.txt"):
        (directory / name).write_text("old failure")
    (directory / "unrelated.txt").write_text("keep")
    ScenarioReport(tmp_path)
    assert list(directory.iterdir()) == [directory / "unrelated.txt"]
