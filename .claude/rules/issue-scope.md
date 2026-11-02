---
description: Cut large issues before coding and freeze a PR's scope after three review rounds
---

# Issue size and review rounds

- Cut before coding: an issue that touches more than one domain mechanism (for example a ledger
  plus its evidence rules plus an import) is split into issues of one mechanism each, one PR per
  issue, before the first branch is created. The session that notices the size proposes the cut in
  the issue and waits for the owner's word only if the cut changes the acceptance.
- Three rounds, then freeze: after the third verifier or review round on a PR its scope is frozen.
  The PR merges when CI is green and no finding remains in the four blocker categories (wrong
  money amount, organization isolation, GoBD immutability, GDPR). Every other finding becomes a
  follow-up issue with a user check, not another round. Blind acceptance runs at most twice per
  PR; a repeated run checks only the changed steps
  ([docs/acceptance/README.md](../../docs/acceptance/README.md#runs-and-rounds-per-pr)).
