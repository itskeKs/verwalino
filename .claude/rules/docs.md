---
description: English documentation and the documentation contract
paths:
  - "**/*.md"
  - "**/*.mdc"
---

# Documentation contract

- Write project artifacts in English; quote German software text verbatim.
- Update documentation in the same PR, stating exactly what the code does:

| When you change … | Update |
|---|---|
| Anything a user notices | The owning page: `README.md`, `docs/architecture.md`, `docs/m*.md` or `docs/operations/` |
| A feature's status | Its row in `docs/feature-status.md` |
| Deployment, installation or configuration keys | `DEPLOY.md`, `.env.example` and `scripts/self-host.sh` help |
| An architectural decision binding future work | A new ADR under `docs/adr/` |
| The look of the frontend: tokens, layout, components, states, copy or formats | `DESIGN.md` and its screenshots in `docs/assets/design/` |
| A file, route, key or command rename | Every mention; `git grep -n '<old>'` returns no stale mentions outside `docs/archive/` |

- Read `docs/domain-reference.md` selectively with `rg -n` for product workflows.
- `docs/archive/` is legacy: do not read, edit or treat it as rules unless the user
  asks for a specific question.
- Keep paired rule bodies and activation equivalent; use quoted block lists for
  Claude `paths` and Cursor `globs`. No Claude `paths` means Cursor
  `alwaysApply: true`. Run `make test-agent-rules` after rule or hook changes
  and `make docs-check` for relative links.
