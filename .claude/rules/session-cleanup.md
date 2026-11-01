---
description: Session-owned resource cleanup and protected shared resources
---

# Session cleanup

- Every session removes what it created when its task is merged, closed or abandoned.
  Before a new task, check for and remove your own leftovers.
- Remove your own worktrees and local branches after the PR is merged or closed; for an
  abandoned task without a PR, first preserve work that must be kept. Check the PR state,
  a clean tree and stopped processes before removal; never discard uncommitted work.
- Remove your own separately authorized acceptance/verifier stacks completely:
  containers, volumes (including anonymous volumes), networks and local images used only
  by that project. Stop your own dev servers, free their ports and terminate every
  background process you started, including installer-harness children.
- Remove scratch files and temporary artefacts you wrote (temporary files under your own
  unique prefix, scratch directories and screenshots outside tracked docs); remove only your own
  inactive lock files. Keep committed deliverables.
- Never touch other sessions' resources: worktrees, branches, containers, volumes,
  networks, images, processes or ports (including `verwalino-review-*`,
  `verwalino-abnahme-*`, `.claude/worktrees/*`, `.coddy/worktrees/*` and other issue worktrees).
  A matching name or label alone does not establish ownership. Never prune Docker globally.
- Leave production (`docker-compose.prod.yml`, project `hausverwaltung` on the owner's Mac,
  `verwalino` on the hosted server) and shared test project `infra` untouched.
  Never remove `/tmp/verwalino-testdb.lock` while another run holds it. The only repair is
  starting the shared test DB with
  `docker compose -f infra/docker-compose.declaration-test.yml up -d postgres`
  when it is down, while holding its otherwise free lock; never restart a running DB.
- Report cleanup in one line in the status/report, never in a repository file.
