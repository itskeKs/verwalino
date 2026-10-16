"""Direct audit of owned repair artifacts; no DB, providers or .env access."""
import ast
import difflib
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
REPAIR = ROOT / "docs/project-completion/review-remediation"
DOCS = [
    "crossreview-remediation-qwen36-49k.plan.md", "README.md", "contract.md",
    "steps/09-final-audit.md", "evidence/09-normative-pa-ic-audit.md",
    "../README.md", "../contract.md", "../../acceptance/parity-register.md",
    "../../../DEPLOY.md",
    "state.md", "handoff-2026-10-13.md",
    "evidence/03-ownership-verification.md", "evidence/04-skip-suppression-befund.md",
    "evidence/05-product-04b-separation.md", "evidence/06-context-budget.md",
    "evidence/07-weg-source-qualification.md", "evidence/08b-test-verification.md",
    "evidence/08b-assertion-mapping.md", "evidence/09-gap-list.md",
    "evidence/09-acceptance-matrix.md",
    "receipts/ownership-baseline-comparison.md", "receipts/plan-contract-state-alignment.md",
    "receipts/07-weg-reserves.md", "receipts/08a-duplicate-adoption.md",
    "receipts/08b-scanner-recovery.md", "receipts/scanner-recovery-mapping.md",
    "receipts/final-acceptance.md",
]
TEST_FILES = [
    "test_allocation_adoptions.py", "test_intake_inspection.py",
    "test_intake_batches.py", "test_outbox_delivery.py",
    "test_allocation_source_replacements.py", "test_duplicate_comparisons.py",
]
SOURCES = [ROOT / "backend/tests" / name for name in TEST_FILES]
SOURCES += [ROOT / name for name in [
    "backend/tests/conftest.py", "backend/tests/legacy_migration_database.py",
    "backend/tests/migration_rehearsal.py", "backend/tests/test_financial_scope_boundary.py",
    "backend/tests/test_declaration_approval_new_property.py",
    "backend/tests/test_declaration_approval_existing_property.py",
    "backend/app/core/intake_inspection.py", "backend/app/services/intake_service.py",
    "backend/app/api/intake_batches.py", "backend/app/workers/workflow.py",
    "backend/app/workers/outbox.py", "backend/app/core/tenant.py",
    "backend/app/engine/wirtschaftsplan.py", "backend/app/engine/weg.py",
    "backend/app/engine/types.py", "backend/app/services/weg_settlement_service.py",
    "backend/migrations/versions/0093_intake_quarantine.py",
    "backend/migrations/versions/0097_internal_outbox_delivery.py",
    "backend/migrations/versions/0109_allocation_adoptions.py",
    "backend/migrations/versions/0122_allocation_source_replacements.py",
    "backend/migrations/versions/0123_replacement_exception_outcomes.py",
]]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def relative(path):
    return str(path.relative_to(ROOT))


errors = []
names = set()
nodes = []
for path in SOURCES:
    if not path.is_file():
        errors.append(f"Missing source: {relative(path)}")
        continue
    tree = ast.parse(path.read_text())
    names.update(n.name for n in ast.walk(tree)
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test_"))
    if path.parent == ROOT / "backend/tests" and path.name in TEST_FILES:
        items = []
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or not node.name.startswith("test_"):
                continue
            count = 1
            for dec in node.decorator_list:
                if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute) and dec.func.attr == "parametrize":
                    values = dec.args[1]
                    if not isinstance(values, (ast.List, ast.Tuple)):
                        raise ValueError(f"Unknown parameter container in {node.name}")
                    count *= len(values.elts)
            items.append(dict(name=node.name, line=node.lineno, cases=count))
        nodes.append(dict(path=relative(path), functions=len(items), cases=sum(x["cases"] for x in items), nodes=items))
# WEG citations refer to functions in this separately scoped test source.
weg_test = ROOT / "backend/tests/engine/test_golden_weg.py"
names.update(n.name for n in ast.walk(ast.parse(weg_test.read_text()))
             if isinstance(n, ast.FunctionDef) and n.name.startswith("test_"))
SOURCES.append(weg_test)
checked_links = 0
checked_nodes = 0
for name in DOCS:
    path = REPAIR / name
    text = path.read_text()
    if not text.endswith("\n"):
        errors.append(f"No final newline: {name}")
    fences = []
    for line_no, line in enumerate(text.splitlines(), 1):
        if line.rstrip() != line:
            errors.append(f"Trailing whitespace: {name}:{line_no}")
        if line.lstrip().startswith("```"):
            fences.append(line_no)
    if len(fences) % 2:
        errors.append(f"Unclosed code fence: {name}")
    for target in re.findall(r"\]\(([^)]+)\)", text):
        target = target.split("#", 1)[0]
        if not target or target.startswith(("https://", "http://", "coddy:", "mailto:")):
            continue
        checked_links += 1
        if not (path.parent / target).resolve().exists():
            errors.append(f"Broken local link: {name} -> {target}")
    for match in re.finditer(r"\b(test_[A-Za-z0-9_]+)\b", text):
        node = match.group(1)
        if text[match.end():].startswith(".py"):
            continue
        checked_nodes += 1
        if node not in names:
            errors.append(f"Unknown test name: {name} -> {node}")
