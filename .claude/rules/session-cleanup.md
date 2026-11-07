---
description: Session-owned resource cleanup and protected shared resources
---

# Worktrees and cleanup

Work in a git worktree outside the main checkout with a predictable name: `../verwalino-issue-N`
for an issue, `../verwalino-review-N` for a PR under review. App-managed worktrees use the
supported managed location instead. An existing issue worktree means that issue is being worked on;
do not take it over.

Remove only your own worktrees and local branches once their PR is merged or closed.
Check the PR state, that a merge landed on `main` when applicable, that the tree is clean
and that no process or other live user uses it before removal; never discard uncommitted work.
For ordinary worktrees use `git worktree remove <dir> && git branch -D <branch>`.
Use supported archival for app-managed worktrees; never bypass its primary, pinned or
shared-worktree protections. For an abandoned task without a PR, first preserve work that must
be kept.

Every agent cleans up after itself. What a session creates, it removes when its task ends:

- compose projects it started for separately authorized acceptance or verifier runs, completely:
  containers, volumes (including anonymous volumes), networks and local images used only by
  that project, never the production instance and never the shared test database (see
  "Databases and containers" below);
- scratch directories, briefs, logs, screenshots and reports, once their content is in the
  issue or PR; temporary files under its own unique prefix and screenshots outside tracked docs
  in any case; keep committed deliverables;
- background processes it started (dev servers, watchers, browsers, test runs), including
  installer-harness children; free their ports;
- remove only your own inactive lock files, after checking that no run holds them.

Before starting, a session checks for leftovers (`git worktree list`, `docker ps -a`, its
scratch directory, its processes) and cleans up first. It removes only what it created itself.
Never touch other sessions' resources: worktrees, branches, containers, volumes, networks,
images, processes or ports (including `verwalino-review-*`, `verwalino-abnahme-*`,
`.claude/worktrees/*`, `.coddy/worktrees/*` and other issue worktrees).
Leftovers of another session or of the owner are reported, not deleted; a merged or closed
PR alone never authorizes their removal. A matching name or label alone does not establish
ownership. Never prune Docker globally.

Leave production (`docker-compose.prod.yml`, project `hausverwaltung` on the owner's Mac,
`verwalino` on the hosted server) and shared test project `infra` untouched.
Never remove `/tmp/verwalino-testdb.lock` while another run holds it. The only repair is
starting the shared test DB with
`docker compose -f infra/docker-compose.declaration-test.yml up -d postgres`
when it is down, while holding its otherwise free lock; never restart a running DB.
Report cleanup in one line in the status/report, never in a repository file.
