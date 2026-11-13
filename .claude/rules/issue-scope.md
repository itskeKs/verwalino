---
description: Issue cuts, recording findings, the three-round freeze, the merge and no session messaging
---

# Issue size and review rounds

- Cut before coding: an issue that touches more than one domain mechanism (for example a ledger
  plus its evidence rules plus an import) is split into issues of one mechanism each, one PR per
  issue, before the first branch is created. The session that notices the size proposes the cut in
  the issue and waits for the owner's word only if the cut changes the acceptance.
- A finding outside the scope is recorded, never extra code and not an issue of its own by
  default. A finding is what a review or an acceptance run saw and the session confirmed against
  code or logs; a class is a kind of defect that one mechanism fixes at many places. Record it in
  exactly one place and say in the PR description where:
  - one comment line on the open bundle of its class (label `bundle`: one open issue per class, one
    acceptance box per place plus a guard);
  - one comment line on the open issue that already covers it;
  - one comment line on the standing inbox issue (label `inbox`), when neither exists;
  - a new issue, only as the next bullet allows, from `.github/ISSUE_TEMPLATE/task.md` with a line
    "Follow-up from PR #M (issue #N)".

  Line format, several lines may share one comment:
  `- [ ] <class or ?> | <screen or file:line> | <what was seen; UI text verbatim> | <SHA> | PR #<n>`
- Immediate issue only for the four blocker categories, security, data loss, or a step of a core
  journey a user cannot finish, after searching open issues (comment there if one covers it).
  Outside that: at most one new issue per PR, only for a self-contained defect on the PR's own
  journey that has no class.
- Triage run: when the inbox holds 15 or more open lines, after five merged PRs, or on the owner's
  word. Every line gets a disposition next to it and is ticked: `issue #n`, `bundle #n` (a new
  bundle when three or more lines share a class), `covered by #n` or `dismissed: <reason>`. A
  blocker-category line is never dismissed. The run changes no code.
- A class is closed only with a guard (test, lint rule or check) that fails when it reappears; a
  bundle's acceptance names the guard or says why none is possible.
- Pick work by tier and order, not by issue number: labels `tier:1` (launch blocker), `tier:2`
  (before launch), `tier:3` (after launch).
- Before a question goes to the owner, read the recorded decisions (issue comments starting
  "Owner decision"); a question one of them settles is answered on the issue, not asked again.
- Three rounds, then freeze: a round is a fresh review of the open PR plus the push that fixes
  its findings; the implementer's own verifier pass before the PR does not count. After the third
  review the scope is frozen: only findings in the four blocker categories (wrong money amount,
  organization isolation, GoBD immutability, GDPR), red CI, a failed user check and conflicts are
  still fixed; every other finding is recorded as above, not another round.
  At most five rounds: a finding that still needs a push after the fifth means no merge. Blind
  acceptance runs at most twice per PR; a repeated run checks only the changed steps
  ([docs/acceptance/README.md](../../docs/acceptance/README.md#runs-and-rounds-per-pr)).
- At the merge the merging session ticks the acceptance boxes of the issue that the PR body
  proves, and reopens the issue when a box has no proof.
- The merge is a merge commit, never squash; the merging session writes the merge comment:
  reviewed SHA, gates run on that SHA with results, user check outcome, what was recorded where
  (lines appended, issues filed).
- Sessions do not message each other. Everything another session needs is a comment on the
  issue or the PR.
