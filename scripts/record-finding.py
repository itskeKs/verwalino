"""Record a finding line once on a bundle, inbox or task issue.

After every review and acceptance run a session records findings as comment lines
(`- [ ] <class or ?> | <screen or file:line> | <what was seen> | <SHA> | PR #<n>`)
on the bundle of the class, on the covering issue or on the inbox (#651).
Before posting, it checks that the bundle does not already carry the same finding.

Usage::

    python3 scripts/record-finding.py <issue> --key "<verbatim key>" --line "<the line>"
    python3 scripts/record-finding.py --file <tsv>

Standard library plus the `gh` command; no new dependency.
"""

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys


# Finding line pattern: - [ ] <class or ?> | <screen or file:line> | <what> | <SHA> | PR #<n>
FINDING_LINE_RE = re.compile(
    r"^- \[ \] "                         # checkbox marker
    r"([^|]+?)"                           # class (non-empty, no pipes)
    r" \| "                               # separator
    r"([^|]+?)"                           # screen/file (non-empty, no pipes)
    r" \| "                               # separator
    r"(.+?)"                              # what was seen (non-empty, greedy)
    r" \| "                               # separator
    r"([0-9a-fA-F]{7,40})"                # git SHA (7-40 hex digits)
    r" \| "                               # separator
    r"PR #(\d+)"                          # PR number
    r"\s*$"                               # optional trailing whitespace
)

VALID_LABELS = {"bundle", "inbox", "task"}


def parse_finding_line(text):
    """Parse a single finding line into components.

    Returns a dict with keys: class_, screen_file, what, sha, pr.
    Raises ValueError when the line does not match the expected format.
    """
    text = text.strip()
    match = FINDING_LINE_RE.match(text)
    if match is None:
        raise ValueError(f"Line does not match finding format: {text!r}")
    return {
        "class_": match.group(1).strip(),
        "screen_file": match.group(2).strip(),
        "what": match.group(3).strip(),
        "sha": match.group(4).strip(),
        "pr": int(match.group(5)),
    }


class GitHubClient:
    """Thin transport for GitHub issues, using the `gh` CLI."""

    def __init__(self):
        self.repo = "itskeKs/verwalino"

    def get_issue(self, number):
        """Retrieve issue body and labels via gh CLI."""
        result = subprocess.run(
            ["gh", "issue", "view", str(number), "-R", self.repo, "--json", "body,labels,state,number"],
            text=True, capture_output=True, check=True, timeout=30,
        )
        data = json.loads(result.stdout)
        return {
            "number": data["number"],
            "state": data["state"],
            "body": data.get("body", ""),
            "labels": [lbl["name"] for lbl in data.get("labels", [])],
        }

    def list_comments(self, number):
        """Retrieve existing comments on the issue via gh CLI REST API with pagination."""
        result = subprocess.run(
            ["gh", "api", "--method", "GET", f"repos/{self.repo}/issues/{number}/comments",
             "-f", "per_page=100", "--paginate", "--slurp"],
            text=True, capture_output=True, check=True, timeout=30,
        )
        pages = json.loads(result.stdout)
        # Flatten all pages into one list of comment dicts.
        comments = []
        for page in pages:
            if isinstance(page, list):
                comments.extend(page)
            else:
                comments.append(page)
        return [{"body": c.get("body", "")} for c in comments]

    def comment(self, number, body):
        """Post a comment on the issue via gh CLI."""
        subprocess.run(
            ["gh", "api", "--method", "POST", f"repos/{self.repo}/issues/{number}/comments",
             "-f", f"body={body}"],
            text=True, capture_output=True, check=True, timeout=30,
        )


def record_finding(client, issue_number, key, line, output=None):
    """Record a single finding on an issue.

    Checks that the key does not already exist in the issue's body or comments.
    Posts the line as a new comment when it is not a duplicate.

    Args:
        client: A GitHubClient (real or faked).
        issue_number: The numeric ID of the target issue.
        key: The verbatim text that identifies the finding.
        line: The full finding line string.
        output: A writable stream for status messages (default: sys.stdout).

    Returns:
        True if a new comment was posted, False if the key was already present.

    Raises:
        ValueError: When the line format is invalid, the issue is closed,
            the issue lacks a required label (bundle/inbox/task), or key is empty.
    """
    if not key.strip():
        raise ValueError("Empty key is refused.")

    # Validate the line format.
    parse_finding_line(line)

    # Fetch the issue and validate its state and labels.
    issue = client.get_issue(issue_number)

    if issue["state"].lower() != "open":
        raise ValueError(
            f"Issue #{issue_number} is {issue['state']}; must be open."
        )

    label_names = {lbl.casefold() for lbl in issue.get("labels", [])}
    if not label_names & VALID_LABELS:
        raise ValueError(
            f"Issue #{issue_number} has labels {issue['labels']}; "
            f"must include one of {sorted(VALID_LABELS)}."
        )

    # Check existing comments for the key (substring match).
    comments = client.list_comments(issue_number)

    # Check if key appears in the issue body.
    issue_body = issue.get("body", "")
    if key in issue_body:
        print(
            f"Issue #{issue_number}: 0 posted, 1 already there.",
            file=output,
        )
        return False

    # Check if key appears in any comment body.
    for comment in comments:
        body = comment.get("body", "")
        if key in body:
            print(
                f"Issue #{issue_number}: 0 posted, 1 already there.",
                file=output,
            )
            return False

    # Post the new finding line as a comment.
    client.comment(issue_number, line)
    print(
        f"Issue #{issue_number}: 1 posted, 0 already there.",
        file=output,
    )
    return True


