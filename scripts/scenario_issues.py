"""File deduplicated nightly failures using only the report, checkout and gh CLI."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
# Pace searches and writes together to respect GitHub's 30 searches/minute limit.
API_INTERVAL_SECONDS = 2
TOKEN_LENGTH = 16
# Retain this many original error characters, split between the head and tail.
ERROR_TEXT_LIMIT = 20_000
# Bound new failure issues per run; overflow gets one additional summary issue.
MAX_NEW_ISSUES_PER_RUN = 10


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()[:TOKEN_LENGTH]


def error_text(error):
    if isinstance(error, dict):
        return ": ".join(str(error[key]) for key in ("type", "message") if error.get(key))
    return str(error or "Unknown error")


def error_kind(error):
    if isinstance(error, dict) and error.get("type"):
        return error["type"]
    text = error_text(error).splitlines()[0]
    text = re.sub(r"\b\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)?", "<timestamp>", text)
    text = re.sub(r"\b[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\b", "<id>", text)
    text = re.sub(r"(?i)\b(request[-_ ]?id|[\w-]*id)\s*(?:[:=]\s*|\s+)[\w.-]+", r"\1=<id>", text)
    text = re.sub(r"(?<![\w<])(?:[A-Za-z]:[\\/]|\.{1,2}/|/|[\w.-]+/)[^\s\"'<>),]+", "<path>", text)
    text = re.sub(r"\b(?:0x[0-9a-fA-F]+|[0-9a-fA-F]{16,})\b", "<id>", text)
    text = re.sub(r"\b\d+(?:\.\d+)?\b", "<number>", text)
    return " ".join(text.split())


def step_text(step):
    return " ".join(str(step.get(key, "")) for key in ("keyword", "name")).strip()


def fingerprint(row, step):
    readable = "::".join((row["feature"], row["scenario"], step_text(step), error_kind(step.get("error") or row.get("error"))))
    return readable, digest(readable)


def feature_sources(root):
    """Read feature tags and source names, including unrendered outline templates."""
    sources = {}
    for path in sorted((root / "features").glob("*.feature")):
        tags = []
        feature = None
        templates = []
        for line in path.read_text().splitlines():
            line = line.strip()
            if feature is None and line.startswith("@"):
                tags.extend(line.split("#", 1)[0].split())
            elif line.startswith("Feature:"):
                feature = line.removeprefix("Feature:").strip()
            elif match := re.match(r"Scenario(?: Outline| Template)?:\s*(.*)", line):
                templates.append(match[1])
        if feature:
            sources[feature] = (
                path.relative_to(root).as_posix(),
                sorted({tag[1:] for tag in tags if re.fullmatch(r"@area:[\w-]+", tag)}),
                templates,
            )
    return sources


def scenario_selector(name, templates):
    if name not in templates:
        for template in templates:
            pattern = "".join(".*?" if part.startswith("<") else re.escape(part)
                              for part in re.split(r"(<[^>]+>)", template))
            if re.fullmatch(pattern, name):
                name = template
                break
    # Match pytest-bdd's make_python_name, without importing test dependencies.
    return re.sub(r"^\d+_*", "", re.sub(r"\W", "", name.replace(" ", "_"))).lower()


def code_block(text):
    if len(text) > ERROR_TEXT_LIMIT:
        omitted = len(text) - ERROR_TEXT_LIMIT
        head = ERROR_TEXT_LIMIT // 2
        text = (f"{text[:head]}\n… [truncated {omitted} characters; full text in the run artifact]\n"
                f"{text[-(ERROR_TEXT_LIMIT - head):]}")
    # Errors may themselves contain Markdown fences.
    fence = "`" * max(3, max((len(match[0]) + 1 for match in re.finditer(r"`+", text)), default=0))
    return f"{fence}text\n{text}\n{fence}"


def issue_body(template, failure, run, run_url, date, source, templates):
    row = failure["row"]
    command_args = shlex.join(["-q", "-k", scenario_selector(row["scenario"], templates)])
    command = f"make test-scenarios-report PYTEST_ARGS={shlex.quote(command_args)}"
    artifacts = [f"scenarios-{run['mode']}-{date}", "scenarios.json", "summary.md", *sorted(failure["artifacts"])]
    body = template.split("---", 2)[2].strip()
    replacements = {
        "Fingerprint: `<feature>::<scenario>::<step>::<error kind>`": (
            f"Fingerprint: `{failure['fingerprint']}`\n{failure['marker']}\n{failure['error_marker']}"
        ),
        '- Scenario: `features/<file>.feature` — "<scenario name>"': (
            f"- Feature: {row['feature']}\n- Scenario: `{source}` — \"{row['scenario']}\""
        ),
        '<step text>': failure["step"],
        '- Expected: …': "- Expected: the scenario step passes.",
        '- Actual: …': f"- Actual:\n\n{code_block(failure['error'])}",
        '- Run: <CI job / nightly / install matrix target / hosted canary> — <link>': f"- First seen run: {run_url} ({date})",
        '<git sha / image digest>': run.get("git_sha") or "not recorded",
        '<hosted|selfhost>': run["mode"],
        '<logs, screenshot, Playwright trace>': ", ".join(f"`{name}`" for name in artifacts),
        'source scripts/test-env.sh && cd backend && uv run pytest tests/bdd -k "<scenario name>"': command,
    }
    for placeholder, value in replacements.items():
        body = body.replace(placeholder, value)
    return body + "\n"


class GitHubClient:
    """Thin transport. Reads may paginate; searches and writes are paced and serial."""

    def __init__(self, repo):
        self.repo = repo
        self.last_request = None
        self.known_labels = None

    def _api(self, method, endpoint, payload=None, *, query=None, paginate=False):
        args = ["gh", "api", "--method", method, endpoint]
        if paginate:
            args += ["--paginate", "--slurp"]
        if query:
            for key, value in query.items():
                args += ["-f", f"{key}={value}"]
        if payload is not None:
            args += ["--input", "-"]
        paced = payload is not None or endpoint == "search/issues"
        if paced and self.last_request is not None:
            time.sleep(max(0, API_INTERVAL_SECONDS - (time.monotonic() - self.last_request)))
        result = subprocess.run(
            args, input=json.dumps(payload) if payload is not None else None,
            text=True, capture_output=True, check=True, timeout=60,
            env={key: value for key, value in os.environ.items() if key != "USER"},
        )
        if paced:
            self.last_request = time.monotonic()
        return json.loads(result.stdout)

    def search(self, token):
        pages = self._api("GET", "search/issues", query={
            "q": f'repo:{self.repo} is:issue label:scenario "scenario-fingerprint: {token}" in:body',
            "per_page": "100",
        }, paginate=True)
        if any(page.get("incomplete_results") for page in pages):
            raise ValueError("GitHub returned incomplete search results; refusing to create duplicates")
        return [issue for page in pages for issue in page["items"]]

    def _ensure_labels(self, labels):
        if self.known_labels is None:
            pages = self._api("GET", f"repos/{self.repo}/labels", query={"per_page": "100"}, paginate=True)
            self.known_labels = {label["name"].casefold() for page in pages for label in page}
        for name in labels:
            if name.casefold() not in self.known_labels:
                self._api("POST", f"repos/{self.repo}/labels", {"name": name, "color": "d73a4a"})
                self.known_labels.add(name.casefold())

    def create(self, title, body, labels):
        self._ensure_labels(labels)
        return self._api("POST", f"repos/{self.repo}/issues", {"title": title, "body": body, "labels": labels})

    def list_comments(self, number):
        pages = self._api("GET", f"repos/{self.repo}/issues/{number}/comments", query={"per_page": "100"}, paginate=True)
        return [comment for page in pages for comment in page]

    def comment(self, number, body):
        self._api("POST", f"repos/{self.repo}/issues/{number}/comments", {"body": body})

    def reopen(self, number):
        self._api("PATCH", f"repos/{self.repo}/issues/{number}", {"state": "open"})

    def add_labels(self, number, labels):
        self._ensure_labels(labels)
        self._api("POST", f"repos/{self.repo}/issues/{number}/labels", {"labels": labels})


def file_failures(report, client, *, run_url, root=ROOT, dry_run=False, output=None):
    output = output or sys.stdout
    run = report["run"]
    date = (run.get("finished") or run.get("started") or datetime.now(timezone.utc).isoformat())[:10]
    failures = {}
    for row in report["scenarios"]:
        if row["outcome"] != "failed" or "known-flaky" in {tag.lstrip("@") for tag in row.get("tags", [])}:
            continue
        steps = [step for step in row.get("steps", []) if step["outcome"] == "failed"]
        if not steps:
            steps = [{"name": "setup/teardown", "error": row.get("error")}]
        for step in steps:
            readable, token = fingerprint(row, step)
            failure = failures.setdefault(token, {
                "row": row, "fingerprint": readable, "marker": f"<!-- scenario-fingerprint: {token} -->",
                "step": step_text(step), "artifacts": set(), "errors": set(),
            })
            failure["artifacts"].update(row.get("artifacts", []))
            failure["errors"].add(error_text(step.get("error") or row.get("error")))

    plan = []
    new_issues = 0
    overflow = []
    sources = feature_sources(root) if failures else {}
    template = (root / ".github/ISSUE_TEMPLATE/scenario-failure.md").read_text() if failures else ""
    for token, failure in failures.items():
        failure["error"] = "\n\n".join(sorted(failure["errors"]))
        failure["error_marker"] = f"<!-- scenario-error: {digest(failure['error'])} -->"
        # Search is fuzzy. Only the complete marker on its own line is identity.
        matches = [issue for issue in client.search(token) if failure["marker"] in issue["body"].splitlines()]
        issue = min(matches, key=lambda issue: (issue["state"].lower() != "open", issue["number"]), default=None)
        if issue is None:
            if new_issues >= MAX_NEW_ISSUES_PER_RUN:
                overflow.append((token, failure))
                continue
            new_issues += 1
            source, labels, templates = sources.get(failure["row"]["feature"], (failure["row"]["feature"], [], []))
            plan.append({
                "action": "create", "title": f"Scenario failure: {failure['row']['scenario']}"[:240],
                "body": issue_body(template, failure, run, run_url, date, source, templates), "labels": ["scenario", *labels],
            })
        else:
            comments = client.list_comments(issue["number"])
            previous = next((comment["body"] for comment in reversed(comments) if "<!-- scenario-error:" in comment["body"]), issue["body"])
            changed = failure["error_marker"] not in previous.splitlines()
            body = f"Failed again in run {run_url} ({date}, mode {run['mode']})\n\n{failure['error_marker']}\n"
            if changed:
                body += f"\nStep error changed:\n\n{code_block(failure['error'])}\n"
            plan.append({"action": "reopen" if issue["state"].lower() == "closed" else "comment",
                         "number": issue["number"], "body": body})

    if overflow:
        run_id = run_url.rstrip("/").rsplit("/", 1)[-1]
        body = f"Further scenario failures in run {run_url} ({date}, mode {run['mode']}):\n\n"
        body += "\n".join(f"- {failure['row']['scenario']} — `{failure['fingerprint']}` (token `{token}`)"
                          for token, failure in overflow)
        plan.append({
            "action": "create",
            "title": f"Nightly scenarios: {len(overflow)} further failures not filed (run {run_id})",
            "body": body + "\n", "labels": ["scenario"],
        })

    if dry_run:
        print(json.dumps(plan, indent=2, ensure_ascii=False), file=output)
        return plan

    # Finish every lookup before applying the batch. The transport paces requests.
    for action in plan:
        if action["action"] == "create":
            issue = client.create(action["title"], action["body"], action["labels"])
            print(f"Created scenario issue #{issue['number']}", file=output)
        else:
            number = action["number"]
            if action["action"] == "reopen":
                client.reopen(number)
                client.add_labels(number, ["regression"])
            client.comment(number, action["body"])
            print(f"{action['action'].capitalize()} scenario issue #{number}", file=output)
    return plan


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--run-url", required=True)
    parser.add_argument("--mode", help="Override the report's run mode")
    parser.add_argument("--dry-run", action="store_true", help="Print the plan; GitHub reads only")
    args = parser.parse_args(argv)
    try:
        report = json.loads(args.report.read_text())
        if args.mode:
            report["run"]["mode"] = args.mode
        file_failures(report, GitHubClient(args.repo), run_url=args.run_url, dry_run=args.dry_run)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print(f"Scenario filing failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
