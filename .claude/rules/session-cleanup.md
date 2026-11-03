---
description: Session-owned resource cleanup and protected shared resources
---

# Worktrees and cleanup

Work in a git worktree outside the main checkout with a predictable name: `../verwalino-issue-N`
for an issue, `../verwalino-review-N` for a PR under review. A worktree lives exactly as long as
its PR and belongs to the issue or PR, not to the session that created it: once that is merged
or closed, check that the merge landed on `main`, that the tree is clean and that nothing runs
there, then `git worktree remove <dir> && git branch -D <branch>`. The maintainer session that
merges removes its review worktree and the issue worktree of the merged PR; every new session
removes stale worktrees of its kind before it starts. An existing `../verwalino-issue-N` means
issue N is being worked on. For an abandoned task without a PR, first preserve work that must
be kept; never discard uncommitted work.

Every agent cleans up after itself. What a session creates, it removes when its task ends:

- git worktrees and local branches once their PR is merged or closed (above);
- compose projects it started for separately authorized acceptance or verifier runs, completely:
  containers, volumes (including anonymous volumes), networks and local images used only by
  that project, never the production instance and never the shared test database (see
  "Databases and containers" below);
- scratch directories, briefs, logs, screenshots and reports, once their content is in the
  issue or PR; temporary files under its own unique prefix and screenshots outside tracked docs
  in any case; keep committed deliverables;
- background processes it started (dev servers, watchers, browsers, test runs), including
  installer-harness children; free their ports;
- lock files it holds (`/tmp/verwalino-testdb.lock` and the like), only when no process of
  its own uses them any more.

Before starting, a session checks for leftovers (`git worktree list`, `docker ps -a`, its
scratch directory, its processes) and cleans up first. It removes only what it created itself,
with one exception: `../verwalino-issue-N` and `../verwalino-review-N` worktrees whose PR is
merged or closed (above). Never touch other sessions' resources: worktrees, branches, containers,
volumes, networks, images, processes or ports (including `verwalino-review-*` with an open PR,
`verwalino-abnahme-*`, `.claude/worktrees/*`, `.coddy/worktrees/*` and other issue worktrees).
Every other leftover of another session or of the owner is reported, not deleted. A matching
name or label alone does not establish ownership. Never prune Docker globally.

Leave production (`docker-compose.prod.yml`, project `hausverwaltung` on the owner's Mac,
`verwalino` on the hosted server) and shared test project `infra` untouched.
Never remove `/tmp/verwalino-testdb.lock` while another run holds it. The only repair is
starting the shared test DB with
`docker compose -f infra/docker-compose.declaration-test.yml up -d postgres`
when it is down, while holding its otherwise free lock; never restart a running DB.
Report cleanup in one line in the status/report, never in a repository file.
