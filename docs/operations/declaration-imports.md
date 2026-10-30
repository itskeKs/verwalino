# Operating the Declaration of Division (Teilungserklärung) Import and Building Knowledge (Gebäudewissen)

The declaration of division import is an asynchronous workflow that requires review.
`DeclarationPipeline` is the fixed, deterministic manager: it runs a bounded local
precheck, fixed extraction passes, local validations and at most one bounded vision
recheck. It is not a freely autonomous agent. Document pages, OCR text and model
responses are **untrusted data**; they receive no tools, no action sink and no
permission to change data. Only **Human Approval** promotes a reviewed draft into
canonical property, unit or rule data.

Building knowledge shows only approved facts, rules, document relationships and page
evidence. The lexical PostgreSQL search works without a model call. The optional Q&A
feature returns exclusively a cited answer drawn from a bounded retrieval of approved
evidence and can be switched off separately.

## Operating Flow and Model Matrix

```text
PDF -> local precheck/OCR -> textual Luna passes -> local evidence/domain validation
       -> RouterDecision(NONE | HUMAN_REVIEW | VISION_RECHECK)
       -> only selected, locally rendered PNG pages to Mini
       -> re-validation -> NEEDS_REVIEW -> Human Approval
```

| Purpose | Model / reasoning | Permitted input | Limit |
| --- | --- | --- | --- |
| regular extraction (`inventory`, `extract_core`, `extract_rules`, `reconcile`) | `gpt-5.6-luna`, `medium` | bounded text/OCR batches or structured intermediate results | no images, no tools, no `previous_response_id` |
| Vision recheck | `gpt-5.4-mini`, `none` | only locally rendered PNG pages selected by `RouterDecision`, plus bounded context | no regular Luna passes with images; a rendering failure is fail closed |
| Eval candidates | Luna `low`, Terra and Luna with Mini router | exclusively the same gold set | no automatic promotion and no production routing |

The local `route_escalation` router uses only reproducible signals. It is not a learned
or online-learning router and uses no model confidence:

| Priority | Local signal | `RouterDecision` |
| ---: | --- | --- |
| 1 | isolated router contract: `PROVIDER_REFUSAL`, `PROVIDER_INCOMPLETE`, `PROVIDER_TIMEOUT` or `PROVIDER_SCHEMA_FAILURE` | `HUMAN_REVIEW` |
| 2 | unknown document or page outside the precheck | `HUMAN_REVIEW` |
| 3 | missing evidence quote on a known, renderable page | `VISION_RECHECK` |
| 4 | low local page quality on a cited/blocker page | `VISION_RECHECK` |
| 5 | domain blocker without a visual signal | `HUMAN_REVIEW` |
| 6 | no signal | `NONE` |

The router sorts and deduplicates pages and limits them with
`HV_DECLARATION_MAX_RECHECK_PAGES`; additional calls remain bounded by
`HV_DECLARATION_MAX_RECHECK_CALLS` and by token, cost and deadline limits. A selected
page that cannot be rendered is not uploaded in an alternative form and leads to Human
Review.

The production pipeline path currently calls the router with
`provider_failure=None`. Provider failures are therefore not redirected to vision,
but follow the existing worker policy directly to `RETRY_WAIT` or `FAILED`. The first
table row documents only the isolated, total router contract: if one of its four
permitted provider failures is passed there, only `HUMAN_REVIEW` is permitted.

## Local Precheck and Evidence-Gate

A maximum of **25 MiB** applies per PDF (`HV_DECLARATION_MAX_PDF_BYTES`), and by
default ten documents and 500 pages per import. The precheck rejects unreadable,
encrypted or active PDFs as well as embedded files. It uses the text layer first and
OCR only when needed.

| Area | Default limit |
| --- | ---: |
| OCR resolution / time per page | 180 DPI / 30 seconds |
| rendered pixels per page | 20,000,000 |
| temporary OCR data per import | 256 MiB |
| OCR pages / pixels per import | 200 / 400,000,000 |
| precheck deadline | 600 seconds |
| provider pages / characters per batch | 20 / 80,000 |
| input / output tokens per import | 500,000 / 60,000 |
| output tokens per provider call | 12,000 |
| recheck pages / recheck calls | 12 / 2 |
| candidates / estimated import cost | 5,000 / 1.00 USD |

