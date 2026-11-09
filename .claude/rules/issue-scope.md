---
description: Issue cuts, the three-round freeze, the merge and no session messaging
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
- At the merge the merging session ticks the acceptance boxes of the issue that the PR body
  proves, and reopens the issue when a box has no proof.
- The merge is a merge commit, never squash; the merging session writes the merge comment:
  reviewed SHA, gates run on that SHA with results, user check outcome, follow-up issues.
- Sessions do not message each other. Everything another session needs is a comment on the
  issue or the PR.
