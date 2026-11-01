# Acceptance from the user's side

Tests check that the code does what an issue says; this folder checks that the product works for a
user. It holds the **core journeys**, the flows the product exists for, written in the user's
words, and the **sample data** with the correct results worked out by hand. A blind acceptance run
(an agent or a person who sees only the running product) walks the journeys and compares what it
sees with these results. Agent runs happen on a portable install, never on production
([AGENTS.md](../../AGENTS.md), "Databases and containers").

The journeys and the expected values are the owner's: they change only with an owner decision,
never to make a run pass. An expected value comes from the domain (law, a standard, a bank format,
a calculation by hand), never from reading the implementation.

## Files

| File | Holds |
| --- | --- |
| [journeys.md](journeys.md) | The core journeys, each with goal, steps in the user's words and expected results |
| `data/` | Sample files a user would have (a bank export, a scanned document), referenced by the journeys |
| `features/journeys/` (repository root) | The automated twin of each journey: a browser scenario tagged `@journey` that runs in CI ([features/README.md](../../features/README.md#journey-twins)) |

No run results live here: a run reports in the PR comment or in the milestone issue, not as a file
in the repository.

## Writing a journey

- **A goal, not a click path.** "The landlord wants to know who has paid" rather than "click
  Import, then Assign". Where the person has to search or guess, that is a finding.
- **Values, not properties.** "100.00 € open for April" is checkable, "the balance is correct" is not.
- **One sample world.** The journeys build on one invented data set (one property, two tenants,
  one account), so later journeys use what earlier ones created.
- **Who checks.** `agent` by default; `owner` only where an agent cannot go (a real device, real
  mail delivery, real payments, a judgement of tone).
- **Narrow screens.** Name the views a journey also checks at 360 px.

## Twins and findings

A blind run finds what nobody wrote down; a twin keeps it found. Every finding the owner confirms
first fails as a step in the twin of its journey, then the fix turns it green, so CI holds it from
then on and the next blind run reports only what is new. A twin is written with the first issue
that touches its journey, not in advance.

## The user check in an issue

An issue whose change a user notices carries a „User check“ section in the same form: what the
user does, what they then see with concrete values, and who checks. It is written before the
change, and the PR is merged only once it has passed. A change to a core journey updates
[journeys.md](journeys.md) in the same PR.

## Mandatory last step: cleanup

After every run, including a failed or abandoned run, the launching session must remove the
disposable acceptance stack completely using the
[label-filtered cleanup commands](../operations/acceptance-cleanup.md).
Stop every background process and dev server the run started; remove its scratch directories,
temporary screenshots and inactive lock files. Remove its worktree and local branch only after
the PR is merged or closed, following [Session cleanup](../../AGENTS.md#session-cleanup).
Leave other sessions, the shared test services and production untouched. End the status/report
with one cleanup line; never write a cleanup report into the repository.