Every **Evidence-Quote** must appear locally on the exact
`(document_key, pdf_page)` given. The validator normalizes Unicode, line breaks,
whitespace and hyphens and requires a non-empty, contiguous match. A page number,
section number or model text is not a substitute for evidence. Error codes such as
`EVIDENCE_QUOTE_NOT_FOUND`, `UNKNOWN_EVIDENCE_DOCUMENT` and
`EVIDENCE_PAGE_OUT_OF_RANGE` contain no document text and must not carry it into the
ledger, logs or tickets either.

Common precheck errors are `PDF_SIZE_LIMIT_EXCEEDED`,
`PDF_PAGE_LIMIT_EXCEEDED`, `PDF_UNREADABLE`, `PDF_ENCRYPTED_FORBIDDEN`,
`PDF_ACTIVE_CONTENT_FORBIDDEN`, `PDF_EMBEDDED_FILES_FORBIDDEN`,
`OCR_UNAVAILABLE`, `OCR_TIMEOUT`, `OCR_FAILED`,
`OCR_RENDERED_PIXELS_LIMIT_EXCEEDED`, `OCR_IMPORT_PAGE_LIMIT_EXCEEDED` and
`OCR_IMPORT_PIXEL_LIMIT_EXCEEDED`.

## Provider Failures, Recovery and Safe Telemetry

Content outcomes, transport errors and usage metering are separate classes; they must
not be merged into a single “four outcomes” schema:

| Class | Handling |
| --- | --- |
| `PROVIDER_REFUSAL` | no schema repair and no data retransmission; existing worker policy |
| `PROVIDER_INCOMPLETE` | its own bounded worker retry; no schema repair |
| `PROVIDER_SCHEMA_FAILURE` | exactly one schema-only repair with the same bounded input; afterwards a final failure |
| `PROVIDER_TIMEOUT`, `PROVIDER_CONNECTION_ERROR`, `PROVIDER_RATE_LIMIT`, `PROVIDER_SERVER_ERROR`, `OPENAI_REQUEST_FAILED` | transport group; retryability follows the individual stable code and the worker policy |
| `PROVIDER_USAGE_INVALID` (concretely `OPENAI_USAGE_INVALID` in the OpenAI adapter) | separate usage metering; cost/usage contract fails closed, no publication |

The worker claims jobs per tenant with a lease, token and generation.
Heartbeats extend only the current generation. On interruption or a retryable error,
the job moves to `RETRY_WAIT`; late results cannot publish. `POST /api/declaration-imports/{id}/retry` is intended only
for retryable errors; `cancel` locks out the current lease. Before a retry, check
cost/token counters, error class and limits; do not repeat blindly.

## Services, Recovery and Status

For local development, the API and the import worker run separately:

```bash
cd backend
HV_SESSION_COOKIE_SECURE=false ./.venv/bin/uvicorn app.main:app --reload
```

```bash
cd backend
./.venv/bin/python -m app.workers.declaration_import
```

In operation, both services are started with a protected external env file:

```bash
docker compose --env-file /absolute/path/hausverwaltung.env \
  -f docker-compose.prod.yml up -d app worker
```

Before a restore, back up the database and the `documents` volume as a consistent pair
and restore only from the same backup generation. After the restore, check PostgreSQL
integrity and the stored SHA-256 values, then start the API and worker. A one-sided
restore can cause document references and stored content to diverge and is not a valid
recovery path.
Status diagnostics use exclusively metadata such as `status`,
`error_code`, `current_stage`, tokens, estimated cost and attempts —
never document text or provider payloads.

The durable pass ledger has **one row per SDK attempt**. It references
`declaration_extraction_run_id`; the repository call is named
`append_pass_entries`. The pipeline empties its queue only after a
committed checkpoint, fail or publish transition. The table is
tenant-isolated via RLS, append-only, and the database validates exactly 23
safe JSON fields, types, allowlists and the binding of the attempt number.
The Python and DB contracts must therefore be changed together.

Only technical metadata is permitted, such as pass ID, attempt number, model,
reasoning effort, prompt/schema/harness version and hash, token partition,
estimated cost, latency, retry counter, `route_action`, reason codes and
failure class. Not permitted are prompt text, OCR/PDF text, quote, image,
response body, local path, tenant/property/user ID, headers, API key and
exception text. Status endpoints stay with their aggregated metadata.

## Caching, Costs and Provider Data

