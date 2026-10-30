# ADR-0001: Organization isolation via shared DB + PostgreSQL Row-Level Security

Status: **Proposed** (confirmation by the human pending)
Date: 2026-06-01 · Context: M0 · Reference: [../architecture.md](../architecture.md) Section 2 · SDD §4, §16

## Context

The product is explicitly multi-organization (SDD §4): a professional property management company looks after many third-party owners/homeowners' associations (WEG). Organization isolation is a hard invariant (no cross-org access) and additionally touches the segregation of funds and trust assets — a data leak is not only a data protection error but a potentially liability-relevant one. The strategy can hardly be retrofitted and is therefore the most consequential M0 decision.

## Decision

**Shared database + mandatory `organization_id` scoping + PostgreSQL Row-Level Security (RLS)**, enforced on three levels:
1. DB RLS with `ENABLE` + `FORCE ROW LEVEL SECURITY` and `USING`/`WITH CHECK` policies, bound to a session GUC `app.current_org_id`; the app connects as a role **without** `BYPASSRLS` and without table ownership.
2. Organization-context middleware that derives `org_id` only from the server-side session and sets it transaction-locally (`SET LOCAL`); fail-closed (no context → zero rows).
3. Repository/query guard (explicit filter for performance + meaningful errors) plus a CI test that enforces RLS coverage of every organization-scoped table.

## Alternatives considered

- **A — Shared DB, app-layer scoping only:** rejected. Depends on the discipline of every single query; one forgotten `WHERE` leaks other organizations' data. No safety net.
- **C — Schema per organization:** rejected for the MVP. Strong isolation, but multiplies migration/provisioning/routing complexity by the number of organizations and makes cross-mandate reporting for the management company itself harder — without a proportional benefit over RLS.
- **D — Database/cluster per organization:** rejected. Disproportionately expensive/complex for an MVP.

## Consequences

**Positive:** defense in depth (the DB is the last line that cannot be bypassed); one schema version/one migration path; simple, RLS-controlled cross-WEG evaluations for the management company; objectively testable (isolation test at API and DB level).

**Negative / to note:** Care is needed with (a) the app DB role without `BYPASSRLS`, (b) reliable, transaction-local setting of the context with connection pooling, (c) migrations/background jobs as a separate privileged role, (d) indexes on `organization_id` for performance. These points are recorded as binding steps in the scaffolding plan (architecture.md Section 7).

**Scope boundary:** Segregation of funds (within one org between mandates/WEGs) is a second axis; in M0 it is only prepared in the model, DB hardening at the earliest with M6 (see architecture.md 2.6).