def main(argv=None):
    """CLI entry point for recording a finding.

    Accepts either:
      - <issue> --key "<key>" --line "<line>"  (single finding)
      - --file <tsv>                           (multiple findings from a TSV file)

    Prints per issue how many lines were posted and how many were already there.
    Returns 0 on success, 1 on refusal.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("issue", nargs="?", type=int, help="Issue number (required with --key/--line)")
    parser.add_argument("--key", required=False, help="Verbatim key identifying the finding.")
    parser.add_argument("--line", required=False, help="The full finding line string.")
    parser.add_argument("--file", type=Path, default=None, help="TSV file with issue, key, line columns.")

    args = parser.parse_args(argv)

    if args.file is not None:
        # Process findings from a TSV file.
        tsv_path = args.file
        if not tsv_path.exists():
            print(f"TSV file not found: {tsv_path}", file=sys.stderr)
            return 1

        raw_text = tsv_path.read_text()
        lines = raw_text.splitlines()

        # Validate every row before any gh call.
        valid_rows = []
        for line_num, raw_line in enumerate(lines, start=1):
            stripped = raw_line.strip()
            if not stripped:
                continue

            parts = stripped.split("\t")
            if len(parts) != 3:
                print(
                    f"TSV line {line_num}: refused: expected exactly 3 tab-separated columns, got {len(parts)}.",
                    file=sys.stderr,
                )
                return 1

            issue_str = parts[0].strip()
            key = parts[1].strip()
            line_text = parts[2].strip()

            # Issue column must be only digits.
            if not issue_str.isdigit():
                print(
                    f"TSV line {line_num}: refused: issue column must be digits, got {issue_str!r}.",
                    file=sys.stderr,
                )
                return 1

            issue_num = int(issue_str)

            # Key must be non-empty after strip.
            if not key:
                print(
                    f"TSV line {line_num}: refused: key is empty.",
                    file=sys.stderr,
                )
                return 1

            # Line must match the finding format.
            try:
                parse_finding_line(line_text)
            except ValueError:
                print(
                    f"TSV line {line_num}: refused: line does not match finding format.",
                    file=sys.stderr,
                )
                return 1

            valid_rows.append((issue_num, key, line_text))

        if not valid_rows:
            return 0

        client = GitHubClient()

        # Group rows by issue, keeping file order.
        issues_seen = []
        rows_by_issue = {}
        for issue_num, key, line_text in valid_rows:
            if issue_num not in rows_by_issue:
                issues_seen.append(issue_num)
                rows_by_issue[issue_num] = []
            rows_by_issue[issue_num].append((key, line_text))

        any_refused = False

        for issue_num in issues_seen:
            rows = rows_by_issue[issue_num]

            # Deduplicate within the file: drop rows whose key equals an earlier row's key.
            issue_skipped = 0
            seen_keys = set()
            unique_rows = []
            for key, line_text in rows:
                if key not in seen_keys:
                    seen_keys.add(key)
                    unique_rows.append((key, line_text))
                else:
                    issue_skipped += 1

            # Read issue once.
            try:
                issue = client.get_issue(issue_num)
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
                print(f"Issue #{issue_num}: refused: {exc}", file=sys.stderr)
                any_refused = True
                continue

            if issue["state"].lower() != "open":
                print(f"Issue #{issue_num}: refused: {issue['state']}", file=sys.stderr)
                any_refused = True
                continue

            label_names = {lbl.casefold() for lbl in issue.get("labels", [])}
            if not label_names & VALID_LABELS:
                print(f"Issue #{issue_num}: refused: labels {issue['labels']}", file=sys.stderr)
                any_refused = True
                continue

            # Read comments once.
            try:
                comments = client.list_comments(issue_num)
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
                print(f"Issue #{issue_num}: refused: {exc}", file=sys.stderr)
                any_refused = True
                continue

            # Check each row against issue body and comment bodies.
            issue_body = issue.get("body", "")
            comment_bodies = [c.get("body", "") for c in comments]

            posted_lines = []
            already_there = 0
            for key, line_text in unique_rows:
                # Check if key is a substring of issue body or any comment body.
                if key in issue_body or any(key in cb for cb in comment_bodies):
                    already_there += 1
                else:
                    posted_lines.append(line_text)

            # Count within-file duplicates as "already there" too.
            already_there += issue_skipped

            # Post one comment with all posted lines joined by newline.
            if posted_lines:
                comment_body = "\n".join(posted_lines)
                try:
                    client.comment(issue_num, comment_body)
                except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
                    print(f"Issue #{issue_num}: refused: {exc}", file=sys.stderr)
                    any_refused = True
                    continue

            print(
                f"Issue #{issue_num}: {len(posted_lines)} posted, {already_there} already there.",
                file=sys.stdout,
            )

        if any_refused:
            return 1
        return 0

    # Single finding mode: requires issue number, --key and --line.
    if args.issue is None or args.key is None or args.line is None:
        parser.print_help()
        return 1

    # Validate key and line before any gh call.
    if not args.key.strip():
        print("Refused: empty key.", file=sys.stderr)
        return 1

    try:
        parse_finding_line(args.line)
    except ValueError:
        print("Refused: line does not match finding format.", file=sys.stderr)
        return 1

    client = GitHubClient()
    try:
        record_finding(client, args.issue, key=args.key, line=args.line)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        print(f"Refused: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"Refused: {exc}", file=sys.stderr)
        return 1

    # If record_finding returned False (key already there), still exit 0.
    return 0


if __name__ == "__main__":
    sys.exit(main())