Luna places the static developer block before dynamic document data and
uses an explicit cache breakpoint and a 30-minute cache. The
stable key contains only model, pass, and prompt, schema and
harness version/hash. Mini receives **no** explicit caching. The
configuration for Luna cache writes is
`declaration_primary_cache_write_multiplier` or
`HV_DECLARATION_PRIMARY_CACHE_WRITE_MULTIPLIER` and is validated to `1.25`.

Costs are calculated conservatively from ordinary input, cached input,
cache write and output tokens. Invalid partitions, negative
counters, missing rates or cache writes for Mini fail closed to
`PROVIDER_USAGE_INVALID`; a cheaper estimate does not justify a loss of
quality.

`HV_OPENAI_API_KEY` is an exclusively server-side deployment secret:
never in the frontend, Git, project `.env`, browser or ticket. Only after the local
precheck are bounded, permitted page contents sent to the provider.
`store=false` is set, **is not ZDR** and triggers no retention commitment.
API data is not used for training by default; without approved extended controls,
abuse monitoring may retain content for up to 30
days. Before production, the DPA (AVV), region/data residency,
retention/ZDR suitability, permissions and GDPR (DSGVO) assessment must be reviewed and
documented.

## Review and Domain Limits

A successful run ends in `NEEDS_REVIEW`, never in application of the draft.
Reviewers compare every fact and every rule with the page evidence, edit
or discard the draft, and only then take responsibility.
Conflicts remain `NEEDS_CLARIFICATION`; the system gives no legal advice. MEA
(co-ownership shares, Miteigentumsanteile) are to be treated as numerator/denominator;
a scale is never silently supplemented or converted.

## Worker Network Boundary

In production, `backend` remains an internal Docker network: database,
migration and bootstrap are exclusively there, the API additionally only in the
shared Traefik network. Only the declaration worker is additionally attached
to the project-local non-internal network `egress`; it is never attached to Traefik and
no service publishes host ports. Its explicit Docker gateway
priorities are `backend: 0` and `egress: 1`: the default route gateway is
therefore in the egress network, and the database remains reachable exclusively via
`backend`.

The OpenAI client is hard-wired to `https://api.openai.com/v1`. The harness has
no URL, browser or tool capability and transmits only the intended
structured output requests. When imports are enabled, the worker checks the two
configured model IDs via the non-billable Models
API (short timeout, at most one SDK retry) before a promotion. For this, the deploy starts, after
the API health check but before the consuming worker, a one-off
`compose run --rm --no-deps` container with an explicit Python entrypoint. The
normal worker entrypoint and its endless loop do not run in it. Neither
PDF/prompt contents nor Responses calls are transmitted, and the key
is read exclusively from its container environment. A failure preserves
the previous runtime state and prevents the promotion.

The non-internal Docker network is not a domain firewall. Anyone who must restrict egress to OpenAI
additionally needs a host/network allowlist. The
isolated Compose verifier additionally checks the running default route against
the Docker egress gateway, but with its placeholder key triggers no
external provider call.

## Local Tests and Gold Evals

Normal tests use only synthetic/redacted data and no network calls.
The ephemeral test database runs only on port 55432:

```bash
docker compose -p hv-declaration-tests -f infra/docker-compose.declaration-test.yml up -d --wait
unset hv_database_url hv_database_url_migrator hv_database_url_sync database_url database_url_migrator database_url_sync
export HV_DATABASE_URL='postgresql+asyncpg://app_rw:app_secret@localhost:55432/hausverwaltung'
export HV_DATABASE_URL_MIGRATOR='postgresql+asyncpg://app_migrator:migrator_secret@localhost:55432/hausverwaltung'
export HV_DATABASE_URL_SYNC='postgresql://app_migrator:migrator_secret@localhost:55432/hausverwaltung'
export HV_ENVIRONMENT=test
cd backend
./.venv/bin/python -m app.evals.declaration_knowledge --fixtures tests/fixtures/declaration_eval_cases.json --provider fake
```

Promotion is fail closed. The gold set requires at least the neutral IDs
`gold-units-mea`, `gold-heating-amendment` and
`gold-adversarial-evidence` as well as the fixed comparison labels `luna_medium`,
`luna_low`, `terra_primary` and `luna_with_mini_router`. Quality must not regress in
any representative dimension; lower costs do not compensate for this. Unlabeled real PDFs yield at most a
descriptive smoke report, **no accuracy claim and no promotion**.

