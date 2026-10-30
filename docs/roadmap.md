# Roadmap

Verwalino is a property-management app for rental management, homeowners' associations (WEG) and
individual unit management (SEV). The software is German; the documentation is English.

**Goal:** Verwalino runs end to end in two ways from one image — installed by anyone on their own
server or laptop, and offered free as a hosted service where each property-management company
signs up and gets its own isolated organization.

The MVP for in-house use is complete (tasks T00–T57; history in git, `git log -- docs/mvp.md`).
What exists today: [feature-status.md](feature-status.md).

## How work is tracked

- Every piece of work is a GitHub issue written as a spec
  ([template](../.github/ISSUE_TEMPLATE/task.md)): What → Why → Proposed change → Acceptance →
  Out of scope. One issue, one PR; `Closes #N` only when every acceptance box is done.
- Order comes from milestones (below) and, inside a milestone, from the list order here.
- Read one issue with `gh issue view N -R itskeKs/verwalino`; list a milestone with
  `gh issue list -R itskeKs/verwalino --milestone "<title>"`.
- Failing scenarios, evals and install checks are filed as issues with the label `scenario`
  ([template](../.github/ISSUE_TEMPLATE/scenario-failure.md)).

## Milestones

### 1 Foundation: gates and specs

1. #1 Quality gates — Makefile, pre-commit hook, ruff, GitHub Actions
2. #2 Executable specs with pytest-bdd (pilot)
3. #3 English documentation, this roadmap, issue-based tasks
4. #4 Organization isolation sweep over every API route
5. #44 Area-scoped agent rules for Claude Code, Cursor and Codex
6. #45 DESIGN.md — the frontend design contract
7. #5 Migrate browser acceptance scripts to features

### 2 Self-hosting ready

1. #6 Clean-host acceptance of the portable installation (T58 remainder)
2. #7 Publish signed multi-arch image to GHCR
3. #10 Backup and restore commands
4. #9 Update with automatic backup and rollback
5. #8 First-run setup in the browser with a one-time token
6. #11 Doctor command
7. #12 One-line installer and install guide
8. #13 Nightly install matrix

### 3 Scenario evals and reporting

1. #14 Nightly scenario run with a machine-readable report
2. #15 File failures as deduplicated issues
3. #16 Request IDs and error tracking
4. #17 Redacted diagnostics bundle and „Problem melden“

### 4 Hosted offering

1. #18 Deployment mode switch hosted|selfhost
2. #20 Rate limiting and lockout for login and signup
3. #19 Self-service signup with email verification
4. #21 Password reset by email
5. #22 Per-organization quotas and AI budget
6. #23 Organization data export and account deletion
7. #42 First hosted server (Contabo) with off-site backup (Hetzner Storage Box)
8. #25 Staging environment and hosted canary
9. #24 Website with legal pages
10. #26 Legal obligations before a public hosted launch (decision)

### 4b Tenant portal

Built right after the hosted launch, before the remaining domain gaps:

1. #41 Tenant portal

### 5 Domain gaps

Remaining property-management features from the gap list in
[feature-status.md](feature-status.md#gaps):

1. #35 Inbox review chain runs end to end in operation
2. #34 Anlage V mapping for the per-property year overview
3. #36 Maintenance reserve ledger
4. #67 Separate operating-cost and heating advances per lease
5. #68 Settlement period configurable per property
6. #69 Dunning fees per organization and optional default interest
7. #70 Security deposit in up to three installments
8. #71 VAT-liable letting per unit (§ 9 UStG)
9. #72 Input VAT apportionment (Vorsteueraufteilung)
10. #73 One permissions area and activated source access protection
11. #37 Lease template and handover protocol
12. #38 Security deposit interest credits (after #70)
13. #39 Photos for properties and units
14. #40 Automatic bank retrieval (PSD2/FinTS)

## Open decisions

Only the owner can decide #26 (legal obligations before a public hosted launch, milestone 4).
