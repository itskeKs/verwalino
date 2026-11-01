"""Check real pytest shard collections and merge CI duration measurements."""

import argparse
from collections import Counter
import json
from pathlib import Path
import subprocess
import sys


def verify_partition(full, shards):
    combined = sum(shards, Counter())
    duplicates = [node for node, count in combined.items() if count > 1]
    missing, extra = full - combined, combined - full
    if not full or missing or extra or duplicates:
        raise ValueError(
            f"Invalid shard partition: missing={list(missing.items())[:10]}, "
            f"extra={list(extra.items())[:10]}, overlap={duplicates[:10]}"
        )


def collect(*selection):
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:randomly", *selection],
        text=True, capture_output=True, check=True,
    )
    return Counter(line for line in result.stdout.splitlines() if line.startswith("tests/") and "::" in line)


def check_shards(shards):
    full = collect()
    groups = [collect("--splits", str(shards), "--group", str(group),
                      "--splitting-algorithm", "least_duration")
              for group in range(1, shards + 1)]
    verify_partition(full, groups)
    print(f"Shard coverage: {sum(full.values())} tests exactly once; "
          f"group sizes: {[sum(group.values()) for group in groups]}")


def merge_durations(paths):
    merged = {}
    for path in paths:
        durations = json.loads(path.read_text())
        if not durations:
            raise ValueError(f"Empty duration measurements: {path}")
        overlap = merged.keys() & durations.keys()
        if overlap:
            raise ValueError(f"Duplicate duration measurements: {sorted(overlap)[:10]}")
        merged.update(durations)
    return dict(sorted(merged.items()))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check", help="Collect the full suite and every shard without running fixtures")
    check.add_argument("shards", type=int)
    merge = commands.add_parser("merge", help="Merge disjoint CI shard measurements for a reviewed refresh commit")
    merge.add_argument("output", type=Path)
    merge.add_argument("inputs", type=Path, nargs="+")
    args = parser.parse_args()
    try:
        if args.command == "check":
            if args.shards < 1:
                parser.error("shards must be positive")
            check_shards(args.shards)
        else:
            args.output.write_text(json.dumps(merge_durations(args.inputs), indent=2) + "\n")
    except subprocess.CalledProcessError as error:
        print(error.stdout, file=sys.stderr)
        print(error.stderr, file=sys.stderr)
        raise SystemExit(error.returncode) from None
    except ValueError as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