A real PDF smoke test is never to be run automatically and produces only a
`descriptive_only` report: it is not promotion-eligible. It requires a
separate explicit authorization and compares exactly `luna_medium`,
`luna_low`, `terra_primary` and `luna_with_mini_router`.

**Phase A — Activation and values:** Before **any path method**, exactly
`HV_DECLARATION_LIVE_EVAL=1`, a server-side API key,
`--provider live --live --compare-models`, a non-empty value for
`HV_DECLARATION_REAL_PDF_MANIFEST` and the three positive, caller-verified
Terra rates are checked. If any of these values is missing or invalid,
no path/manifest/PDF/provider access and no transmission takes place.

**Phase B — local external input check:** Only then are the report and
manifest paths checked locally: both must be located externally, the passed manifest
must exactly match `HV_DECLARATION_REAL_PDF_MANIFEST`, comply with case limits and pass the
SHA-256 check per case. In this phase, the manifest and declared PDFs are read and hashed locally. A mismatching hash
prevents capability, adapter, provider and transmission — it does not claim
that the file was never opened. Only after a successful Phase B is the
capability created and an adapter constructed. Manifest, PDFs and report
never belong in the repository.

```bash
cd backend
./.venv/bin/python -m app.evals.declaration_knowledge \
  --fixtures tests/fixtures/declaration_eval_cases.json \
  --provider live --live --compare-models \
  --manifest /absolute/external/path/manifest.json \
  --report-dir /absolute/external/path/reports
```

The three Phase A rates are named
`HV_DECLARATION_COMPARE_TERRA_INPUT_USD_PER_MILLION`,
`HV_DECLARATION_COMPARE_TERRA_OUTPUT_USD_PER_MILLION` and
`HV_DECLARATION_COMPARE_TERRA_CACHED_INPUT_USD_PER_MILLION`.
The presence of `HV_DECLARATION_REAL_PDF_MANIFEST` is a Phase A gate; only
in Phase B is its value checked as an absolute external path for equality with the
passed manifest and then validated locally.

## Research Context

