# Acceptance from the user's side

Tests check that the code does what an issue says; this folder checks that the product works for a
user. It holds the **core journeys**, the flows the product exists for, written in the user's
words, and the **sample data** with the correct results worked out by hand. A blind acceptance run
(an agent or a person who sees only the running product) walks the journeys and compares what it
sees with these results. Agent runs never happen on production; where they do happen is
[below](#where-a-run-happens).

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

A blind run finds what nobody wrote down; a twin keeps it found. The blind run reports everything
it sees, with a class guess per finding; the session that started it confirms each finding and
records it as in [AGENTS.md](../../AGENTS.md#work-comes-from-issues) (bundle, covering issue or
inbox line; a new issue only for the cases named there). Every confirmed finding first fails as a
step in the twin of its journey, then the fix turns it green, so CI holds it from then on and the
next blind run reports only what is new. A twin is written with the first issue that touches its
journey, not in advance.

## The user check in an issue

An issue whose change a user notices carries a "User check" section in the same form: what the
user does, what they then see with concrete values, and who checks. It is written before the
change, and the PR is merged only once it has passed. A change to a core journey updates
[journeys.md](journeys.md) in the same PR.

## Where a run happens

| Run | Environment | Started by |
| --- | --- | --- |
| A user check of one PR | A local instance of the PR's commit on the shared test database, `127.0.0.1:18000`, self-hosted mode | `scripts/acceptance-env.sh` |
| The core journeys at a milestone | A portable install ([AGENTS.md](../../AGENTS.md), "Databases and containers"), removed per [acceptance cleanup](../operations/acceptance-cleanup.md) | `scripts/self-host.sh` |

The per-PR environment is built from the commit, not from the checkout, so it runs beside any
worktree and shows exactly what the PR changed. The script has four subcommands:

- `start <commit>` takes `/tmp/hausverwaltung-acceptance.lock` and `/tmp/verwalino-testdb.lock`
  (a second `start` is refused while either is held), unpacks the commit with `git archive` into a
  scratch directory under `/tmp`, installs and builds it there, brings the test database to the
  commit's migration head and runs the app detached. The database is migrated up when the commit
  carries a newer revision; it is refused when it is ahead of the commit, and also when the schema
  was built by other migration scripts than the commit's (a parallel branch can own the same
  revision number), judged by the same schema-origin note the backend tests keep on the database,
  which `start` writes again after a migration. It prints the URL, the PID and the scratch path
  once `/api/health/live` answers. The test database must already be running; the script starts none.
- `create-organization <name> <admin-email>` creates an empty organization with an administrator
  through the installation's own bootstrap command and prints a sign-in that it has checked. A name
  that already exists in the test database is refused before anything is written, because the
  bootstrap command would add the administrator to that organization; an address that already
  exists is refused too. The script records the id of the organization it created.
- `erase-organization <name>` runs the product's erasure for the organization of this run recorded
  under that name, by its id, once its deletion was confirmed in the product. The seven-day waiting
  time is simulated: the clock of that one command runs eight days ahead, and the output says so.
  No other organization is touched. Afterwards the administrator address can be used again.
- `stop` ends the app after checking that the process is the one `start` launched (and says when
  the app was already gone), removes the scratch directory, releases both locks and reports that the
  port is free. It refuses to signal any other process, and deletes only a directory that carries the
  script's name pattern and marker and is not a link. The record of the run is the acceptance lock,
  not the scratch directory, so a `stop` that was interrupted can simply be run again: a scratch
  directory that is already gone or half removed does not keep the locks. A directory that fails the
  checks is left in place and named, with the exit status 1, after the locks are released.

The instance runs only the API: it has no provider and no declaration worker. For a user check that needs the
declaration import, the launching session (never the tester) processes the queued import with the offline
worker of the browser twin, once the tester has started it. No provider is called; the output says which model
was asked for which page, and both model ids come from the settings the tester saved in the product. From a
checkout of the PR's commit, with the test database settings and the instance's document directory:

```sh
cd <repository>/backend && . ../scripts/test-env.sh && uv run --locked --extra dev --extra ops --extra ocr \
  python -m tests.offline_declaration_import --organization "<organization name>" \
  --documents "$(sed -n 's/^scratch=//p' /tmp/hausverwaltung-acceptance.lock/instance)/documents"
```

It knows the fictional Lindenallee declaration only, exits with 1 when an import does not reach review and
changes nothing outside the instance's organization.

The tester gets the URL and the sign-in, nothing else. While an instance runs, no backend test
process may use the test database, because the lock stays with the run until `stop`. Production
(`docker-compose.prod.yml`) is never involved.

## Runs and rounds per PR

This section is the maintainer brief and the review checklist for a PR's acceptance and review
rounds ([AGENTS.md](../../AGENTS.md), "Work comes from issues").

- **Two blind runs at most per PR.** A repeated run checks only the steps that changed since the
  previous run, not the whole journey again.
- **Count the rounds.** Every verifier or review round on the PR is numbered. After the third
  round the scope is frozen. Limit further review to verifying fixes for the four blocker
  categories below.
- **What still blocks after the freeze.** The PR merges when CI is green and no finding remains in
  the four blocker categories: wrong money amount, organization isolation, GoBD immutability,
  GDPR.
- **Everything else is recorded.** A finding outside those four categories, from a review or a
  blind run, goes to one place as in [AGENTS.md](../../AGENTS.md#work-comes-from-issues): a
  comment line on its class's bundle, on the open issue that covers it, or on the inbox. It does
  not start another round or run, and the merge comment names what was recorded where.

## Mandatory last step: cleanup

After every run, including a failed or abandoned run, the launching session must remove the
disposable acceptance stack completely using the
[label-filtered cleanup commands](../operations/acceptance-cleanup.md), or, for a per-PR instance,
run `scripts/acceptance-env.sh stop`.
Stop every background process and dev server the run started; remove its scratch directories,
temporary screenshots and inactive lock files. Remove its worktree and local branch only after
the PR is merged or closed, following [Worktrees and cleanup](../../AGENTS.md#worktrees-and-cleanup).
Leave other sessions, the shared test services and production untouched. End the status/report
with one cleanup line; never write a cleanup report into the repository.
