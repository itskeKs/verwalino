"""Verify the original-derived remediation scaffold without DB or network access."""

import hashlib
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[2]
PLAN_SHA256 = "f80a518bd36ab92455c2afaec5433c63278732c2b5d1ba56cf6e26f75e58b112"
STEPS = [
    "01-bootstrap.md",
    "02-status-contract.md",
    "03-intake-evidence.md",
    "04-parity-register.md",
    "05-deployment-runbook.md",
    "06-source-evidence.md",
    "07-weg-reserves.md",
    "08-duplicates-scanner.md",
    "09-final-audit.md",
]
SUBSTEPS = ["08a-duplicate-adoption.md", "08b-scanner-recovery.md"]


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def field(text, name):
    match = re.search(rf"^{re.escape(name)}: (.+)$", text, re.MULTILINE)
    require(match is not None, f"Missing state field: {name}")
    return match.group(1)


def main():
    plan = ROOT / "crossreview-remediation-qwen36-49k.plan.md"
    require(sha256(plan) == PLAN_SHA256, "Original-plan export changed")
    source = plan.read_text()
    sections = re.findall(
        r"^### ([1-9])\. ([^\n]+)\n(.*?)(?=^### [1-9]\. |^## Definition of Done)",
        source,
        re.MULTILINE | re.DOTALL,
    )
    require(len(sections) == 9, "Expected nine original sections")
    for index, (_, _, body) in enumerate(sections):
        text = (ROOT / "steps" / STEPS[index]).read_text()
        require(body.strip() in text, f"Source section missing: {STEPS[index]}")
        require("## Übergabe und Grenzen" in text, f"Missing handoff: {STEPS[index]}")
        if index < 8:
            require(f"steps/{STEPS[index + 1]}" in text, f"Wrong successor: {STEPS[index]}")
    body8 = sections[7][2]
    expected_a = body8.split("**8a Duplikate:**", 1)[1].split("**8b Scanner:**", 1)[0].strip()
    expected_b = body8.split("**8b Scanner:**", 1)[1].split("**Abnahme:**", 1)[0].strip()
    for name, expected in zip(SUBSTEPS, [expected_a, expected_b]):
        require(expected in (ROOT / "steps" / name).read_text(), f"Substep drift: {name}")

    baseline = json.loads((ROOT / "evidence/01-baseline.json").read_text())
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip()
    require(head == baseline["head"], "HEAD changed during bootstrap")
    for path, expected in baseline["protected_worktree_sha256"].items():
        require(sha256(REPO / path) == expected, f"Protected pre-existing file changed: {path}")
    preserved = ["operator-decision-required.md", "04b-closure.md", "receipts/04b-assignment-mappings.md"]
    for path in preserved:
        require(sha256(ROOT / path) == baseline["existing_chain_sha256"][path], f"04b artifact changed: {path}")
    for path in ["state.md", "README.md", "contract.md"]:
        backup = ROOT / "evidence" / ("01-pre-recovery-" + path)
        require(sha256(backup) == baseline["existing_chain_sha256"][path], f"Original entry not preserved: {path}")

    state = (ROOT / "state.md").read_text()
    require(field(state, "plan") == "crossreview-remediation-qwen36-49k", "Wrong plan")
    current = field(state, "current")
    require(current in ["steps/01-bootstrap.md", "steps/02-status-contract.md"], "Incorrect bootstrap current")
    require((ROOT / current).is_file(), "Current step missing")
    for name in ["current_status", "last_completed", "last_receipt", "next_action", "running_tasks"]:
        field(state, name)
    require("open_blockers:" in state and "Owned dirtypaths" in state, "Missing handoff boundaries")
    receipt = (ROOT / "receipts/01-bootstrap.md").read_text()
    if current == "steps/02-status-contract.md":
        require(field(state, "current_status") == "pending", "Next package prematurely started")
        require(field(state, "last_completed") == "steps/01-bootstrap.md", "Wrong completed package")
        require(field(state, "last_receipt") == "receipts/01-bootstrap.md", "Wrong receipt")
        require("status: done-local" in receipt, "Bootstrap receipt not accepted")
    else:
        require(field(state, "current_status") == "in-progress", "Incorrect bootstrap status")

    docs = [ROOT / name for name in ["README.md", "contract.md", "state.md", "receipts/01-bootstrap.md"]]
    docs += [ROOT / "steps" / name for name in STEPS + SUBSTEPS]
    checked_links = 0
    for path in docs:
        text = path.read_text()
        for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", text):
            require("://" not in target, f"Unexpected network link in bootstrap: {path}")
            target = target.split("#", 1)[0]
            if target:
                require((path.parent / target).exists(), f"Broken link in {path}: {target}")
                checked_links += 1
        require(text.endswith("\n"), f"Missing final newline: {path}")
        for number, line in enumerate(text.splitlines(), 1):
            require(line == line.rstrip(), f"Trailing whitespace: {path}:{number}")
    contract = (ROOT / "contract.md").read_text()
    require("Lies docs/project-completion/review-remediation/state.md" in contract, "Wrong fresh-session entry")
    subprocess.run(["git", "diff", "--check"], cwd=REPO, check=True)
    print(json.dumps({
        "result": "PASS",
        "scope": "Bootstrap structure only; no product, financial, runtime or release acceptance",
        "current": current,
        "original_plan_sha256": PLAN_SHA256,
        "original_sections_verified": len(sections),
        "separate_substeps_verified": len(SUBSTEPS),
        "protected_preexisting_files_unchanged": len(baseline["protected_worktree_sha256"]),
        "preserved_04b_artifacts_unchanged": len(preserved),
        "local_links_verified": checked_links,
        "git_diff_check": "PASS",
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