The structure follows [Harness Engineering](https://openai.com/index/harness-engineering/):
repo legibility, enforceable boundaries, tests and evals — not autonomy.
The model, vision, structured output, cache and data notes are aligned with the
[Latest model guide](https://developers.openai.com/api/docs/guides/latest-model),
[GPT-5.4 Mini](https://developers.openai.com/api/docs/models/gpt-5.4-mini),
[Vision](https://developers.openai.com/api/docs/guides/images-vision),
[Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs),
[Prompt Caching](https://developers.openai.com/api/docs/guides/prompt-caching)
and [Your data](https://developers.openai.com/api/docs/guides/your-data).
Privacy and operating assumptions must additionally be checked against the
[business data notes](https://openai.com/business-data/) of the chosen
contract. [Evals](https://openai.com/index/evals-drive-next-chapter-of-ai/)
justify the fail-closed promotion instead of a cost decision made by gut feeling.
The routing literature ([RouteLLM](https://proceedings.iclr.cc/paper_files/paper/2025/file/5503a7c69d48a2f86fc00b3dc09de686-Paper-Conference.pdf),
[RouterEval](https://aclanthology.org/2025.findings-emnlp.208.pdf)) motivates
comparison and measurement; without representative labeled data, production
routing deliberately remains deterministic. The document boundary also follows
the recommendations on [prompt injection resistance](https://openai.com/index/designing-agents-to-resist-prompt-injection/).

## Shutting Down Operation Cleanly

Locally started API and worker processes are terminated in their respective foreground with
`Ctrl-C`. Stop the isolated test database only with its exact
project name:

```bash
docker compose -p hv-declaration-tests -f infra/docker-compose.declaration-test.yml down
```

Real user PDFs, manifest data, API keys and provider payloads stay
outside Git, test fixtures, reports, logs and tickets.

## Explicit queue principals (migration 0043)

New start/retry commands require a UUID `Idempotency-Key`. Keep the key when a
response is lost. An identical replay returns its `command_receipt_id` alongside
the current `queue_request_id` and import state; it never queues work again or
replaces a later principal. A fresh command against active work conflicts. The
UI retains its command key across request failures. Queue history is immutable
and tenant-safe; `initiated_by` remains the original draft creator.

Workers resolve the triggering actor from the current receipt, then check active
writer membership under the shared ACL lock before claiming, heartbeat, source
reads, target-data reads, budget reservation/checkpoint and publication. Lease
recovery retains that receipt. Unknown old queue principals are not guessed from
the creator: they are cancelled as `JOB_PRINCIPAL_UNVERIFIED`. A revoked,
deactivated or non-writer actor causes `JOB_PERMISSION_CHANGED`. A currently
authorized writer can explicitly retry these permission cancellations with a new
key; ordinary user cancellation remains terminal. Run budgets still apply.

Cancellation commits separately from the failed effect transaction, fences stale
workers, closes running extraction records, and preserves source originals and
persisted budget reservations. Both pipeline- and heartbeat-origin cancellation
are reported as permission changes. Already-dispatched provider work cannot be
recalled; its output cannot publish after revocation. No database transaction is
held across PDF parsing, OCR or provider I/O. Populated downgrade refuses to erase
queue history; repair forward. Worker readiness requires the new queue column.

This is an organization-level live-principal prerequisite. It does not grant a
property-source or accounting-scope entitlement, automatically classify old
sources, activate live providers or satisfy the full B1 rollout gate. Browser
acceptance uses synthetic local originals and the production cancellation helper
against the guarded disposable database; it does not run a live provider.

Interactive declaration endpoints also revalidate the current writer role inside
the transaction that handles source access and review/approval. A role downgrade
between initial authentication and this check returns 403; deactivation or missing
membership returns 401. A role change concurrent with an already-authorized short
request waits for that request's shared ACL lock. Subsequent requests use the
changed permissions. Approved property-document metadata/PDF routes require a
currently active member; their unclassified read-only compatibility is retained.
This check does not confer accounting-scope access. The optional knowledge-provider
workflow uses the separate per-phase principal checks described below.

## Knowledge-query principal checks (migration 0044)

Approved knowledge overview, rules and lexical search now use the live-member
request guard. Optional Q&A persists the initiating actor on its content-free cost
ledger and checks active membership separately before source retrieval, reservation
and final answer validation. Read-only members retain current approved-knowledge
compatibility. No database transaction spans provider network I/O.

Deactivation or membership removal during provider execution suppresses the answer
with HTTP 401. A separate cancellation transaction records `QA_PERMISSION_CHANGED`
and conservatively retains the full reserved amount as estimated cost, with a safe
audit event. Already-dispatched provider input cannot be recalled. Historical null
actors stay unknown; an active member can expire a timed-out reservation without
adopting its identity. Downgrade refuses to erase recorded principals; repair
forward. This is an organization-level prerequisite, not accounting-scope source
classification or authorization to enable a live provider.

## Scheduling metadata before source access (migration 0046)

Workers require `next_declaration_queue_metadata` and
`lock_declaration_lease_metadata` at startup. These tenant-fenced helpers expose
only bounded scheduling/fence metadata and perform ACL-before-row locking. The
worker resolves and sets its persisted queue principal before loading the import
through ordinary RLS. A hidden revoked job can still be safely cancelled through
the existing exact-fence cancellation helper; absence from a source query is not
used as evidence that no job exists. Lease expiry is checked with the database
clock after lock waits. Provider I/O remains outside database transactions.

No production source policy is enabled by this migration. Property-data-domain
checks must join queue-principal resolution before that later activation. Do not
remove the source-query policy to make an actorless worker find jobs. Downgrade
removes only the two scheduling helpers and preserves all queue and source data;
updated workers will report schema-not-ready until the helpers are restored.

## Database worker commands (0070)

Declaration workers require their own `app_worker` connection through
`HV_DATABASE_URL_WORKER`. There is no fallback to the web URL. Provision the login
before migration; fresh development/production bootstrap scripts include it. For
an existing production volume use the separately authorized procedure in DEPLOY.md.
Never reset a populated volume to rerun init scripts. The web container must not
receive the worker credential.

`app_worker` inherits only `app_rw` and has no member roles. Neither runtime role
may have superuser, BYPASSRLS, CREATEROLE, CREATEDB or REPLICATION. Worker sessions
validate the role and membership graph; losing inheritance also fails validation.
Claim/renew/transition are worker-only database commands. Manual cancellation
remains a current-user prepare operation. Lease duration is 30–3600 seconds and
database time owns expiry/scheduling. An old token cannot renew or complete work.

Strict processing state and maximum budget cannot be changed with raw runtime SQL.
Reclaim closes abandoned attempts and installs a new fence. Reservation/accounting
corrections remain part of the trusted pipeline; recorded overrun evidence must not
be dropped merely because it exceeds a configured prospective budget.

The existing NEEDS_REVIEW revision increment is still a compatibility exception,
not proof that candidate review and audit happened atomically. Worker child-row
and review-effect command closure remain required before source activation. Do not
infer B1 completion from the accepted lifecycle-command increment.

## Worker output receipts (0071)

Workers require the effect-access helper and private context table at startup.
Each output transaction obtains its current lease through
`lock_declaration_lease_metadata`; only the actual app_worker login receives a
private receipt keyed to that database connection and transaction. Ordinary runtime
SQL cannot read or write the receipt table. A new transaction must validate the
lease again; setting a token GUC is insufficient. Expired receipts are removed in
bounded, nonwaiting batches. The table contains no source text or approval history.

Strict output triggers guard runs, passes, candidates, evidence, issues and page
counts. Manual cancellation/reclaim retains trusted cleanup. Downgrade refuses
ENFORCED/PAUSED organizations and takes NOWAIT dependency locks. Source activation
remains disabled pending atomic human-review effects and other B1 coverage.

## Atomic review decisions (0072)

Review endpoints use `apply_declaration_review` in their caller transaction. The DB
owns human actor/time, candidate versions, revision and canonical no-value audit;
strict raw writes cannot stamp those independently. The actual worker login is
excluded even though it inherits ordinary runtime function privileges. Expired or
revoked review authority returns a conflict instead of leaking SQL or source data.

Changing a decision conservatively reopens managed resolved issues. The service
refreshes DB-owned state and runs the existing graph validator before committing.
Its issue writes and complex correction semantics are still explicitly untrusted
compatibility boundaries before activation. Downgrade refuses strict mode, takes
NOWAIT locks and preserves decisions/audit history. Release gate is 0072; this code
change does not authorize deployment or source activation.

## Trusted semantic review connection (0073)

Set the API-only `HV_DATABASE_URL_REVIEW` to the independent app_review login.
Missing/unsafe configuration fails closed; ordinary web and worker connections
cannot substitute. The review role must have no inherited roles/delegates, ownership,
elevated flags, public-schema CREATE or unexpected table/column privileges. It reads
only the four review graph tables and executes narrow graph-lock/decision/issue
commands. Existing-volume provisioning requires the separately authorized procedure
in DEPLOY.md; never reset a populated volume to recreate roles.

Review mutation requests keep one transaction for authenticated context, live writer
check, graph locking, canonical correction, decision, revalidation and issue effects.
A private five-minute transaction receipt binds the final command. The repository
submits status-only effects and the DB supplies/restores canonical resolution
metadata. Role/receipt failure rolls back the transaction. Private receipts contain
no source text and expire via bounded nonwaiting cleanup.

This protects the review pipeline against ordinary runtime SQL, not compromise of
the trusted API process or review secret. Worker provider processing never receives
that credential. Source activation remains disabled pending the rest of B1.

### Review retry receipts (0074)

Both review mutation endpoints accept an optional UUID Idempotency-Key header.
First-party clients retain the key for an unchanged request after a network failure;
a changed revision, selection, decision or correction starts a new key. Replays
recheck current live writer and source access, then return the original response
without applying another decision. Refresh current review state after success.
Keys are actor/operation scoped; bulk candidate ordering is canonical. Changed
content under the same key returns IDEMPOTENCY_KEY_CONFLICT (409).

The private declaration_review_receipt table is accessed only by the isolated
review runtime command; do not grant direct runtime access. Its response can contain
source-backed candidate/evidence data and must remain protected as source material.
Do not purge receipts to resolve retry conflicts. A schema downgrade with receipts
refuses to discard them; disable writers or forward-repair instead. Source activation
remains disabled until the remaining B1 acceptance work is complete.

### Indirect report source coverage (0075)

Strict-mode account statements/open items/deadlines require one reviewed accounting
scope and complete current source READ access, including payment-only recipients.
Missing source access is a denied report, never a partial balance or empty overview.
Dunning snapshots and their provenance follow the same property audience. PDF export
also requires source EXPORT; financial permissions remain independently necessary.
PAUSED keeps authorized disclosure available and blocks dunning effects. Unclassified
legacy dunning notices require reviewed provenance before strict-mode disclosure;
contact access alone is insufficient. Privacy checks retain hidden issued notices
as blockers. Do not remove source policies or rewrite historical notices to resolve
coverage gaps. Activation remains unavailable pending reviewed legacy coverage.