for name in ["07-reserve-diagnostic.py", "08a-identity-diagnostic.py", "09-audit.py"]:
    path = REPAIR / "evidence" / name
    compile(path.read_bytes(), str(path), "exec")

baseline = json.loads((REPAIR / "evidence/01-baseline.json").read_text())
protected = []
for name, expected in baseline["protected_worktree_sha256"].items():
    path = ROOT / name
    actual = digest(path) if path.is_file() else None
    protected.append(dict(path=name, baseline_sha256=expected, current_sha256=actual, equal=expected == actual))
changed = [x["path"] for x in protected if not x["equal"]]
historical_delta = ["backend/tests/test_intake_batches.py"]
document_delta = ["docs/acceptance/parity-register.md"]
unexpected = sorted(set(changed) - set(historical_delta + document_delta))
if unexpected:
    errors.append(f"Unexpected protected changes: {unexpected}")
before = REPAIR / "evidence/08b-test-intake-batches.before.txt"
source = ROOT / "backend/tests/test_intake_batches.py"
if digest(before) != baseline["protected_worktree_sha256"][relative(source)]:
    errors.append("Original test bytes do not match workspace baseline")
patch = "".join(difflib.unified_diff(before.read_text().splitlines(True), source.read_text().splitlines(True),
                                   fromfile="before/" + relative(source), tofile="after/" + relative(source)))
# Historical bytes are immutable: verify, never regenerate an earlier artifact.
if (REPAIR / "evidence/08b-owned-test.delta.patch").read_text() != patch:
    errors.append("Historical scanner patch no longer matches its recorded sources")
# These checks constrain active steering; they are not product acceptance tests.
controls = [REPAIR / name for name in [
    "README.md", "contract.md", "state.md", "handoff-2026-10-13.md",
    "crossreview-remediation-qwen36-49k.plan.md",
]] + [ROOT / "docs/project-completion" / name for name in ["README.md", "contract.md"]]
for path in controls:
    text = path.read_text()
    for required in ["65536", "mlx/qwen3.6-35b-a3b", "openai/gpt-6.1-sol", "reasoning: medium"]:
        if required not in text:
            errors.append(f"Missing active control {required}: {relative(path)}")
    if "Qwen3.8" in text:
        errors.append(f"Stale active model: {relative(path)}")
plan_text = (REPAIR / "crossreview-remediation-qwen36-49k.plan.md").read_text()
goal = plan_text.split("## Start-Goal", 1)[1].split("```text\n", 1)[1].split("\n```", 1)[0]
if not goal.startswith("/goal --model openai/gpt-6.1-sol --reasoning medium") or len(goal) >= 4000:
    errors.append("Invalid copy-only Goal checker or length")
deploy_text = (ROOT / "DEPLOY.md").read_text()
if "0122 (head)" not in deploy_text or "head `0070 (head)`" in deploy_text:
    errors.append("Deployment head contradicts accepted 0122 gate")
if "app_rw` role for both" in deploy_text or "worker receive only `app_rw`" in deploy_text:
    errors.append("Deployment documentation conflates API and worker roles")
parity_text = (ROOT / "docs/acceptance/parity-register.md").read_text()
for forbidden in ["| `leases.py` | P5.1 (RF31)", "| `meetings.py` | P4.5 (RF31)"]:
    if forbidden in parity_text:
        errors.append("Unsupported normative RF31 mapping remains")

check = subprocess.run(["git", "diff", "--check"], cwd=ROOT, capture_output=True, text=True)
if check.returncode:
    errors.append("git diff --check failed; see captured output")
report = dict(captured_at_utc=datetime.now(timezone.utc).isoformat(),
              scope=DOCS, checked_local_links=checked_links, checked_test_name_references=checked_nodes,
              protected_equal=sum(x["equal"] for x in protected), protected_total=len(protected),
              protected_deltas_against_historical_baseline=changed, historical_test_delta=historical_delta,
              authorized_document_delta=document_delta, goal_characters=len(goal),
              errors=errors, exit_code=1 if errors else 0,
              git_diff_check=dict(command=["git", "diff", "--check"], exit_code=check.returncode,
                                  stdout=check.stdout, stderr=check.stderr),
              limits="Local link existence and scoped AST/syntax/whitespace checks, not external-link availability or a complete semantic review of upstream P/A/RF/Parity/Deployment packages. Baseline-equal historical 04b artifacts intentionally preserved; not active control.")
(REPAIR / "evidence/09-document-audit.json").write_text(json.dumps(report, indent=2) + "\n")

