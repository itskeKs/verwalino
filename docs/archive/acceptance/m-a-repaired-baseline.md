# M-A repaired baseline acceptance

Executed locally on 2026-10-02 (Europe/Berlin). A1 `1160431`, A2 `e806859`, A3
`317e5ff`, plus the A4 commit containing this report. M-A acceptance passes for
this synthetic repaired baseline. This is not product parity, invoice-format
certification, specialist/legal approval or a production release. V01–V09 remain
open. M-B–M-G delivery remains outstanding in the parity register.

## Environment and boundaries

Python 3.13, Node 26, PostgreSQL 16 on the guarded disposable tmpfs project
`hausverwaltung-declaration-test`, localhost:55432. FastAPI bound to
127.0.0.1:18000, Vite to 127.0.0.1:13000 with an explicit proxy to that API.
The existing development app/database were not used or reset. Local Poppler and
Tesseract with German language data performed real scan extraction.

Browser plugin not available; regular bundled Playwright 1.62.1 used the already
installed Chromium 153 executable. No package/browser installation, session
scraping, reference account, live provider, push, deployment or SSH was used.
Contexts were fresh and logged into synthetic Alpha/Beta accounts using the normal
login form. Screenshots cover 1440×1000 and 390×844. Screenshots/PDFs remain outside
the repository at `/tmp/hv-ma-acceptance`; compact machine-readable evidence is
retained under `docs/acceptance/evidence/`.

## Journeys and observed results

| Fixture / path | Result |
| --- | --- |
| A01/A02: text PDF, image-only German scan, XML, embedded XML → upload → detail → extraction review → linked draft → confirmation → source | Pass, amounts 119.00/119.00/119.00/476.00. Originals match local SHA-256 bytes. Confirmed sources have release timestamps and the correct cost IDs after reload. |
| A01: intercept the first save response after the backend commits, return a synthetic 503, retry from retained form | Pass, same idempotency key returns the same cost ID. No duplicate financial effect. |
| A02: encrypted PDF → worker failure → retry | Pass, explicit `encrypted_pdf`; original retained, no cost created. |
| A06: unknown advance → direct UI compute, preview, finalize | Pass, all three return structured 422 blockers. Contractual advance in the lease does not imply receipt. |
| A06: zero and 1,200.00 before/after explicit reason/completeness declaration | Pass, both fail before attestation and finalize after it. Costs are 2,033.00 (seed 1,200 + four invoices); balances are 2,033.00 and 833.00. |
| A06: draft versus finalized PDF | Draft PDF remains blocked (409); final PDF opens from its button and contains the expected amount, advance, balance and snapshot checksum. |
| A01/A06: finalized account line → cost → linked source detail | Pass. Source navigation uses an exact unambiguous frozen-snapshot line match; legacy evidence is not rewritten. |
| A31: Beta browser requests Alpha original and finalized settlement | Both denied with 404. Role-denial/RLS tests supplement this browser journey. |
| A32: 0028 → 0031 with old final snapshot/released original/numeric draft | Counts, source bytes/hash, extraction payload, snapshot hash and money sums preserved; no coverage backfill. |
| A32: disable financial computation | API regression verifies rental/WEG/asset/plan compute/finalize return 503 while existing finalized detail and PDF remain readable. |

The browser report records concrete synthetic IDs and snapshot hashes. Browser
console output contains exactly the intentional save 503 and five missing-coverage
422s; no unhandled page errors or framework overlay. Page title/URL, nonblank
content and interaction outcomes were verified.

The initial mobile capture exposed long-title/hash overflow (447 px content in a
390 px main viewport). Scoped account-view wrapping fixes it; a read-only viewport
rerun measured 390/390 and verified opening the navigation, following Belege and
closing the drawer. Data-table scrolling remains local to its wrapper. The
viewport-only rerun reused the prior valid financial evidence.

## Checks and independent review

The A4 integrated backend run exposed 17 failures from attempting to read a source
ID absent on legacy relational lines and two worker tests sharing older queued
fixtures. Both were corrected: source IDs now come from the frozen payload and
those worker tests use dedicated organizations/queues. The affected four-module
rerun passed 99 tests; the other 67 tests passed in the initial integrated run.
Earlier A3 accounting/engine/RLS/name checks passed 248 tests and the linked-plan
delta passed 36. Frontend: 61 tests passed; lint/build passed, including after the
mobile wrapping change. The existing approximately 566 kB bundle warning remains.

Independent read-only reviews found and resolved A1's source-version race, A2's
queue-full original rollback and A3's stale linked-plan gap. A3 delta and A4 source
navigation/rollback reviews found no unresolved integrity finding. Runtime
throughput of organization-level financial source locking remains an M-G gate.

## Reproduction

Do not run pytest/reset scripts concurrently with the browser services. The seed
script intentionally destroys **only** the explicitly guarded disposable DB;
normal development/customer URLs are rejected before migration.

1. Start `docker compose -p hausverwaltung-declaration-test -f
   infra/docker-compose.declaration-test.yml up -d --wait`.
2. Provide explicit `HV_DATABASE_URL` (async app_rw),
   `HV_DATABASE_URL_MIGRATOR` (async app_migrator), and `HV_DATABASE_URL_SYNC`
   (sync app_migrator), all on localhost:55432/hausverwaltung using that compose
   profile's credentials. Set `HV_DOCUMENT_STORAGE_ROOT=/tmp/hv-ma-acceptance/storage`.
   Exclude `USER` from every child command.
3. From repository root run
   `env -u USER backend/.venv/bin/python scripts/acceptance/m_a_fixture.py --reset-disposable --output /tmp/hv-ma-acceptance`.
   This seeds revision 0028, asserts count/hash/money preservation through 0031,
   and writes the synthetic fixture manifest and five source files.
4. From backend start
   `env -u USER .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 18000`.
   With `HV_DATABASE_URL_WORKER` supplied through the existing worker secret store,
   start `env -u USER .venv/bin/python -m app.workers.invoice_extraction --organization UUID`,
   using `org_a_id` from the manifest and the same app database/storage environment.
5. From frontend use the existing Vite installation with the explicit local proxy:

   ```sh
   env -u USER node --input-type=module -e 'import {createServer} from "vite";const s=await createServer({server:{host:"127.0.0.1",port:13000,strictPort:true,proxy:{"/api":{target:"http://127.0.0.1:18000",changeOrigin:true}}}});await s.listen();'
   ```

6. Point `HV_PLAYWRIGHT_PACKAGE` at an installed Playwright package and
   `HV_CHROMIUM_EXECUTABLE` at its available Chromium executable. Run
   `env -u USER node scripts/acceptance/m_a_browser.cjs` from repository root.
   Use `--viewport-only` for read-only layout/navigation rechecks after a successful
   run. Full financial reruns require the fresh fixture; they deliberately create
   real synthetic costs and final accounts.
7. Stop the task-owned API, Vite and worker processes. Stop the disposable compose
   project when no subsequent milestone needs it; preserve external evidence files.

Rollback retains the new schema/evidence. Disable invoice claims with
`HV_INVOICE_EXTRACTION_ENABLED=false`; disable financial calculations with
`HV_FINANCIAL_COMPUTATION_ENABLED=false`. Do not deploy old unknown-to-zero logic
or remove source uniqueness/evidence tables in a live rollback. Specialist period
policy, broader scoped authorization, package finalization and provider integrations
remain the later milestones' work.
