<!-- Commit subjects: type(scope): summary. Body: cause → change → tests. See CONTRIBUTING.md, "Pull requests". -->

## What this change is for

<!-- One or two sentences. `Closes #N` only when every acceptance box of the issue is done, otherwise
`Refs #N` plus what remains. -->

Refs #

## Scenarios and tests

<!-- Which scenario in features/ and which unit tests were added or changed, and that they were
red before the change. "None" needs a reason. -->

## User check

<!-- Copy the issue's user check. The acceptance run on this state reports here as a comment
(passed / not passed per point, findings); for `owner` checks the owner answers here. Delete when
the issue has no user check. -->

## Checks

- [ ] `make lint` green
- [ ] `make test` green (whole suite, against the test database)
- [ ] `make docs-check` green
- [ ] Docs updated in the same PR (owning page, `docs/feature-status.md` if a status changed)
- [ ] Screenshots section filled in if `frontend/` changed; `DESIGN.md` updated if look or copy changed

## Screenshots

<!-- Mandatory when frontend/ changed, otherwise delete this section. Before and after, desktop
and 360 px wide, taken from a local instance with seed data — never from a production instance. -->

| | Desktop | 360 px |
| --- | --- | --- |
| Before | | |
| After | | |

## Notes for the reviewer

<!-- Risks, follow-ups, what to look at first. Delete if empty. -->
