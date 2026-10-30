# Architecture and Data Model Design: M0 (Foundation & Multi-Organization Capability)

As of: 2026-06-01 · Version 0.1 (design, before implementation)
Reference: [sdd-property-management-mvp.md](sdd-property-management-mvp.md) v0.2 · [archived roadmap](archive/roadmap.md)
Author: `engine-correctness` (Opus 4.8) · Status: **Draft for confirmation by the human — no code yet, no commit**

> **Historical note:** The following sections document the
> M0 design state of 2026-06-01. They are not the status of today's
> implementation. The current, bounded declaration-of-division workflow (Teilungserklärung) is added in the
> following section; its operating contract is in the
> [Runbook](operations/declaration-imports.md).

---

## Local Invoice Worker (T10)

Invoice extraction runs as its own process, independent of the declaration-of-division
provider. The API stores the original and durably enqueues the job;
`invoice_extraction` by default determines the organization IDs in every round and
processes them serially under the isolated `app_worker` role. `--organization`
restricts the selection. Claim and result completion remain short, separate
transactions with the existing principal/source RLS, lease and fencing checks;
PDF/XML/OCR runs outside the DB transaction in the time-limited child process.
Proposals change neither costs nor manual inputs or approvals.

The dedicated healthcheck verifies local OCR, storage and DB role without queue consumption
or a provider call. SIGTERM lets a running bounded extraction run out, without
adopting results after an abort; the existing lease recovery applies.
The local launcher manages API, UI and worker with a shared, isolated
`/tmp` document storage and exclusively the test DB `:55432`. The approved,
not yet activated production service uses only the internal network and a readable
original volume; no provider, web, review or migrator credentials.
Operating details and the unchanged production release limit: [DEPLOY.md](../DEPLOY.md).
No schema/RLS change.

## Portable Operation (2026-10-23)

