---
name: Task
about: A change to build — the issue is the spec, one PR closes it
labels: task
---

## What

<!-- The problem or the capability, in plain words. What does a user see or need? -->

## Why it happens

<!-- Only for defects and gaps: the cause, with file references checked against current main
(`backend/app/api/x.py:120`). Leave out for new capabilities. -->

## Proposed change

<!-- The smallest change that solves it. Reuse what exists; name the files. -->

## User check

<!-- What a user does and then sees, in their words, with concrete values worked out from the
domain (law, standard, a calculation by hand), never from the implementation. Who checks: agent,
or owner where an agent cannot go (real device, real mail, real payment). Leave out when no user
notices the change. See docs/acceptance/README.md. -->

Checked by: agent

## Acceptance

<!-- Checkboxes. A PR may say "Closes #N" only when every box is done; otherwise "Refs #N" plus
what remains. Delete lines that do not apply. -->

- [ ] Happy path as a scenario in `features/<name>.feature`, red before the change, green after
- [ ] Edge and error cases as unit tests in the area's existing test file, each red without the fix
- [ ] Organization isolation holds for every new route (`features/organization_isolation.feature`)
- [ ] Docs updated in the same PR: the page that owns the area, `docs/feature-status.md` if a status changed
- [ ] Screenshot in the PR for a visible UI change
- [ ] User check passed on the PR's state: the acceptance comment for `agent`, the owner's answer for `owner`
- [ ] A changed core journey is updated in `docs/acceptance/journeys.md` and its twin in `features/journeys/`
- [ ] A fix for an acceptance finding first fails as a step in the journey's twin
- [ ] `make test` and `make lint` green

## Out of scope

<!-- What this issue deliberately does not do, and where it belongs instead (issue link). -->