# Keep historical run records verbatim, including commands and log digests.
# Regenerating a document audit neither executes those tests nor rebinds their
# outcomes to today's source hashes.
previous = json.loads((REPAIR / "evidence/09-audit-manifest.json").read_text())
runs = previous.get("historical_runs", previous.get("runs", []))
for run in runs:
    if digest(REPAIR / run["log"]) != run["log_sha256"]:
        raise ValueError(f"Historical log digest changed: {run['log']}")
head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
artifacts = [REPAIR / name for name in DOCS] + [REPAIR / "evidence" / name for name in
              ["07-reserve-diagnostic.py", "08a-identity-diagnostic.py", "09-audit.py", "08b-owned-test.delta.patch",
               "09-test-nodes.json", "09-baseline-comparison.json", "09-document-audit.json"]]
artifacts += [REPAIR / run["log"] for run in runs]
artifacts += [REPAIR / "evidence" / name for name in
              ["08a-bg426-results.log", "08b-test-intake-batches.before.txt",
               "09-document-audit-first.json", "09-repair-state-baseline.diff"]]
artifacts += [REPAIR / "evidence" / name for name in [
    "09-deployment-mock.log", "09-deployment-mock.json", "09-continuation-protection.json",
]]
manifest = dict(captured_at_utc=datetime.now(timezone.utc).isoformat(), head=head, recorded_baseline_head=baseline["head"],
                interpreter=sys.executable, python=sys.version,
                implementation_model_requirement="mlx/qwen3.6-35b-a3b", context_target_tokens=65536,
                goal_checker=dict(model="openai/gpt-6.1-sol", reasoning="medium"),
                backend_context_window_verified=False, goal_executed=False,
                historical_runs=runs, runs_executed_by_audit=[],
                continuation_deployment_mock=json.loads((REPAIR / "evidence/09-deployment-mock.json").read_text()),
                prior_session_checks=[
                    dict(task="bg_706", frontend_lint_exit=0, frontend_tests_passed=318, frontend_build_exit=0,
                         limitation="Earlier session run, not re-executed; 871.02 kB chunk warning remains."),
                    dict(task="bg_705", status="timed_out", exit_code=-1,
                         limitation="Incomplete at approximately 56%, E marker present; no backend acceptance."),
                ],
                current_source_sha256={relative(p): digest(p) for p in SOURCES if p.is_file()},
                reference_source_sha256={name: digest(ROOT / name) for name in [
                    "docs/2026-10-01-property-management-implementation-plan.md",
                    "docs/2026-10-01-product-ux-technical-design.md",
                    "docs/project-completion/traceability.md",
                    "scripts/deploy-production.sh", "scripts/verify-production.sh",
                    "scripts/test-deploy-production.sh", "infra/db/prod-init.sh",
                ]},
                owned_artifact_sha256={relative(p): digest(p) for p in artifacts},
                audit_command="backend/.venv/bin/python docs/project-completion/review-remediation/evidence/09-audit.py",
                audit_exit_code=report["exit_code"],
                limits="Current document audit and scoped source hashes only. historical_runs preserve earlier task records verbatim, not new tests or proof of current source execution. No product/test edits in this continuation. Existing historical scanner test delta is not this continuation. No historical worktree reconstruction or full P3.3/P4.3/release acceptance. Goal text only; backend window unverified.")
(REPAIR / "evidence/09-audit-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")

# Historical artifacts are immutable: verify, never regenerate an earlier artifact.
# These writes happen AFTER the protection check to avoid circular dependencies.
comparison_path = REPAIR / "evidence/09-baseline-comparison.json"
comparison = json.loads(comparison_path.read_text())
comparison["protected"] = protected
comparison["authorized_document_delta"] = document_delta
comparison["own_delta"] = dict(path=relative(source), original_bytes=relative(before), patch="evidence/08b-owned-test.delta.patch")
for entry in comparison["existing_repair_files"]:
    path = ROOT / entry["path"]
    entry["current_sha256"] = digest(path)
    entry["equal"] = entry["baseline_sha256"] == entry["current_sha256"]
comparison["captured_at_utc"] = datetime.now(timezone.utc).isoformat()
comparison_path.write_text(json.dumps(comparison, indent=2) + "\n")
old_state = REPAIR / "evidence/01-pre-recovery-state.md"
if digest(old_state) != baseline["existing_chain_sha256"]["state.md"]:
    errors.append("Saved repair-state bytes do not match their original baseline")
(REPAIR / "evidence/09-repair-state-baseline.diff").write_text("".join(
    difflib.unified_diff(old_state.read_text().splitlines(True), (REPAIR / "state.md").read_text().splitlines(True),
                        fromfile="baseline repair state", tofile="current repair state")))
(REPAIR / "evidence/09-test-nodes.json").write_text(json.dumps(nodes, indent=2) + "\n")

print(json.dumps({k: report[k] for k in ["checked_local_links", "checked_test_name_references", "protected_equal", "protected_total", "errors", "exit_code"]}, indent=2))
sys.exit(report["exit_code"])