`scripts/self-host.sh` and the repurposed `infra/docker-compose.yml` form, alongside
the unchanged alpha, a separate first-installation path (project
`verwalino`). Web app, private PostgreSQL DB and one-off migration/admin jobs
use the existing image; only Ops receives migrator rights. The default is
loopback HTTP; optionally Caddy takes over HTTPS on a separate proxy network.
Secrets are generated locally and not loaded as shell code. AI, SMTP and
invoice OCR are disabled here. The explicit `update` command stops app writers,
backs up database/documents, migrates and checks readiness, automatically restoring
the previous image and backup on failure. See
[portable update and recovery](../DEPLOY.md#portable-update-and-recovery).
The development/test database remains exclusively `infra-postgres-1`:55432.
A real portable first installation and TLS acceptance are still pending; details in
[DEPLOY.md](../DEPLOY.md#portable-docker-first-installation).

## Current Declaration-of-Division Workflow (2026-08-29)

For declarations of division, the application uses a **fixed,
deterministic manager**, not a freely autonomous agent. The
`DeclarationPipeline` owns the fixed pass order, budgets, deadlines,
reservations, retry limits, local validation, and publication. A
model receives no tools, no hand-offs, and no action sink. PDF text,
OCR, and image pages are untrusted data: they may propose facts, but
may not determine the flow, permissions, or canonical data.

```text
approved PDF -> local pre-check/OCR -> Luna structured outputs
  -> exact local evidence-quote validation + domain checks
  -> deterministic RouterDecision
  -> optional: render selected page locally as PNG -> mini recheck
  -> validate again -> NEEDS_REVIEW -> Human Approval
```

Luna runs in the product with `reasoning.effort="medium"`. The recheck is limited to
`gpt-5.4-mini` with `reasoning.effort="none"` and exclusively to
router-selected pages. Luna `low`, Terra, and the variant with a
mini router are eval configurations, not productive auto-promotion. Each
pass is independent: there is no `previous_response_id` and no
carried-over reasoning state. The provider uses Responses API Structured
Outputs, not freely parsed JSON text.

The evidence boundary is local and page-bound: A quote is only
accepted if, after canonical normalization, it occurs as contiguous
text on exactly the claimed page. Unknown or non-renderable pages remain
with human review. The pure `RouterDecision`
decides only from local error codes, page inventory, renderability, and
local quality measurement; neither document text nor model confidence is
factored in. There is no learned/online router as long as no representative,
labeled gold set backs its change.

Vision escalation is a transient local data transfer: Only after
`VISION_RECHECK` is the exactly selected page rendered to PNG within the existing
pixel, byte, and deadline limits. If rendering fails,
no substitute image and no other page is transferred. After that, all
evidence and domain gates apply again. A successful run ends exclusively
in `NEEDS_REVIEW`; **Human Approval** remains the only path into canonical
building or knowledge data.

The technical pass ledger is separate from the domain data: an append-only,
RLS-protected row per SDK attempt, bound to
`declaration_extraction_run_id`, contains exclusively allowlist-validated
technical metadata. The cache key for Luna consists exclusively of
static model/pass/prompt/schema/harness identity; the static
developer block comes before dynamic document data. Luna uses explicit
caching with 30 minutes, Mini no explicit caching. Cost and
usage partitions are validated fail closed before persistence.

These boundaries are modeled on [Harness Engineering](https://openai.com/index/harness-engineering/)
(repo legibility, enforceable boundaries, and evals instead of autonomy), on the
[model guidelines](https://developers.openai.com/api/docs/guides/latest-model),
[vision guidance](https://developers.openai.com/api/docs/guides/images-vision),
[Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs),
and [prompt-injection resistance](https://openai.com/index/designing-agents-to-resist-prompt-injection/).
Routing research such as [RouteLLM](https://proceedings.iclr.cc/paper_files/paper/2025/file/5503a7c69d48a2f86fc00b3dc09de686-Paper-Conference.pdf)
and [RouterEval](https://aclanthology.org/2025.findings-emnlp.208.pdf) supports
the decision to measure and compare variants; it does not replace
representative domain labels.

> This document covers M0 (and the preparatory data model for M1). It makes the load-bearing architecture decisions that can hardly be retrofitted later (organization isolation, GoBD immutability). It contains **no** application code and **no** migration implementation — only design, rationale, and an actionable scaffolding plan for `bulk-impl`.
>
> **Binding status:** The SDD (v0.2) and the roadmap are the source of domain truth and remain untouched. Where this document goes beyond the SDD, this is marked as an *assumption* or *open item*. Points the human must decide are marked **[DECISION]**. The associated Architecture Decision Records are in [adr/](adr/).

---

## 0. Guiding principle and scope

**Domain claim (SDD §1):** Settlement reliability — recomputable, audit-proof, immutable results. The data model carries this claim from day 1, long before the settlement engine (M3/M4) exists.

**M0 exit criterion (roadmap):** “Two organizations can be created and are guaranteed not to see any of each other's data; roles take effect.” This criterion is the yardstick for every decision in this document.

**Four project-wide invariants** (SDD §4, §6.6, §14, §16; roadmap cross-cutting concerns), anchored in the data model from M0 and finally hardened in M9:

| Invariant | Meaning | Where anchored in M0 |
| --- | --- | --- |
| **I1 Organization isolation** | No cross-organization data access, strictly enforced. | RLS + tenant context (Section 2) |
| **I2 GoBD immutability** | Approved vouchers (Belege)/settlements are never overwritten; correction = new version; checksum + audit trail. | Append-only tables, SHA-256, version chains (Section 3) |
| **I3 Chronological integrity** | Histories (ownership, rent) are never overwritten; pro rata periods remain possible. | Temporal tables with `valid_from`/`valid_to` (Section 5) |
| **I4 Monetary exactness** | Monetary amounts are exact, never floating point. | `NUMERIC(14,2)` / `NUMERIC` for shares (Section 5.1) |

A fifth axis, distinct in domain terms, is the **segregation of funds** (SDD §4, §6.3): Funds/vouchers of different mandates or homeowners' associations (WEGs) *within* an organization are never commingled. It must be distinguished from organization isolation (Section 2.6) and is implemented in M0 as model preparation, but not yet as a second RLS axis.

---

## 1. Architecture overview

### 1.1 System boundaries (M0)

```
            ┌──────────────────────────────────────────────────────────┐
            │                  Responsive web app (React/TS)            │
            │         Desktop-first, mobile-friendly (browser)          │
            └───────────────────────────┬──────────────────────────────┘
                                         │  HTTPS / JSON (API-first)
                                         ▼
   ┌───────────────────────────────────────────────────────────────────────┐
   │                          Backend (API-first)                           │
   │                                                                         │
   │  ┌───────────────┐   ┌────────────────────┐   ┌──────────────────────┐ │
   │  │ Auth /        │   │  Tenant-Context-    │   │  Domain-Services     │ │
   │  │ Session-Layer │──▶│  Middleware         │──▶│  (M0: Org, User,     │ │
   │  │ (Login, RBAC) │   │  (sets org_id in    │   │   Mandates)          │ │
   │  └───────────────┘   │   DB session GUC)   │   └──────────┬───────────┘ │
   │                      └────────────────────┘              │             │
   │                                                          ▼             │
   │  ┌──────────────────────────────────────────────────────────────────┐ │
   │  │  Persistence layer (Repository/ORM)                               │ │
   │  │  - All tenant queries run through an org-bound                    │ │
   │  │    DB connection context                                          │ │
   │  └──────────────────────────────────────────────────────────────────┘ │
   └───────────────────────────────────┬───────────────────────────────────┘
                                        │
                       ┌────────────────┴───────────────┐
                       ▼                                 ▼
        ┌──────────────────────────┐      ┌──────────────────────────────┐
        │ PostgreSQL                │      │ Object storage (vouchers)     │
        │ - Shared DB               │      │ - M0: local FS with           │
        │ - Row-Level Security      │      │   checksums                   │
        │ - org_id on every         │      │ - later: S3 with Object Lock  │
        │   tenant table            │      │   (WORM) + versioning         │
        └──────────────────────────┘      └──────────────────────────────┘
```

In M0 only the left core is relevant (auth, tenant context, org/user/mandates, RLS foundation). The PDF rendering pipeline and object storage are set up as an **infrastructure skeleton** (roadmap M0), but filled with domain content only from M2/M3. This document describes the voucher storage and snapshot concept (Section 3) completely in advance, because it is an invariant and shapes the data model — the actual voucher implementation is M2 (`engine-correctness` delivers the in-depth GoBD voucher storage design there, roadmap model assignment).

### 1.2 Architecture principles (from SDD §16, made concrete here)

- **API-first:** The backend API is cleanly separated from the web UI; it serves integrations (later bank import, DATEV export), not native clients.
- **Layers:** Auth → tenant context → domain service → repository → DB. Organization isolation is enforced in **two** of these layers (repository *and* DB RLS), see Section 2.
- **Settlement logic separated** from UI and PDF layout (only takes effect from M3, but the layer model sets it up from M0).
- **Immutable snapshots** for finalized settlements and approved vouchers (Section 3).
- **Cost categories as versioned system data** (relevant from M2; model preparation in Section 5.4).

---

## 2. Organization isolation (I1 — the most important M0 decision)

### 2.1 Options

| Option | Description | Isolation strength | Operational complexity | Suitability for settlement/reporting |
| --- | --- | --- | --- | --- |
| **A — Shared DB, app-layer scoping** | One DB, `organization_id` on every table, filtering exclusively in application code. | Weak: a forgotten `WHERE org_id = …` immediately leaks foreign data. | Low | Good |
| **B — Shared DB + PostgreSQL Row-Level Security (RLS)** | One DB, `organization_id` on every table, **DB-enforced** RLS policies in addition to app scoping. | Strong: the DB itself rejects cross-organization access even when the app filter is missing. Defense in depth. | Medium | Good (all mandates in one DB; cross-organization reporting for the management company itself possible) |
| **C — Schema-per-tenant** | One PostgreSQL schema per organization, identical tables per schema. | Strong (physical separation per schema). | High: migrations across N schemas, connection routing, schema provisioning per new customer. | Weaker for cross-tenant views; good for “one WEG = one hard box”. |
| **D — Database/cluster-per-tenant** | A separate DB per organization. | Very strong. | Very high; disproportionate for an MVP. | Poor for aggregation; expensive. |

### 2.2 Decision: **Option B — Shared DB + RLS + mandatory `organization_id` scoping** *(Recommendation, [DECISION])*

**Rationale in the context of precisely this domain:**

1. **Defense in depth instead of single-point failure.** Organization isolation here is not only data protection but touches the *segregation of funds* (trust/third-party funds) — a leak is a serious, potentially liability-relevant error. Option A depends on the discipline of every single query; one single forgotten filter statement (or a generic report, an ad-hoc script, a future bulk endpoint) leaks foreign data. RLS makes the DB the **last, non-bypassable line of defense**: Even if the app-layer filter is missing, the DB returns only rows of the active organization. The invariant thereby becomes a *property of the storage*, not a property of the code.
2. **Lower operational load than schema-per-tenant.** A professional property management company looks after many mandates; the product serves many tenants but is not extremely data-intensive per tenant. Schema-per-tenant (C) multiplies the migration complexity by the number of tenants and requires connection routing/provisioning — effort without a proportional isolation gain over RLS.
3. **Reporting/operations of the management company itself.** The management company as an organization will eventually want its own cross-mandate evaluations (deadline monitor across all WEGs, total open items). With option B this is a simple, RLS-controlled query in the org context; with C it would be N schema joins.
4. **Migration/testability.** One schema version, one migration path. Isolation tests run against a DB with two orgs (see scaffolding plan, Section 7).

**Deliberately accepted:** RLS requires care with (a) the DB role the app connects with (must **not** have `BYPASSRLS`, must **not** be table owner/superuser), (b) reliably setting the tenant context per transaction, and (c) migrations/background jobs that run as a privileged role and deliberately bypass RLS. These points are recorded below as binding rules.

The detailed rationale and the rejected alternatives are recorded in [adr/0001-organization-isolation-rls.md](adr/0001-organization-isolation-rls.md).

### 2.3 How I1 is technically *enforced* (three levels)

The invariant must not be bypassable by accident. It is enforced at three complementary levels:

**Level 1 — DB row-level security (the non-bypassable line).**

- Every tenant-scoped table receives `organization_id UUID NOT NULL` with an FK to `organization(id)`.
- RLS is **enabled and forced** on each of these tables: `ALTER TABLE … ENABLE ROW LEVEL SECURITY; ALTER TABLE … FORCE ROW LEVEL SECURITY;` (`FORCE`, so that RLS also applies to the table owner).
- A `USING` policy for reading and a `WITH CHECK` policy for writing bind each row to the active organization, which is set as a session variable (GUC):

  ```sql
  -- Concept (implementation in M0 by bulk-impl, only the form here):
  CREATE POLICY org_isolation ON <table>
    USING      (organization_id = current_setting('app.current_org_id')::uuid)
    WITH CHECK (organization_id = current_setting('app.current_org_id')::uuid);
  ```

  The `USING` clause prevents *reading* foreign rows; the `WITH CHECK` clause prevents *writing/moving* a row into a foreign organization (e.g., an INSERT/UPDATE that tries to set a foreign `organization_id`).
- The application connects with a DB role **without** `BYPASSRLS` and **without** table ownership. Migrations and maintenance jobs use a separate, privileged role.

**Level 2 — Tenant context middleware (sets the context reliably).**

- On every authenticated request, the middleware derives the `organization_id` **exclusively from the server-side session/the validated token** — never from a client-supplied parameter, header, or body.
- Per request/transaction, `app.current_org_id` is set via `SET LOCAL` (transaction-bound) or `set_config(..., is_local => true)` **before** the first tenant query runs. `SET LOCAL` is transaction-local and is automatically reset at commit/rollback — decisive with pooled connections, so that no context “leaks through” between requests.
- **Fail-closed:** If no tenant context is set, the RLS policy returns zero rows (the GUC is empty → the comparison fails). We additionally set the GUC by default to an impossible value, so that “context forgotten” leads to “nothing visible”, never to “everything visible”.

**Level 3 — Repository/query-layer guard (early, clear errors + performance).**

- There is **no** direct table access from domain services; all tenant data access goes through a narrow repository/data-access layer that works only via the org-bound session context.
- Additionally (not instead of RLS, but for performance and meaningful errors), the repository filters explicitly on `organization_id`. RLS is the safety net; the explicit filter ensures sensible index use and makes it visible in the code that this is a tenant query.
- A **lint/review gate** (CI): tenant-scoped tables are kept in a central list; a test checks that RLS is enabled and a policy exists for each of these tables (see Section 7, migration test). This way a newly added table cannot accidentally reach production without RLS.

### 2.4 Why three levels instead of RLS alone

RLS alone protects against data leaks, but when the context is missing it returns zero rows “silently” — which can mask bugs. The repository guard makes such cases visible early and loudly. The middleware guarantees that the context is set reliably in the first place. Together: **RLS prevents the leak, the other two levels prevent silent malfunction.**

### 2.5 Non-tenant tables (deliberate exceptions)

A few tables are **global/system-wide** and carry no `organization_id`:

- `organization` itself (the tenant root).
- Global system master data such as the **standard chart of accounts** (`cost_category`), provided it is identical for all orgs (SDD §7 — fixed chart in phase 1). This table is readable by everyone, writable only by migrations/the system role. *(Assumption A-1, see Section 8 — the chart of accounts may be copied per org if orgs are allowed to version it.)*
- Pure auth infrastructure that is assigned to an organization (`user`, `membership`) carries `organization_id` and is therefore tenant-scoped — see Section 4.

Each exception is explicitly noted in the central table list (Section 7), so that “no `organization_id`” is always a deliberate, reviewed decision and never an oversight.

### 2.6 Distinguishing organization isolation from segregation of funds

These are **two different isolation axes**, and the distinction matters in domain terms (SDD §4):

- **Organization isolation (I1):** between *organizations*. Management company A never sees data of management company B. → In M0 strictly enforced via RLS.
- **Segregation of funds:** *within* an organization, between mandates/WEGs. Funds and vouchers of the WEG “Hauptstr. 5” are not commingled with those of the WEG “Bahnhofstr. 12” (separation of trust/third-party funds).

Segregation of funds is **not** implemented in M0 as a second RLS axis (that would be premature and would only affect accounts/payments from M6). Instead it is **prepared in the data model**: `BankAccount`, `CostEntry`, `Payment`, `SettlementRun`, etc. carry a reference to `management_mandate` or `property`, so that the separation can later be enforced and evaluated cleanly. The roadmap anchors segregation of funds fully only from M6 — the model keeps the door open from M0.

> **[DECISION] / open item:** Should segregation of funds be enforced on the DB side in the medium term as well (second RLS axis or constraint level), or is app-layer enforcement via the mandate/property assignment sufficient? Recommendation: in M0 only model preparation; decide on DB hardening with M6, when accounts/payments are real. Recorded here so that it is not forgotten.

---

## 3. GoBD immutability (I2) and interlocking with the GDPR (DSGVO) erasure obligation

> Note on depth: M0 anchors the concept in the data model. The *in-depth* GoBD voucher storage specification is planned as a separate step in M2 (`engine-correctness`, roadmap model assignment). This section establishes the load-bearing structure so that M1/M2 can build on it.

### 3.1 What must be immutable (SDD §6.6, §14, §16)

1. **Approved vouchers** (`InvoiceDocument`: PDF and/or e-invoice XML) — no longer overwritable once approved.
2. **Finalized settlements** (`SettlementRun` and its `SettlementLine`, likewise finalized `EconomicPlan`, `AssetReport`) — as immutable snapshots, so that later master data changes do not alter old documents.
3. **Audit trail** (who/when/what) for vouchers, cost items, and approvals.

### 3.2 Building blocks of immutability

**(a) Append-only instead of overwriting.**

- Vouchers and snapshots are **never** changed in content via `UPDATE`. A correction creates a **new version** referencing the original (`supersedes_id` → previous version; the original is marked `superseded` but remains). This yields a gapless version chain.
- Status transitions (e.g., `draft_extracted → needs_review → confirmed → rejected`, SDD §9) are allowed as long as the *finalized* content remains untouched. From `confirmed`/locking onward, the content-bearing part is frozen.

**(b) Integrity via SHA-256.**

- When a voucher is locked, a **SHA-256 hash** is computed over the file content (PDF bytes or XML bytes) and stored in `invoice_document.content_sha256`.
- The physical content lives in object storage (M0: local FS with checksums; later S3 with Object Lock/WORM + versioning, SDD §16). The DB holds metadata, hash, and storage location/version.
- For **snapshots** (settlements), the serialized snapshot payload (canonical JSON representation of all computed values) is also hashed (`settlement_run.snapshot_sha256`), so that subsequent manipulation is detectable.

**(c) Versioning with reference to the original.**

- Version chain via `version` (running) + `supersedes_id` (FK to the predecessor) + status `active`/`superseded`. Per logical voucher, exactly one `active` version is defined at any point in time.

**(d) Audit trail (append-only event log).**

- A central, append-only table `audit_event` logs security- and settlement-relevant operations: `actor_user_id`, `organization_id`, `entity_type`, `entity_id`, `action` (e.g., `document.released`, `settlement.finalized`, `user.role_changed`), `occurred_at`, `payload_diff` (JSON), optionally `prev_event_hash` for chained integrity protection (hash chain — prevents unnoticed deletion of individual audit rows).
- `audit_event` is tenant-scoped (carries `organization_id`, RLS active) and is written **via INSERT only**; no UPDATE/DELETE in normal operation (secured by missing grants and a CI test).

**(e) Enforcement against accidental mutation.**

- Immutability is secured not just by convention but close to the DB: For finalized/locked rows, a **BEFORE UPDATE/DELETE trigger** (or revoked UPDATE/DELETE grants on the relevant tables for the app role) prevents any mutation of the content-bearing columns. The concrete mechanism (trigger vs. grants vs. a separate “frozen” table) will be finally chosen in the M2 GoBD design; M0 provides the columns and status fields for it.

### 3.3 Retention (8 years) vs. GDPR erasure obligation — the area of tension

SDD §6.6/§15 and §17 demand both: **at least 8 years of audit-proof voucher retention** (BEG IV from 2025) *and* GDPR-compliant erasure/access capability, “without violating audit-proof retention”. This can only be resolved if **voucher substance** and **personal master data** are cleanly separated.

**Concept (M0 lays the structure, M9 hardens it):**

1. **Separation of voucher and person.** A voucher (`InvoiceDocument`, accounting voucher, finalized settlement) must be retained for 8 years as tax-relevant evidence — *unchanged in content*. Personal master data (`Person`, contact data, bank data) lives in separate entities and is referenced, not copied into the voucher, as far as is defensible in domain terms. *(Limit: An invoice/settlement naturally contains the addressee's name/address — this part is a component of the retention-bound voucher and is **not** deleted, but falls under statutory retention as the legal basis against the erasure claim.)*
2. **Crypto-erasure / anonymization instead of hard delete.** Upon an erasure request, personal data that is *not* part of a retention-bound voucher is deleted or anonymized; retention-bound vouchers remain unchanged, but are **blocked** against further processing (restriction of processing instead of erasure — the GDPR permits retention under a statutory retention obligation and then provides for restriction of processing).
3. **Erasure/blocking status in the model.** `Person` (and related master data) receive the fields `deletion_requested_at`, `processing_restricted` (bool), `anonymized_at` from M0. This anchors the concept in the model from day 1; the actual erasure/anonymization mechanics come in M9.
4. **Retention anchor.** Vouchers/snapshots receive a `retention_until` field (computed from the relevant date + 8 years — *which date exactly is relevant is an open item, see below*), so that the erasure process can automatically check what may be deleted and what must remain blocked.

> **[DECISION] / SDD §18 (level of detail of the erasure/anonymization concept):** This area of tension is explicitly marked as open in SDD §18. **It does not block M0/M1** (the fields are provided, the mechanics come in M9), but must be clarified in domain terms before M9, ideally with a tax advisor/data protection officer:
> - Which date starts the 8-year period per voucher type (invoice date, end of the fiscal year, locking date)?
> - Which personal fields count as “voucher component” (retention-bound) vs. “pure master data” (erasable)?
> - Anonymization vs. blocking: technical design (e.g., pseudonymization of the master data while preserving the voucher reference).
>
> **No self-certification:** This design makes retention transparent and auditable; whether it is court-/audit-proof in an individual case must be signed off in domain terms (tax advisor/specialist attorney).

---

## 4. Auth & roles

### 4.1 Entities

- **`Organization`** — organization/isolation root (SDD §4). Carries no `organization_id` (it *is* the tenant).
- **`User`** — a login account. **Assumption A-2:** In phase 1, a user belongs to **exactly one** organization (`user.organization_id`). This considerably simplifies tenant context and RLS. *(If a vendor account is later to serve several management companies, an n:m `Membership` would be needed — deliberately out of scope for M0, see open item below.)*
- **`Membership` / role assignment** — links `User` with `Organization` and a **role**. With A-2 (one user = one org), the role could also hang directly on the user; I nevertheless model it as a separate assignment (`membership`) so as not to block the later n:m extension. *(Design proposal, not strictly required.)*
- **`Role`** — fixed enumeration, **not** freely configurable (SDD §4): `OWNER_ADMIN` (business owner/admin), `CLERK` (case worker), `READ_ONLY` (read-only). As an enum/fixed master data, not as an editable table with individual permissions (SDD §3 non-goal: no fine-grained permission system).
- **`ManagementMandate`** — business engagement *within* an organization (SDD §4), with type (`WEG`, `MIET`, `SEV` — Sondereigentumsverwaltung, individual unit management) and term (`start_date`, `end_date`). Carries `organization_id` (tenant-scoped). Links the management company to a managed context and is the anchor for the later segregation of funds.

### 4.2 Role matrix (from SDD §4, made concrete for M0)

| Action (M0-relevant) | OWNER_ADMIN | CLERK | READ_ONLY |
| --- | --- | --- | --- |
| Organization settings, user/role management | ✔ | ✗ | ✗ |
| Create/change mandates | ✔ | (read) | (read) |
| Operational capture/settlement/voucher approval (from M2+) | ✔ | ✔ | ✗ |
| Read all org data | ✔ | ✔ | ✔ |

(`✗` = no access; READ_ONLY is strictly read-only, e.g., for an advisory board/auditor/deputy.)

Role enforcement takes place in the authorization layer (policy check per endpoint). It is **orthogonal to organization isolation**: RLS ensures *which* organization is visible; RBAC ensures *what* a user may do within their organization. Both take effect independently.

### 4.3 Login/session approach *(Recommendation, [DECISION])*

- **Session model:** Server-side sessions with HTTP-only, Secure, SameSite cookies (no token accessible to JS → XSS hardening). Alternatively short-lived JWT access tokens + refresh; for an administrative back office with a browser UI, the **server session is the more robust default** (simple logout/invalidation, no token-leak surface).
- **Password hashing:** Argon2id (or bcrypt as a fallback), never plaintext, never reversible.
- **Tenant binding:** The user's `organization_id` lives in the server-side session and is the *only* source for the tenant context (Section 2.3, Level 2). No org switch via request parameter.
- **MFA:** not mandatory for M0; note as a hardening option for M9 (back office with personal + financially relevant data).
- **Audit:** login, logout, failed logins, role changes → `audit_event`.

**Implemented T57 path:** Signed cookies carry user/organization and,
for a positive generation, `sv`. The runtime checks active membership and the current
DB generation on every request and again at live transaction boundaries under the
ACL lock. Additive revision 0138 increments on password change or
deactivation; reactivation does not reset it. Old cookies without `sv` are valid only
for generation zero. Old auth definer functions and RLS remain unchanged;
new, narrowly granted wrappers add the generation. Admin creation/change/reset
take the exclusive ACL lock before row locks, check freshly, prevent
self-revocation and commit together with non-sensitive audit data. This is
API admin authorization, not a claimed replacement for all existing SQL RLS
responsibilities. Read-only hides domain write actions, not own
password maintenance. The downgrade refuses to lose positive generations.
The original authenticated generation is carried through all short knowledge phases
and the prior-year-plan admission, never freshly adopted there.
Revision 0139 grants the isolated review runtime exclusively execution of the
versioned session check, not of the password lookup. Unknown legacy roles
remain non-authorizing (`null` in the administration DTO, login 401).

> **[DECISION]:** Server session (recommended) vs. JWT. And: self-hosted auth vs. external identity provider (e.g., Keycloak/Authentik). For the MVP I recommend **our own lean server-session auth** (full control, no external dependency), but encapsulating the interface so that a later IdP switch remains possible.

> **Open item (out of scope M0):** Users in several organizations (n:m). Deliberately excluded in phase 1 (A-2). If groups of management companies or shared vendor accounts are needed, reassess before the milestone (M) decision.

---

## 5. Data model for M0 + M1

This section refines the SDD entities (§11/§13) with (a) tenant scoping, (b) temporal fields, (c) immutability fields. **M0 implements only the core (Organization, User, Membership, ManagementMandate) plus the migration/RLS foundation.** M1 adds master data. The remaining entities (costs, vouchers, settlement, …) are sketched here only as a *preview*, insofar as they influence M0/M1 structural decisions — they are detailed in their milestones.

### 5.1 Cross-cutting conventions (binding for **all** tables)

| Convention | Rule | Rationale |
| --- | --- | --- |
| **Primary key** | `id UUID` (default `gen_random_uuid()`), not sequential integers for domain entities. | No guessability/enumeration across org boundaries; distributable. |
| **Tenant column** | `organization_id UUID NOT NULL` (FK → `organization.id`) on **every** tenant-scoped table. Exceptions only per Section 2.5. | I1 (organization isolation). |
| **Monetary amounts (I4)** | **`NUMERIC(14,2)`** for EUR amounts — **never** `float`/`double`/`real`. Sign allowed (back payment/credit). | Exact decimal arithmetic; floating point is ruled out for money (rounding errors, not audit-proof). |
| **Shares/keys** | MEA (Miteigentumsanteil, co-ownership share) and distribution shares as **`NUMERIC`** with sufficient scale (e.g., `NUMERIC(12,6)` for shares/numerator-denominator) — stored exactly, not rounded; rounding only at the final presentation (rule comes with M3). | Settlement reliability; rounding is a deliberate, documented operation, not a side effect of floating point. |
| **Timestamps** | `created_at`, `updated_at` as `TIMESTAMPTZ` (UTC). | Traceability, time zone safety. |
| **Temporal validity** | History tables: `valid_from DATE NOT NULL`, `valid_to DATE NULL` (NULL = open). | I3 (chronological integrity); pro rata periods. |
| **Soft/append semantics** | No hard deletes on history- or voucher-bearing tables; status fields instead of deletion. | I2/I3 and GDPR blocking concept (Section 3.3). |
| **Money precision note** | Consistently `Decimal`/decimal type on the application side; no implicit float conversions (see stack, Section 6). | I4 throughout from storage to computation. |

### 5.2 ER overview (M0 core, bold = created in M0)

```
                         ┌────────────────────────┐
                         │ organization            │  (tenant root, NO org_id)
                         │ - id PK (UUID)           │
                         │ - name                   │
                         │ - created_at             │
                         └───────────┬──────────────┘
            ┌────────────────────────┼───────────────────────────┐
            ▼                        ▼                            ▼
 ┌───────────────────┐   ┌───────────────────────┐   ┌─────────────────────────┐
 │ user               │   │ management_mandate     │   │ (M1: property, person …) │
 │ - id PK            │   │ - id PK                │   │   all with org_id, RLS   │
 │ - organization_id  │FK │ - organization_id   FK │   └─────────────────────────┘
 │ - email (uniq/org) │   │ - mandate_type         │
 │ - password_hash    │   │   (WEG|MIET|SEV)       │
 │ - status           │   │ - start_date           │
 │ - created_at       │   │ - end_date  (NULL=open)│
 └─────────┬──────────┘   │ - created_at           │
           │              └───────────┬────────────┘
           ▼                          │ (M1: mandate ↔ property/unit relation)
 ┌───────────────────┐                ▼
 │ membership         │     ┌──────────────────────────┐
 │ - id PK            │     │ audit_event  (append-only) │
 │ - organization_id  │FK   │ - id PK                    │
 │ - user_id       FK │     │ - organization_id       FK │
 │ - role (ENUM:      │     │ - actor_user_id         FK │
 │   OWNER_ADMIN|     │     │ - entity_type / entity_id  │
 │   CLERK|READ_ONLY) │     │ - action                   │
 │ - created_at       │     │ - occurred_at              │
 └───────────────────┘     │ - payload_diff (JSONB)     │
                           │ - prev_event_hash (opt.)   │
                           └──────────────────────────┘
```

**Constraints (M0 core):**
- `user`: `UNIQUE (organization_id, email)` — email unique per organization (not globally, since under A-2 one user = one org; different orgs could use the same property manager email domain). RLS active.
- `membership`: `UNIQUE (organization_id, user_id)` under A-2 (one role per user per org); FK to `user` and `organization`. RLS active.
- `management_mandate`: `CHECK (end_date IS NULL OR end_date >= start_date)`; `mandate_type` as an enum. RLS active.
- `audit_event`: INSERT grant only for the app role; RLS active; `occurred_at` set by the server.
- **RLS on `user`, `membership`, `management_mandate`, `audit_event`** enabled + `FORCE`.

### 5.3 ER overview (M1 master data — design now, implementation in M1)

```
 organization 1───* property 1───* unit
                       │             │
                       │             ├──* ownership_history   (temporal: valid_from/valid_to)
                       │             │       └─ owner_profile  (FK → person)
                       │             └──* lease_agreement      (temporal: valid_from/valid_to)
                       │                     └─ tenant_profile (FK → person)
                       │
                       ├──* bank_account        (segregation of funds; FK → mandate/property)
                       └──* meter 1───* meter_reading

 person (base master data, GDPR fields)
   ├─ owner_profile   (1:1 role as owner)
   ├─ tenant_profile  (1:1 role as tenant)
   └─ vendor          (vendor/supplier; own entity or person specialization)
```

T51 adds `/api/properties/import/template.csv`, `/preview` and `/apply`.
`MasterImportService` validates bounded UTF-8 semicolon CSV and uses the same
canonical creation functions as the individual endpoints (`master_creation_service`).
The preview checks approvals without domain/grant publication. Apply first obtains the
exclusive publication ACL, creates all properties/units/contacts in a
function-bound transaction and keeps only result IDs in the immutable,
actor/org-bound `master_import_batch` (0134, FORCE RLS). Payload digest and
request UUID prevent duplicate retries; replay re-checks all original
sources. No implicit contact profiles, lease agreements or self-approvals.

**`property`** (property, SDD §6.1): `organization_id` FK, `name`, address, **`management_type`** (`MIET`|`WEG`|`SEV`|`MISCH`), settlement period convention. RLS active.

**`unit`** (unit, SDD §6.1): `organization_id`, `property_id` FK, `number`, `location`, **`area NUMERIC(10,2)`** (living/usable area), **`heated_area NUMERIC(10,2)`** (heated area), `usage_type` (Wohnung|Gewerbe|Stellplatz|Sonstige, i.e., residential|commercial|parking space|other), **`mea NUMERIC(12,6)`** (co-ownership share — exact, not float). Optional distribution peculiarities. RLS active.

**`person`** (SDD §6.2): `organization_id`, contact data, optional bank data. **GDPR fields (Section 3.3):** `deletion_requested_at`, `processing_restricted`, `anonymized_at`. RLS active. Owner/tenant/vendor as separate logics (SDD §6.2) — either specializations of `person` (`owner_profile`, `tenant_profile`, `vendor`) or separate tables with a person reference; **[DECISION] for M1**, proposed here as 1:1 profiles, since one person can have several roles (e.g., owner *and* also a tenant themselves).

**`ownership_history`** (SDD §6.2, §14 — *core of chronological integrity*): `organization_id`, `unit_id` FK, `owner_profile_id` FK, **`valid_from DATE NOT NULL`**, **`valid_to DATE NULL`**, **`mea_share NUMERIC(12,6)`** (this owner's share of the unit, in case of shared ownership). RLS active.
- **Non-overlap constraint:** Per `unit_id`, the `[valid_from, valid_to)` intervals of an ownership strand must **not overlap**. Recommended via a PostgreSQL **EXCLUDE constraint** with `daterange` and the `&&` operator (btree_gist), which includes the org and unit reference. This is the DB-side guarantee for the M1 exit criterion (“gapless and overlap-free”). *(The exact form — whether a single owner strand per unit or several fractional co-owners with a sum condition on the shares — is an M1 detail question; the EXCLUDE mechanism is recorded here as the means.)*

**`lease_agreement`** (SDD §6.2, §14): `organization_id`, `unit_id` FK, `tenant_profile_id` FK, **`valid_from`/`valid_to`** (lease start/end), **`base_rent NUMERIC(14,2)`** (base rent), **`advance_payment NUMERIC(14,2)`** (advance payment toward operating costs (Nebenkosten), “NK-Vorauszahlung”), optionally **`deposit NUMERIC(14,2)`** (security deposit), notes. RLS active. Same **EXCLUDE constraint** logic against overlapping rental periods per unit (with a view to vacancy: gaps are *allowed* — vacancy —, overlaps are not).

T43 adds `POST /api/leases/{id}/rent-changes/staffel` as an atomic generator
via `LeaseRentChangeService`, without additional lease fields. Live ACL,
financial and lease row locks protect the base rate from concurrent changes;
all dates/amounts are validated before the T23 inserts. Euro amounts are
rounded to cents with Decimal per step; calendar dates remain anchored to the first
increase day. Existing future history is not overwritten.
Booked payments, issued documents and final snapshots are preserved.

T44 uses the pure Decimal engine `index_rent.py` for VPI (consumer price index) ratio calculation,
cent rounding, the one-year period and the start of the month after next following the stated receipt.
Only the last base rent change determines the one-year period, not a pure operating-cost (NK) change.
Preview and adoption check the same live/source rights and lock before reading
the base rate. Adoption recalculates and compares against the preview; stale
values yield 409. The T23 write path and an additional index audit preserve
history and calculation bases, without a new schema or any claim of dispatch.

T45 adds `heizkosten.py` with immutable heating/hot-water inputs/outputs
in `engine/types.py`. Separate cost pools and explicit base-cost bases are distributed via
central largest-remainder rounding; base, consumption, reduction and
payable amounts remain separately traceable. The pure TENANT/WEG_OWNER context
prevents blanket application of the §12 tenant reduction right to WEG owners.
No ORM/schema change. T46 connects `heating_settlement.py` to the existing
BK orchestrator; `HeatingInputService` resolves, per activated confirmed cost source,
visible units/usage periods and exact meter boundaries. Physical
base quantity × days and actual consumption quantity remain separate bases; the
T45 rounding remains central. Vacancy rows keep the gross share without a §12 deduction.
`ComputeExtras.heating_inputs` stores only user intent in `snapshot_payload.extra_inputs`;
Compute/Finalize re-check freshly under the existing live/source/financial/row locks.
Frozen heating partial amounts and concrete measurement sources are added before the hash;
the PDF renderer reads them without recalculation from the database. Existing third-party statements,
general consumption metrics, RLS and immutable originals are preserved.

T47 adds `/api/mail/{dunning|settlement|invitation}/{id}` via
`DocumentMailService` and the separate SMTP transport `mail_service.py`.
The existing PDF renderers and financial/source/contact checks are reused;
with financial binding, `send` is required in addition to `export`. Live ACL/financial/source
and contact row locks protect the approval up to the SMTP call. Per request,
an organization-bound key serializes concurrent retries.
A dedicated tenant-bound transaction writes `mail_prepared` before the network call
as an append-only `AuditEvent` under the real domain-object ID; `mail_completed` follows
with `accepted`, `failed` or `unknown`. An open attempt after a process/commit failure
remains permanently recognizable; never an automatic retry or claim of delivery.
The exact PDF attachment is stored content-addressed in the existing document storage;
audit reference, hash, contact ID and message ID are preserved. The protected
history PDF endpoint additionally checks the **original** contact export approval,
even if the invitation recipient list later changes. No schema change,
no update of issued letters or final snapshots; TLS remains verified,
SMTP credentials exclusively from the environment and `plain` only for loopback without login.

T48 adds `/api/letters` with editable plain-text templates and immutable
`letter_document` originals (migration 0132). Only fixed placeholders are replaced;
HTML escaping happens afterwards, with no executable template engine or foreign resources.
The complete as-of-date set is checked under narrowly bounded SQL admission before the
RLS-filtered enumeration; missing source/contact export rights do
not lead to a partial circular letter. One PDF per person; end dates remain exclusive.
Live ACL/financial/property/template/contact locks and organization-bound
request IDs protect issuance and concurrent retries. Replay also checks
all original recipients, not only rows that are still visible. Text/subject,
original sources and content-addressed storage reference are preserved;
download checks current original-source/contact rights and the hash, without recalculation.
Changing a template does not change any letters. Populated tables prevent lossy
downgrade; dispatch is not part of circular letter issuance.

T49 adds the read-only `/api/reports/datev/{sources|preview}` path and
`POST /api/reports/datev.csv`. `ReportService.property_period` shares the existing
complete property/financial-scope admission with annual reports; a dedicated repository
reads current booked payments and confirmed costs. Live ACL/financial locks,
complete source/contact export rights and a repeated export check after rendering
prevent partial datasets. The preview fingerprint binds concrete source rows and
explicit SKR03/SKR04/account configuration to the download. No mutation or
migration; global categories remain free of account mappings from other organizations.
The pure renderer uses DATEV header 700/Buchungsstapel (booking batch) 13 with 125 fields,
Decimal, UTF-8 BOM and CRLF. Only explicitly verified gross accounts without
tax automation; no invention of VAT/BU/voucher links. The result is a current
inventory export, not a differential substitute for a booking journal and not a confirmation
of an import in the target system. Corrections/re-imports must be coordinated there on the domain side.

T50 adds `/api/payments/sepa`: explicit creditor configuration per current
account and a separate `sepa_mandate` consent (not `management_mandate`), revision
0133 with FORCE RLS, canonical source checks and immutable identity.
Mandates contain only a fingerprint of the current name/IBAN instead of a
second raw-data copy; bank/contact changes and data protection blocks prevent issuance.
ACL/financial/source row locks protect the configuration and the monthly projection.
`tenant_expected_charges` supplies the existing Decimal expected amount (Soll) from rent history and
partial months. Complete financial-scope/source/contact export admission prevents
silent omission of inaccessible or unmandated tenants. The preview fingerprint
and repeated rights check bind the download; the pure XML renderer validates
locally against the unchanged EPC-CORE-pain.008.001.08 XSD (as a package resource).
No transmission service, no payment creation and no carrying forward of a
collection history from a download. Populated new tables prevent a downgrade;
the transactional round-trip test compares all previous data/security objects
and the unchanged bytes of an already issued letter.

**`bank_account`** (SDD §6.3 — segregation of funds): `organization_id`, reference to `management_mandate`/`property`, `account_type` (laufend|Rücklage|Kaution, i.e., current|maintenance reserve|security deposit), IBAN/bank master data. RLS active. *(Implementation only from M6; prepare the model anchor in M1 so that the mandate/property reference is consistent.)*

**`meter` / `meter_reading`** (SDD §6.2.1): optional property/unit meters with readings (`reading_date`, `value NUMERIC(14,4)`, `period`). RLS and explicit org filters active. Since T29, `/api/meters` offers GET/POST, `/{id}` GET/PATCH for descriptive fields and `/{id}/readings` GET/POST for readings that are preserved unchanged. Repository and API check the visible parent object, unit coherence and existing master read/prepare rights; no later re-parenting. Write operations hold live ACL/financial/row locks in this order and commit before the success response. Measured values are validated without float or rounding and output as four-decimal strings. Since T30, property and unit detail use the same `MeterPanel` with fixed context and role flag; string/BigInt arithmetic shows the difference between the last two distinct days, not yet a T31 settlement prefill. Context switches decouple late responses, write actions are locked synchronously against repeated triggering; errors retain the inputs.

### 5.4 Preview of I2-bearing entities (detail in M2/M3/M4)

Only the fields that anchor the immutability/snapshot concept in the model are listed here (full definition in the respective milestones):

- **`cost_category`** (SDD §7): fixed standard chart of accounts; **versioned system master data** (`version`, validity), global or copied per org (assumption A-1). Defaults for apportionability + distribution key.
- **`cost_entry`** (SDD §6.4): `organization_id`, `property_id`, optionally `management_mandate_id`, `cost_category_id`, supplier (`vendor`), invoice date, **service period `service_period_from`/`_to`** (SDD §14 — not just the invoice date!), **`gross_amount NUMERIC(14,2)`**, optional VAT amount/rate (`NUMERIC`), voucher number, separate distribution keys for owner/tenant settlement, apportionability, status of voucher origin/approval (`draft_extracted|needs_review|confirmed|rejected`).
- **`invoice_document`** (SDD §6.6, §13): `organization_id`, reference to `cost_entry`, `storage_uri`, **`content_sha256`**, **`version`**, **`supersedes_id`** (FK self), `status` (`active|superseded`), `released_at`, `released_by`, **`retention_until`** (retention anchor, Section 3.3).
- **`settlement_run`** (SDD §6.9, §13, §16): `organization_id`, `property_id`/`mandate`, type (operating cost statement (BK)|WEG annual settlement|economic plan (Wirtschaftsplan)|asset report (Vermögensbericht)), settlement period, `status` (`draft|finalized`), **`snapshot_payload JSONB`** (frozen calculation basis), **`snapshot_sha256`**, `finalized_at`, `finalized_by`. Immutable from `finalized` (trigger/grants, Section 3.2e).
- **`settlement_line`**: per addressee/category; belongs to the snapshot of the `settlement_run`; all amounts `NUMERIC(14,2)`.
- Audit trail (`audit_event`) — already in M0 (Section 5.2), used intensively from M2.

> This preview is **not** part of the M0 implementation. It is here so that the M0 structural decisions (UUID PKs, `organization_id`, `NUMERIC`, status/version fields, RLS table list) are consistent with the later model family and `bulk-impl` does not have to rebuild anything in M2.

---

**`task`** (Phase 2, T41): operational, deletable tasks with title, note,
optional due date, done flag and an assignee from the same organization.
Property/unit/lease references are optional and are derived coherently.
FORCE RLS checks the live role as well as old and new parent rights; a dedicated
invoker adapter and fixed function search paths prevent permission shortcuts and
temporary parent-table shadowing. Write paths hold ACL before row locks
and commit before the success response. Global tasks are not a financial-scope case;
existing tasks remain editable if the assignee is later deactivated.

The task UI adopts property/unit contexts without automatic
saving; the dashboard reads open tasks with a local, inclusive
seven-day limit. This operational data is not made dependent on the selection of a
financial scope. The separate read path `/api/fristen/context`
checks, under live/ACL and existing source rules, the admissibility of the
organization-wide deadline monitor, without loading financial report rows.
Only the specific missing-selection conflict is returned as a required scope
selection; other errors remain errors. Only after a deliberate selection does
the dashboard read the financial deadlines in the chosen context.

## 6. Stack recommendation *(Recommendation — the human must confirm, commit nothing yet)*

The SDD (§16) leaves the backend open: **Python/FastAPI** vs. **Node.js/NestJS**. Evaluation along the requirements of precisely this domain:

| Criterion (domain-specific) | Python / FastAPI | Node.js / NestJS | Weight |
| --- | --- | --- | --- |
| **Exact decimal arithmetic** (I4, core of the settlement) | `decimal.Decimal` is a first-class standard data type; ORM mapping `NUMERIC ↔ Decimal` is lossless and idiomatic. Very good. | JS `number` is an IEEE-754 float → unsuitable for money; one **must** fall back on a library (decimal.js / big.js) and enforce discipline that *no* `number` slips through for money anywhere. Feasible, but more error-prone. | **high** |
| **Settlement engine** (complex, test-driven business logic, M3/M4) | Clear, well testable (pytest); rich numeric/date standard library. | Well testable (Jest); also works. | high |
| **XRechnung/ZUGFeRD parsing ecosystem** (M5) | Mature ecosystem for XML/EN-16931, PDF/A-3 extraction (embedded XML), lxml-based schema/Schematron handling. | Ecosystem exists, but tends to have less out-of-the-box depth for EN-16931/ZUGFeRD. | medium |
| **PDF generation** (HTML→PDF, SDD §16) | Integration with Playwright (HTML→PDF) is language-independent; server-side PDF libraries are available. | Playwright/Puppeteer are natively at home here (JS). Slight advantage. | medium |
| **Migration tooling** | Alembic (with SQLAlchemy) — very mature, finely controllable; **RLS policies/triggers can be versioned cleanly as explicit migration steps**. | Migrations (TypeORM/Prisma) good; Prisma may require somewhat more manual work/escape hatches for RLS/raw policy DDL and session GUCs. | **high** (RLS!) |
| **Testability** (correctness is the product) | pytest + Hypothesis (property-based testing for distribution/rounding logic) — strong for precisely this domain. | Jest + fast-check possible; equally usable. | high |
| **OCR/AI integration** (M2 PDF extraction) | Python is the natural language for OCR/ML integration and local models. | Can be integrated via services. | low–medium |
| **Team/consistency with frontend** | Two languages (TS frontend, Python backend) — context switching. | One language (TS everywhere) — shared types/DTOs possible. **Advantage Node.** | medium |

**Recommendation: Python / FastAPI** for the backend, with a React/TypeScript frontend (the frontend is fixed by SDD §16 anyway).

**Short rationale:** The value-determining and riskiest part of this product is the **correct, audit-proof settlement with exact decimal arithmetic** (M3 is the largest, riskiest chunk according to the roadmap). Python offers the least “friction” here: `Decimal` is first class (no permanent float-avoidance regime necessary), Alembic versions **RLS policies and triggers as clean, reviewable migration steps** (central for enforcing I1/I2), and the XRechnung/EN-16931 ecosystem (M5) is more mature. The main disadvantage — two languages in the stack instead of one — weighs less for a correctness-critical financial/legal product than consistent money/decimal safety and the RLS/migration handling.

> **[DECISION]:** Backend Python/FastAPI (recommended) vs. Node/NestJS. Secondary, if FastAPI is chosen: ORM/migrations combination **SQLAlchemy + Alembic** (recommended because of fine RLS/DDL control) vs. a higher-level alternative. **Please confirm before `bulk-impl` scaffolds — nothing is committed yet.**

> **Important (independent of the backend):** Whatever is chosen, these points are non-negotiable: (1) money as a decimal type, never float; (2) RLS + `FORCE` + app role without `BYPASSRLS`; (3) transaction-local setting of the tenant context (`SET LOCAL`/`set_config(... is_local => true)`); (4) migrations with explicitly versioned RLS policies/triggers.

---

## 7. Scaffolding plan for `bulk-impl` (M0 implementation)

Goal: meet the M0 exit — “Two organizations can be created and are guaranteed not to see any of each other's data; roles take effect.” Actionable checklist. **Prerequisite: stack confirmation by the human (Section 6). No commit before then.**

> This checklist is written for `bulk-impl` (Opus 4.6, bulk engineering). Architecture/data model decisions have already been made in this document; `bulk-impl` implements them and escalates only if an open specification question (SDD §18) or an organization isolation/GoBD design question arises (per the `bulk-impl.md` escalation rule).

### 7.1 Project structure (proposal, FastAPI variant)

```
hausverwaltung/
├── docs/                       # SDD, roadmap, this architecture.md (leave untouched)
├── backend/
│   ├── app/
│   │   ├── main.py             # app bootstrap, API-first
│   │   ├── core/
│   │   │   ├── config.py       # settings (DB URL, session secret, EU region)
│   │   │   ├── db.py           # engine/session; sets app.current_org_id via SET LOCAL
│   │   │   ├── tenant.py       # tenant context middleware (org from session)
│   │   │   ├── security.py     # password hashing (Argon2id), session handling
│   │   │   └── rbac.py         # role enum + authorization dependency
│   │   ├── models/             # ORM models (Organization, User, Membership, Mandate, AuditEvent)
│   │   ├── repositories/       # org-bound data access (no direct table access otherwise)
│   │   ├── api/                # routers (auth, organizations, users, mandates) — API-first
│   │   └── services/           # domain services (M0: org/user/mandate management)
│   ├── migrations/             # Alembic; RLS policies + triggers as explicit steps
│   │   └── versions/
│   ├── tests/
│   │   ├── conftest.py         # test DB, two test orgs, org-bound clients
│   │   ├── test_tenant_isolation.py   # THE isolation test (see 7.4)
│   │   ├── test_rls_coverage.py       # RLS on every tenant table (see 7.3)
│   │   └── test_rbac.py               # roles take effect
│   └── pyproject.toml
├── frontend/
│   ├── src/
│   │   ├── app/                # routing, base layout (desktop + mobile)
│   │   ├── auth/               # login flow, session
│   │   └── shell/              # navigation/layout shell (responsive)
│   └── package.json
├── infra/
│   ├── docker-compose.yml      # PostgreSQL (+ optionally Playwright service as PDF pipeline skeleton)
│   └── db/roles.sql            # app role WITHOUT BYPASSRLS; separate migration role
└── README.md
```

### 7.2 Migration and DB setup (order)

1. **Create DB roles** (`infra/db/roles.sql`): an **app role** (`app_rw`) without `BYPASSRLS`, without table ownership, without superuser; a separate **migration/owner role** (`app_migrator`) for DDL. The app connects exclusively as `app_rw`. *(Enforces: RLS also applies to the app; migrations can deliberately bypass RLS.)*
2. **First migration — core tables:** `organization` (without `organization_id`), `user`, `membership`, `management_mandate`, `audit_event` with the columns/constraints from Section 5.2. UUID PKs (`gen_random_uuid()`, possibly `pgcrypto`/`pg_uuidv7`). Money fields exist in M0 only as preparation; if there are none in M0 → no float risk, but document the convention.
3. **Second migration — RLS foundation (separate, explicit step):** for each tenant table (`user`, `membership`, `management_mandate`, `audit_event`):
   - `ALTER TABLE … ENABLE ROW LEVEL SECURITY;`
   - `ALTER TABLE … FORCE ROW LEVEL SECURITY;`
   - `CREATE POLICY org_isolation … USING (organization_id = current_setting('app.current_org_id')::uuid) WITH CHECK (…);`
   - Choose the default GUC so that “not set” = “nothing visible” (fail-closed).
   - `audit_event`: additionally **revoke** UPDATE/DELETE grants for `app_rw` (append-only).
4. **Tenant context wiring:** `db.py`/`tenant.py` set `SET LOCAL app.current_org_id = '<uuid>'` (or `select set_config('app.current_org_id', :org, true)`) from the session per request transaction. Mind the connection pool: transaction-local, no leak between requests.
5. **Accompanying indexes:** Indexes on `organization_id` (and composite with frequent filter columns) so that RLS filters perform well.

### 7.3 Mandatory RLS coverage test (`test_rls_coverage.py`)

An automated test that **prevents accidentally unprotected tables** (level-3 gate from Section 2.3):

- Maintain a central list `TENANT_TABLES` (tenant-scoped) and `GLOBAL_TABLES` (deliberate exceptions per Section 2.5).
- The test queries `pg_class.relrowsecurity`/`relforcerowsecurity` and `pg_policies` and asserts: **every** table in `TENANT_TABLES` has RLS enabled **and** `FORCE` **and** at least one policy with `USING`+`WITH CHECK`. Every table in the DB schema is either in `TENANT_TABLES` or in `GLOBAL_TABLES` — an unknown table makes the test **fail** (forces deliberate classification).

### 7.4 The concrete isolation test (`test_tenant_isolation.py`) — core of the M0 exit

Setup (fixtures): test DB; **two organizations** `org_a`, `org_b`; one user with a role each; one `management_mandate` each; one sample row each in at least one tenant table. Two API clients, each logged in as a user of the respective org (real login flow, real tenant context).

Minimum assertions:

1. **Read isolation (API):** Client A lists mandates/users → sees **only** `org_a` data, never `org_b`. And vice versa.
2. **Direct access to a foreign ID (API):** Client A fetches an `org_b` resource directly by known UUID → **404/Not Found** (not 403, so as not to reveal existence), no data leak.
3. **Write isolation / WITH CHECK:** Attempt to create a row with a foreign `organization_id` or to “move” an own row into `org_b` → **rejected** by the DB (WITH CHECK).
4. **RLS without app filter (DB level, the decisive test):** Issue an **unfiltered** `SELECT * FROM management_mandate` directly via an `app_rw` DB session with `app.current_org_id = org_a` set → returns **exclusively** `org_a` rows. Proves that the isolation holds even when the application filter is missing (defense in depth, option B).
5. **Fail-closed without context:** Session **without** `app.current_org_id` set → `SELECT` returns **zero rows** (not “everything”). Proves the fail-closed property.
6. **RBAC takes effect (`test_rbac.py`):** `READ_ONLY` may read but not create/change (→ 403); `CLERK` may operate but not manage users/roles (→ 403); `OWNER_ADMIN` may do everything in their org. Within the own org, combined with the isolation from 1–5.

> If tests 1–6 pass, the M0 exit criterion is objectively demonstrated: two orgs, guaranteed data separation (at API **and** DB level), roles take effect.

### 7.5 App shell & infrastructure (in parallel, roadmap M0)

- **Responsive web app shell** (React/TS): navigation/layout skeleton (desktop + mobile), login page, org context display, placeholder navigation for later modules. No native app.
- **Auth flow** end-to-end: login → session → tenant context in the backend.
- **PDF rendering pipeline as a skeleton:** Set up HTML→PDF (Playwright) as a service/stub and wire it to a smoke test (e.g., “Hello-PDF”) — empty in domain terms, but present as infrastructure (roadmap M0). Actual settlement PDFs from M3.
- **Object storage skeleton:** local FS with a checksum helper function (SHA-256) as a stub; encapsulate the interface so that S3 with Object Lock/WORM can later sit behind it (SDD §16). Domain voucher storage only in M2.

### 7.6 Definition of Done for M0 (handover to the next stage)

- [ ] App role without `BYPASSRLS`; separate migration role; documented in `infra/db/roles.sql`.
- [ ] Migrations: core tables + RLS policies (`ENABLE`+`FORCE`+`USING`/`WITH CHECK`) as explicit, reviewable Alembic steps.
- [ ] Tenant context is set transaction-locally from the session; never from client input.
- [ ] `test_rls_coverage.py` green (every tenant table protected; no unknown table).
- [ ] `test_tenant_isolation.py` assertions 1–5 green (incl. DB level 4 and fail-closed 5).
- [ ] `test_rbac.py` green (three roles take effect).
- [ ] App shell with login + org context; PDF and object storage skeleton present.
- [ ] Money/decimal convention documented (`NUMERIC`, `Decimal` on the app side), even if little money appears in M0 yet.
- [ ] No open SDD §18 question left unmarked as “guessed away” — escalations recorded.

---

## 8. Assumptions made

| # | Assumption | Impact if wrong | Clarify with |
| --- | --- | --- | --- |
| **A-1** | The standard chart of accounts (`cost_category`) is identical for all organizations in phase 1 (global system master data), SDD §7 (“fixed standard chart of accounts”). | If orgs are allowed to customize/version it, it must be copied per org + become tenant-scoped (RLS). Model rework in M2, not M0-critical. | Product/domain side, before M2 |
| **A-2** | A `User` belongs to **exactly one** organization (no n:m). | Group/vendor accounts across several management companies would need `membership` as a true n:m relation + extended tenant context. `membership` is already modeled so that the extension remains possible. | Product/domain side |
| **A-3** | Server-side sessions (cookie) as the auth default (instead of JWT); own auth instead of an external IdP. | JWT/IdP require different session/tenant-context handling; the interface is encapsulated, so switching is possible. | Tech lead/human |
| **A-4** | Segregation of funds is only prepared in the model in M0 (mandate/property reference), not enforced as a second DB RLS axis. | If early DB hardening of the segregation of funds is desired, a second policy axis/constraints are needed (sensible at the earliest with M6). | Human, at the latest before M6 |
| **A-5** | EUR as the only currency in phase 1 → `NUMERIC(14,2)` without a currency field is sufficient. | Multi-currency would require a currency code + possibly a higher scale. Unlikely for a German property management company. | Product (low risk) |
| **A-6** | Data hosting in the EU (SDD §15); operation as SaaS with a data processing agreement (AV-Vertrag) or self-hosted. Affects the infra region, not the M0 data model. | Region/hosting decision; no model impact. | Operations/legal |

---

## 9. Open items (blocking / non-blocking for M0/M1)

**From SDD §18 — relevant for M0/M1:**

| Item (SDD §18) | Blocks M0/M1? | Handling in this design |
| --- | --- | --- |
| Level of detail of the erasure/anonymization concept vs. audit-proof retention | **No** for M0/M1 (fields provided); **yes** for M9 | Structure laid out in Section 3.3 (`processing_restricted`, `retention_until`, `anonymized_at`); domain clarification (tax advisor/data protection) before M9. **[DECISION]** |
| Level of detail of tenant ledger (Mietkonto) / security deposit / security deposit account | **No** (M6) | `bank_account`/`lease_agreement` fields prepared; depth in M6. |
| Commercial units: up-front deduction (Vorwegabzug), VAT option | **Partially M1** (field `usage_type=Gewerbe` exists; logic only M3) | `unit.usage_type` provided; apportionment logic/up-front deduction is an M3 question. |
| Rounding rules per settlement | **No** (M3) | Shares stored exactly as `NUMERIC`; rounding as a deliberate M3 operation (never float). |
| Tax statements (§ 35a EStG) in the owner settlement | **No** (M4) | Note only; M4. |
| E-invoice receiving channels / validation strictness | **No** (M5) | `invoice_document` modeled; M5. |

**Architecture-specific items (raised by this document) that the human should decide:**

1. **[DECISION] Isolation strategy:** Confirm shared DB + RLS (option B, recommended) — the most consequential M0 decision. ADR-0001.
2. **[DECISION] Backend stack:** Python/FastAPI (recommended) vs. Node/NestJS; with FastAPI: SQLAlchemy+Alembic. ADR-0002.
3. **[DECISION] Auth model:** Server session (recommended) vs. JWT; own auth vs. IdP (A-3).
4. **[DECISION] Hardening of the segregation of funds:** App layer (M0) now; decide DB hardening with M6 (A-4).
5. **[DECISION] Scope of the chart of accounts:** global vs. per org (A-1) — before M2.

> **No self-certification (stance):** This design makes organization isolation, immutability, and retention *transparent and auditable* and discloses the assumptions made. It **cannot** guarantee that a later settlement or the voucher storage is court-/audit-proof in a specific case. Before the GoBD/GDPR-relevant parts go into production (at the latest M9), a domain sign-off by a tax advisor/specialist attorney is planned.

---

### Monthly Rent Metrics (T52)

`GET /api/reports/dashboard-kpis` uses the complete report admission,
live/source/contact rights and the existing financial lock. The existing pure
tenant ledger (Mietkonto) computes expected amount (Soll) and net balance separately per lease with rent history and
exclusive database end dates. Actual (Ist) follows exclusively the assigned rent month
of active booked KALTMIETE/NK (IN minus OUT); unassigned amounts are reported
separately. Credit balances from other leases are not offset. The scope must cover the entire
relevant history; incoherent sources yield 409 instead of partial figures.
Vacancy uses current units and the occupancy at month end. No new
persistence, migration, rebooking or reconstruction of historical unit versions.

### Selected Dunning Drafts (T53)

`POST /api/dunning/batch/preview` is non-writing; the shared
`DunningService.tenant_basis` uses the history-sensitive canonical tenant ledger
also for individual drafts. Complete prepare/source/contact admission precedes
the RLS-filtered enumeration. The digest covers sources, settings and
escalation history, not only the principal claim. `POST /batch` creates the
confirmed subset atomically under ACL → financial → request lock; later errors
roll back drafts and provenance together. No issuing or sending.

0135 keeps only immutable original IDs in the actor/org-bound `dunning_batch`
(FORCE RLS, SELECT/INSERT). An identical request after a source change returns, after
renewed admission of all originals, the same IDs, not a newly computed dunning run;
changed requests with the same key and stale new previews yield 409.
Issued dunning notices and their PDFs remain unchanged. Populated downgrades
refuse data loss; the real test round trip compares old data and all
security objects within a rolled-back transaction.

### Prior-Year Actuals as Editable Plan Template (T54)

`POST /api/economic-plans/previous/preview` reads current confirmed costs including
category ID/version only after complete prior-year read and target-year prepare
admission. Local explicit context switches leave the general
scope-mismatch guards unchanged. Service-period shares (inclusive days,
HALF_UP per cost item) and signed credit notes are netted per category;
reserves remain separate. Incomplete rights, negative net amounts and invalid
categories yield errors instead of partial proposals. The digest covers sources and
metadata, not only totals.

`POST /previous` confirms exclusively edited amounts of the same categories,
re-checks the digest under the financial lock and atomically creates an uncomputed
draft. Existing JSON holds `draft_inputs` and `previous_actuals`; GET restores
the editable inputs after reload. The normal compute/finalize
pipeline keeps its attestation/snapshot rules and the prior-year provenance.
No migration or change to old costs/finalized plans, no automatic
WEG resolution. An unclear create outcome is not retried automatically.

### Editable Master Data with Protection of Existing Records (T55)

Bank PATCH requires live writer, financial lock before row lock, exact prepare
and canonical property source right. Vendor PUT additionally requires the
current contact prepare/processing right; management-mandate PATCH remains
reserved for the live admin. Already bound mandate periods/types remain
immutable through the existing SQL guard (409); labels are editable.

Revision 0136 adds exclusively bank/vendor `is_active`, nullable year of construction
and `Numeric(14,2)` total area, plus its own additive triggers. Inactive sources
remain historically readable but are blocked for new financial references. The SQL
bank identity protection also checks existing references hidden by RLS;
renamings change neither the bank fingerprint nor the SEPA consent. A narrowly
checked exact counter-entry of a booked original remains possible as a reversal.
The old policies/functions and original vouchers remain unchanged. Populated
new fields prevent a lossy downgrade; the real round trip
compares old columns and security objects in a rolled-back transaction.

### Reference-Protected Deletion of Erroneous Entries (T56)

Four fixed DELETE routes use live writer, ACL/financial lock, parent-before-
child row locks and the existing canonical prepare checks. A purely
read-only, narrowly authorized definer function of additive revision 0137 inventories
all incoming foreign keys including hidden/CASCADE edges
as well as logical source, audit, import/batch and snapshot references. It returns
only a bounded reference class, no foreign IDs or contents. The internal
JSON matcher compares complete UUID values/keys, not substrings.

DELETE itself remains under unchanged RLS, with explicit organization and
RETURNING check; zero affected rows are never success. Deferred constraints
are executed before 204. References or existing source protection yield 409,
missing authority 404/403. No cascades, profile cleanup, audit deletion or
circumvention of the existing ENFORCED contact deletion policy. The real down/up test
compares all old data/security objects and byte-identical issued PDFs.

## 10. What this document deliberately does NOT decide

- Concrete settlement/distribution logic (M3/M4) — a separate `engine-correctness` step.
- In-depth GoBD voucher storage design (M2) — only the load-bearing structure here.
- PDF layouts (SDD §18, open).
- Final choice of the immutability enforcement mechanism (trigger vs. grants vs. frozen table) — detail in M2.
- Calendar/schedule (roadmap: needs team size/velocity).
