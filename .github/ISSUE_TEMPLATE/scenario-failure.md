---
name: Scenario failure
about: A scenario, eval or install check failed — filed by a runner or by hand
labels: bug, scenario
---

<!-- Runners fill this in automatically. Keep the fingerprint line: it deduplicates reports. -->

Fingerprint: `<feature>::<scenario>::<step>::<error kind>`

## What failed

- Scenario: `features/<file>.feature` — "<scenario name>"
- Step: "<step text>"
- Expected: …
- Actual: …

## Where

- Run: <CI job / nightly / install matrix target / hosted canary> — <link>
- Version: <git sha / image digest>, mode: <hosted|selfhost>
- Artifacts: <logs, screenshot, Playwright trace>

## Reproduce

```bash
source scripts/test-env.sh && cd backend && uv run pytest tests/bdd -k "<scenario name>"
```

## Acceptance

- [ ] The scenario passes again; if the failure exposed a missing case, a unit test that is red without the fix
- [ ] Root cause stated in the PR description
