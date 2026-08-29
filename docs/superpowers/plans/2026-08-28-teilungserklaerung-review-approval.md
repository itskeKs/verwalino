# Teilungserklärung Review and Approval Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Let authorized users review every extracted proposal with its source, correct or reject it, and atomically apply an approved declaration set to either a new or an existing property.

**Architecture:** The existing import candidates remain an immutable extraction proposal plus revisioned human decisions. A review service recalculates server-owned risk policy on every write, prevents stale or unsafe bulk decisions, and produces an explicit approval preview. One transactional approval service locks the import and target property, validates the complete decision set, maps temporary extraction keys to canonical IDs, writes property data plus provenance, and marks the import APPLIED only after every canonical write succeeds. The React UI is a thin client over these invariants.

**Tech Stack:** Python 3.12, FastAPI, Pydantic 2, SQLAlchemy 2 async, PostgreSQL 16 with FORCE RLS, Alembic, pytest, React 19, TypeScript 6, React Router 7, Vitest, Testing Library, jsdom.

---

## Plan boundary and execution order

This is plan 2 of 3. It starts after the foundation plan has reached NEEDS_REVIEW and ends after a reviewed import can be applied with complete source provenance.

1. Execute 2026-08-28-teilungserklaerung-import-foundation.md.
2. Execute this plan.
3. Execute 2026-08-28-gebaeudewissen-search-qa.md.

Repository-specific override: do not commit, push, deploy, or use a live OpenAI key unless the user explicitly authorizes that separate action. Each task ends with a diff/test checkpoint instead of a commit.

## Isolated backend test environment

Before the first backend task, verify that the reserved Compose project is not
already owned by another process, then start the tmpfs database created by plan
1 and export its URLs:

~~~bash
docker compose -p hv-declaration-tests -f infra/docker-compose.declaration-test.yml ps
docker compose -p hv-declaration-tests -f infra/docker-compose.declaration-test.yml up -d --wait
export HV_DATABASE_URL=postgresql+asyncpg://app_rw:app_secret@localhost:55432/hausverwaltung
export HV_DATABASE_URL_MIGRATOR=postgresql+asyncpg://app_migrator:migrator_secret@localhost:55432/hausverwaltung
export HV_DATABASE_URL_SYNC=postgresql://app_migrator:migrator_secret@localhost:55432/hausverwaltung
~~~

If the initial ps shows an unexpected pre-existing container, do not reuse or
stop it. Every backend pytest/alembic command below must use these exported
port-55432 URLs because the test fixture destructively migrates its configured
database.

## File map

### Create

- backend/migrations/versions/0025_declaration_approval_tables.py — canonical legal references, assignments, document/rule relations, rules, and approved source records.
- backend/app/models/property_legal_reference.py — canonical register and legal-reference values.
- backend/app/models/property_assignment.py — special-use rights and physical assignments.
- backend/app/models/property_rule.py — approved rules, relations, and source citations.
- backend/app/models/property_document_relation.py — approved document relationships.
- backend/app/models/approved_fact_source.py — provenance for approved core fields.
- backend/app/repositories/declaration_review_repo.py — revision-safe candidate decision persistence.
- backend/app/repositories/property_knowledge_repo.py — canonical rule, relation, assignment, and source writes used by approval.
- backend/app/declarations/review_schemas.py — review, preview, and approval DTOs.
- backend/app/declarations/approval.py — validation, key mapping, new-property apply, existing-property diff, and atomic commit.
- backend/app/services/declaration_review_service.py — review commands and approval preview.
- backend/app/api/property_documents.py — tenant-safe document metadata and content endpoints.
- backend/tests/test_declaration_review_api.py — review/RBAC/revision/bulk-policy tests.
- backend/tests/test_property_document_content.py — content authorization and integrity tests.
- backend/tests/test_declaration_approval_new_property.py — complete new-property apply tests.
- backend/tests/test_declaration_approval_existing_property.py — update, no-delete, MEA, and rollback tests.
- frontend/src/pages/DeclarationImportPage.tsx — upload/classification/status wizard.
- frontend/src/pages/DeclarationReviewPage.tsx — grouped review and final approval.
- frontend/src/components/declarations/CandidateDecision.tsx — individual decision controls.
- frontend/src/components/declarations/EvidenceViewer.tsx — authorized PDF source pane.
- frontend/src/components/declarations/ReviewGroup.tsx — grouped candidate rendering.
- frontend/src/components/declarations/ApprovalSummary.tsx — final diff and blockers.
- frontend/src/pages/DeclarationImportPage.test.tsx — upload wizard tests.
- frontend/src/pages/DeclarationReviewPage.test.tsx — risk, source, correction, and approval tests.
- frontend/src/test/setup.ts — jsdom/testing-library setup.

### Modify

- backend/app/models/__init__.py — register all approval ORM models.
- backend/app/main.py — register property-document routes.
- backend/app/api/declaration_imports.py — expose candidates, review, preview, retry, cancel, and approve endpoints.
- backend/app/repositories/declaration_candidate_repo.py — row locks, decision update, and accepted-candidate reads.
- backend/app/models/declaration_candidate.py — corrected value and review comment fields.
- backend/app/models/declaration_import.py — approval idempotency key and stored result fields.
- backend/app/repositories/declaration_import_repo.py — draft revision CAS and APPLIED transition.
- backend/app/repositories/property_repo.py — create and lock helpers for approval.
- backend/app/repositories/unit_repo.py — lookup by property/number and bulk MEA helpers.
- backend/app/services/property_document_service.py — authorized list/content streaming.
- backend/tests/test_rls_coverage.py — include every 0025 tenant table.
- backend/tests/conftest.py — reviewed-candidate and content-store fixtures.
- frontend/package.json — add test script and test dependencies.
- frontend/package-lock.json — lock test dependencies.
- frontend/vite.config.ts — configure Vitest/jsdom.
- frontend/src/api/client.ts — add authenticated streaming URL helper without JSON headers.
- frontend/src/api/types.ts — import, candidate, evidence, preview, document, and MEA DTOs.
- frontend/src/api/endpoints.ts — declaration and property-document calls.
- frontend/src/App.tsx — add new import and review routes.
- frontend/src/pages/PropertiesPage.tsx — add the new-property import entry point.
- frontend/src/pages/PropertyDetailPage.tsx — add the existing-property amendment entry point and confirmed MEA display.
- frontend/src/pages/UnitDetailPage.tsx — show Unit.mea with the enclosing Property denominator.
- frontend/src/index.css — accessible wizard, review, issue, and split-pane styles.

## Task 1: Persist approved legal facts, rules, relationships, and provenance

**Files:**

- Create: backend/migrations/versions/0025_declaration_approval_tables.py
- Create: backend/app/models/property_legal_reference.py
- Create: backend/app/models/property_assignment.py
- Create: backend/app/models/property_rule.py
- Create: backend/app/models/property_document_relation.py
- Create: backend/app/models/approved_fact_source.py
- Modify: backend/app/models/declaration_import.py
- Modify: backend/app/models/declaration_candidate.py
- Modify: backend/app/models/__init__.py
- Modify: backend/tests/test_rls_coverage.py
- Create: backend/tests/test_declaration_approval_new_property.py

- [ ] **Step 1: Write the failing schema and RLS tests**

Add these assertions to backend/tests/test_declaration_approval_new_property.py:

~~~python
import sqlalchemy as sa


APPROVAL_TABLES = {
    "property_legal_reference",
    "property_assignment",
    "property_document_relation",
    "property_rule",
    "property_rule_relation",
    "property_rule_source",
    "approved_fact_source",
}


async def test_approval_tables_have_organization_and_property_fk(db_engine) -> None:
    async with db_engine.connect() as connection:
        rows = await connection.execute(
            sa.text(
                """
                SELECT table_name, column_name
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = ANY(:tables)
                  AND column_name IN ('organization_id', 'property_id')
                """
            ),
            {"tables": sorted(APPROVAL_TABLES)},
        )
    columns = {(row.table_name, row.column_name) for row in rows}
    for table in APPROVAL_TABLES:
        assert (table, "organization_id") in columns
    for table in APPROVAL_TABLES:
        assert (table, "property_id") in columns


async def test_every_approval_table_is_tenant_isolated(
    org_a_approval_graph,
    org_b_session,
) -> None:
    for table in sorted(APPROVAL_TABLES):
        result = await org_b_session.execute(
            sa.text(f'SELECT count(*) FROM "{table}"')
        )
        assert result.scalar_one() == 0
~~~

org_a_approval_graph inserts one dependency-valid row into every approval
table through an org A tenant session, including both relation tables and both
source tables. Add a complementary parametrized insert test that attempts an
org A organization_id through the org B session for each table and asserts an
RLS DBAPIError. These behavior tests accompany the policy-presence checks.

Also add exact `app_rw` privilege tests. Query
`information_schema.table_privileges` and
`information_schema.column_privileges` as the migrator and assert:

- every approval table has table-level SELECT and INSERT, and no table-level
  UPDATE, DELETE, TRUNCATE, REFERENCES, or TRIGGER grant;
- property_legal_reference and property_assignment have column-level UPDATE
  only for status, valid_to, and updated_at;
- property_rule has column-level UPDATE only for status, valid_to, search_text,
  and updated_at; search_text is explicitly non-authoritative derived data;
- property_document_relation has column-level UPDATE only for status and
  updated_at;
- approved_fact_source has column-level UPDATE only for status, valid_to, and
  updated_at;
- property_rule_relation and property_rule_source have no UPDATE privilege.

The org A fixture proves that SELECT and INSERT work through an `app_rw`
tenant session. Add parametrized behavior tests that, through the same role,
perform each permitted lifecycle-column update, reject an update of an
immutable content column on every table, and reject DELETE on all seven
tables. Assert PostgreSQL insufficient-privilege errors separately from the
cross-tenant RLS errors so a missing GRANT cannot masquerade as isolation.

Extend backend/tests/test_rls_coverage.py so APPROVAL_TABLES are part of TENANT_TABLES. The existing parametrized tests must demand ENABLE ROW LEVEL SECURITY, FORCE ROW LEVEL SECURITY, and tenant policies for every new table.

- [ ] **Step 2: Run the focused tests and record the expected failure**

Run:

~~~bash
cd backend
pytest tests/test_declaration_approval_new_property.py tests/test_rls_coverage.py -q
~~~

Expected: missing-table, missing-TENANT_TABLES, and missing app_rw privilege
failures.

- [ ] **Step 3: Create migration 0025 with exact constraints**

Use revision 0025 and down_revision 0024. Create all seven tables with UUID primary keys, organization_id, created_at, updated_at where mutable, and these domain columns:

- property_legal_reference: property_id, reference_type, district, cadastral_district, field_number, parcel, sheet, details_json, status, valid_from, valid_to, source_import_id.
- property_assignment: property_id, unit_id nullable, stable_key,
  assignment_type, designation, location, description, details_json, status,
  valid_from, valid_to, source_import_id.
- property_document_relation: property_id, source_document_id,
  target_document_id, relation_type, status, source_import_id.
- property_rule: property_id, stable_key, topic, title, summary, scope_json, effect_json, status, valid_from, valid_to, source_import_id, approved_by, approved_at, search_text.
- property_rule_relation: organization_id, property_id, source_rule_id, target_rule_id, relation_type, source_import_id.
- property_rule_source: organization_id, property_id, property_rule_id, property_document_id, pdf_page, printed_page, section, quote, field_path.
- approved_fact_source: organization_id, property_id, subject_kind, subject_id,
  field_key, approved_value_json, value_fingerprint, status, valid_from,
  valid_to, property_document_id, pdf_page, printed_page, section, quote,
  source_import_id.

Add check constraints:

- property_rule.status is EFFECTIVE, SUPERSEDED, NEEDS_CLARIFICATION, or RETRACTED.
- property_assignment.status is EFFECTIVE, SUPERSEDED, or
  NEEDS_CLARIFICATION.
- property_document_relation.status is EFFECTIVE, SUPERSEDED, or
  NEEDS_CLARIFICATION.
- property_legal_reference.status is EFFECTIVE, SUPERSEDED, or
  NEEDS_CLARIFICATION.
- approved_fact_source.status is EFFECTIVE or SUPERSEDED.
- relation_type is AMENDS, SUPPLEMENTS, REFERENCES for document relations.
- rule relation_type is SUPERSEDES, SUPPLEMENTS, or CONFLICTS.
- pdf_page is greater than zero.
- quote is non-empty after trimming.
- valid_to is null or greater than valid_from.
- approved_fact_source EFFECTIVE requires valid_to null and SUPERSEDED
  requires valid_to non-null.
- source and target IDs differ.

Add unique constraints:

- property_rule on organization_id, property_id, stable_key.
- property_assignment on organization_id, property_id, stable_key.
- property_rule_source on property_rule_id, property_document_id, pdf_page, field_path.
- approved_fact_source on subject_kind, subject_id, field_key,
  value_fingerprint, property_document_id, pdf_page, source_import_id.
- both relation tables on source ID, target ID, relation_type.

Also add approval_idempotency_key VARCHAR(128) and approval_result_json JSONB
to declaration_import. The pair is null before approval and written in the
same transaction as APPLIED. Add a check constraint that either both are null
or both are non-null when status is APPLIED.

Add corrected_value_json JSONB and review_comment TEXT to
declaration_candidate. corrected_value_json is required exactly when
review_status is CORRECTED; review_comment is limited to 2,000 characters by
the API and is required by the service for the high-risk rejection and
clarification cases defined below. Map both columns in
backend/app/models/declaration_candidate.py.

Use approved_fact_source with subject_kind DOCUMENT_RELATION and the canonical
relation UUID for every accepted document-relation evidence record. This
preserves all relation evidence without duplicating quote/page columns on the
relation row.

approved_value_json is the exact typed canonical value at approval time.
value_fingerprint is SHA-256 over canonical JSON with sorted keys, UTF-8, and
compact separators. On an UPDATE, mark prior EFFECTIVE rows for the same
subject_kind/subject_id/field_key SUPERSEDED with valid_to equal to approved_at,
then insert the new EFFECTIVE source rows. On KEEP, add another EFFECTIVE source
only when its value_fingerprint equals the current effective fingerprint. This
keeps historical evidence but prevents an old deed from being cited as support
for a changed current value.

property_rule.stable_key identifies one immutable rule version, not a mutable
topic family. Build it deterministically with UUIDv5 from property_id,
source_import_id, and the reviewed candidate stable_key. A replacement gets a
new key and links to the prior rule through SUPERSEDES; never overwrite a prior
rule merely because both use the same open topic.

Add indexes on every foreign key plus organization_id/property_id. Apply the same tenant isolation expression used by migration 0024 to all seven tables:

~~~sql
ALTER TABLE property_rule ENABLE ROW LEVEL SECURITY;
ALTER TABLE property_rule FORCE ROW LEVEL SECURITY;
CREATE POLICY property_rule_tenant_select ON property_rule
    FOR SELECT USING (
        organization_id =
        NULLIF(current_setting('app.current_org_id', true), '')::uuid
    );
CREATE POLICY property_rule_tenant_insert ON property_rule
    FOR INSERT WITH CHECK (
        organization_id =
        NULLIF(current_setting('app.current_org_id', true), '')::uuid
    );
CREATE POLICY property_rule_tenant_update ON property_rule
    FOR UPDATE
    USING (
        organization_id =
        NULLIF(current_setting('app.current_org_id', true), '')::uuid
    )
    WITH CHECK (
        organization_id =
        NULLIF(current_setting('app.current_org_id', true), '')::uuid
    );
CREATE POLICY property_rule_tenant_delete ON property_rule
    FOR DELETE USING (
        organization_id =
        NULLIF(current_setting('app.current_org_id', true), '')::uuid
    );
~~~

Generate equivalent policies for each table; do not create a privileged bypass role.

Before granting runtime access, execute these exact grants so inherited
migration defaults cannot broaden the contract:

~~~sql
REVOKE ALL PRIVILEGES ON TABLE
    property_legal_reference,
    property_assignment,
    property_document_relation,
    property_rule,
    property_rule_relation,
    property_rule_source,
    approved_fact_source
FROM app_rw;

GRANT SELECT, INSERT ON TABLE
    property_legal_reference,
    property_assignment,
    property_document_relation,
    property_rule,
    property_rule_relation,
    property_rule_source,
    approved_fact_source
TO app_rw;

GRANT UPDATE (status, valid_to, updated_at)
    ON property_legal_reference TO app_rw;
GRANT UPDATE (status, valid_to, updated_at)
    ON property_assignment TO app_rw;
GRANT UPDATE (status, valid_to, search_text, updated_at)
    ON property_rule TO app_rw;
GRANT UPDATE (status, updated_at)
    ON property_document_relation TO app_rw;
GRANT UPDATE (status, valid_to, updated_at)
    ON approved_fact_source TO app_rw;
~~~

This yields these column-level UPDATE grants only:

- status, valid_to, updated_at on property_legal_reference and
  property_assignment;
- status, valid_to, search_text, updated_at on property_rule;
- status and updated_at on property_document_relation;
- status, valid_to, updated_at on approved_fact_source;
- no UPDATE at all on property_rule_relation or property_rule_source.

Revoke DELETE, TRUNCATE, REFERENCES, and TRIGGER on every approval table.
Do not grant sequence access because all primary keys are UUIDs. Add a BEFORE
UPDATE trigger on approved_fact_source as defense in depth that permits only
status, valid_to, and updated_at to change; approved_value_json,
value_fingerprint, document/page/section/quote, subject, field, and
source_import_id are immutable. Add focused DBAPIError tests for source update
and deletion attempts. The trigger must also enforce the one-way lifecycle
transition EFFECTIVE to SUPERSEDED and prevent reopening a superseded source.

Add a separate BEFORE UPDATE trigger on property_rule that freezes stable_key,
topic, title, summary, scope_json, effect_json, source_import_id, approved_by,
approved_at, organization_id, and property_id. It permits only status,
valid_to, search_text, and updated_at, enforces valid lifecycle transitions,
and caps search_text to the configured deterministic projection limit.
property_rule.search_text is a replaceable search projection, never the source
of a displayed rule or Q&A fact; responses always use canonical rule columns
and cited source rows. Privilege behavior tests must prove a projection refresh
succeeds while a title/scope/source-import mutation fails.

- [ ] **Step 4: Add ORM models and register them**

Use the project UUID/timestamp mixins from backend/app/models/base.py. Store controlled values as strings plus database check constraints, matching existing model conventions. Relationships must not use unscoped lazy loads. Export each model from backend/app/models/__init__.py so Alembic and tests import the full metadata.

- [ ] **Step 5: Run migration and RLS tests**

Run:

~~~bash
cd backend
alembic upgrade head
pytest tests/test_rls_coverage.py tests/test_declaration_approval_new_property.py -q
~~~

Expected: PASS.

- [ ] **Step 6: Inspect the task diff**

Run:

~~~bash
git diff -- backend/migrations/versions/0025_declaration_approval_tables.py backend/app/models backend/tests/test_rls_coverage.py backend/tests/test_declaration_approval_new_property.py
git diff --check
~~~

Checkpoint: only the declared persistence/RLS files changed.

## Task 2: Add revision-safe individual and bulk review APIs

**Files:**

- Create: backend/app/declarations/review_schemas.py
- Create: backend/app/repositories/declaration_review_repo.py
- Create: backend/app/services/declaration_review_service.py
- Modify: backend/app/repositories/declaration_candidate_repo.py
- Modify: backend/app/repositories/declaration_import_repo.py
- Modify: backend/app/api/declaration_imports.py
- Create: backend/tests/test_declaration_review_api.py
- Modify: backend/tests/conftest.py

- [ ] **Step 1: Write failing API tests**

Cover all of these cases in backend/tests/test_declaration_review_api.py:

~~~python
async def test_rule_candidate_requires_individual_review(client, clerk_headers, review_import) -> None:
    rule = review_import.candidate("RULE")
    response = await client.post(
        f"/api/declaration-imports/{review_import.id}/candidates/bulk-review",
        headers=clerk_headers,
        json={
            "draft_revision": review_import.draft_revision,
            "candidate_ids": [str(rule.id)],
            "decision": "ACCEPTED",
        },
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "INDIVIDUAL_REVIEW_REQUIRED"


async def test_stale_review_revision_changes_nothing(client, clerk_headers, review_import) -> None:
    candidate = review_import.candidate("PROPERTY_FIELD")
    response = await client.patch(
        f"/api/declaration-imports/{review_import.id}/candidates/{candidate.id}",
        headers=clerk_headers,
        json={"draft_revision": 0, "decision": "ACCEPTED"},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "STALE_DRAFT_REVISION"


async def test_correction_is_stored_as_typed_value(client, clerk_headers, review_import) -> None:
    candidate = review_import.candidate_for("property:new", "street")
    response = await client.patch(
        f"/api/declaration-imports/{review_import.id}/candidates/{candidate.id}",
        headers=clerk_headers,
        json={
            "draft_revision": review_import.draft_revision,
            "decision": "CORRECTED",
            "corrected_value": "Talstraße 112",
            "comment": "Schreibweise geprüft",
        },
    )
    assert response.status_code == 200
    assert response.json()["candidate"]["review_status"] == "CORRECTED"
    assert response.json()["draft_revision"] == review_import.draft_revision + 1
~~~

Also test:

- READ_ONLY receives 403 for individual review, bulk review, retry, cancel, and approve.
- READ_ONLY receives 404 for a foreign-tenant draft.
- ACCEPTED and CORRECTED require at least one valid evidence record for every critical candidate.
- REJECTED requires a comment for UPDATE, RULE, DOCUMENT_RELATION, MEA_RESCALE, WARNING, or BLOCKER candidates.
- NEEDS_CLARIFICATION requires a non-empty comment.
- any unresolved BLOCKER keeps can_approve false.
- a bulk request is all-or-nothing and rejects one unsafe member without changing safe members.
- a second identical individual request with the new revision is idempotent only if decision, correction, and comment match.

- [ ] **Step 2: Run the tests and record the expected 404/422 failures**

Run:

~~~bash
cd backend
pytest tests/test_declaration_review_api.py -q
~~~

- [ ] **Step 3: Define strict review DTOs**

In backend/app/declarations/review_schemas.py create extra-forbid Pydantic models:

~~~python
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


Decision = Literal[
    "ACCEPTED",
    "CORRECTED",
    "REJECTED",
    "NEEDS_CLARIFICATION",
]


class CandidateReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    draft_revision: int = Field(ge=0)
    decision: Decision
    corrected_value: Any | None = None
    comment: str | None = Field(default=None, max_length=2_000)

    @model_validator(mode="after")
    def validate_decision_payload(self) -> "CandidateReviewRequest":
        if self.decision == "CORRECTED" and self.corrected_value is None:
            raise ValueError("CORRECTED requires corrected_value")
        if self.decision != "CORRECTED" and self.corrected_value is not None:
            raise ValueError("corrected_value is only valid for CORRECTED")
        if self.decision == "NEEDS_CLARIFICATION" and not (self.comment or "").strip():
            raise ValueError("NEEDS_CLARIFICATION requires comment")
        return self


class BulkCandidateReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    draft_revision: int = Field(ge=0)
    candidate_ids: list[str] = Field(min_length=1, max_length=250)
    decision: Literal["ACCEPTED", "REJECTED"]
    comment: str | None = Field(default=None, max_length=2_000)
~~~

Define CandidateOut, EvidenceOut, IssueOut, ReviewImportOut,
ApprovalPreviewOut, and ApprovalRequest. CandidateOut exposes the persisted
field as review_status; request.decision is mapped to that column by the
service. Every review response returns the new draft_revision. ApprovalRequest
contains exactly draft_revision and an idempotency_key with length 16 to 128.

- [ ] **Step 4: Implement the transaction and optimistic revision boundary**

DeclarationReviewRepository must:

1. SELECT the import FOR UPDATE by id and organization_id.
2. Require status NEEDS_REVIEW.
3. Compare request draft_revision with the locked row.
4. Lock all addressed candidates in stable ID order.
5. Recompute CandidatePolicy from candidate type, operation, confidence, evidence, and unresolved issues.
6. Validate and persist the decision plus reviewer audit.
7. Rerun deterministic validators affected by a correction. Mark an issue
   RESOLVED with reviewer/time only when its invariant now passes; never allow
   a user to acknowledge away a BLOCKER.
8. Insert a declaration_import.candidate_reviewed AuditEvent containing import
   ID, candidate ID, decision, and revision but no value, comment, or quote.
9. Increment declaration_import.draft_revision exactly once.
10. Return only after flush.

Use one service transaction supplied by get_db; do not open or commit an independent session inside the repository.

For bulk review, reject the complete request before writes when any selected candidate has requires_individual_review true. Recompute policy server-side even when a stored value says false.

- [ ] **Step 5: Expose candidate and review endpoints**

Add:

- GET /api/declaration-imports/{id}/candidates
- PATCH /api/declaration-imports/{id}/candidates/{candidate_id}
- POST /api/declaration-imports/{id}/candidates/bulk-review
- GET /api/declaration-imports/{id}/approval-preview
- POST /api/declaration-imports/{id}/retry
- POST /api/declaration-imports/{id}/cancel

GET candidate details is write-role-only because the data is an unapproved
draft. All mutations use require_write_dep. Foreign tenant, missing candidate,
and candidate not belonging to the import all return 404. Retry returns the
current import unchanged when it is already QUEUED, PREFLIGHT, EXTRACTING, or
RECONCILING. It requeues FAILED or RETRY_WAIT only for PROVIDER_TIMEOUT,
PROVIDER_RATE_LIMIT, PROVIDER_SERVER_ERROR, OCR_TIMEOUT, or
WORKER_INTERRUPTED, while attempt and cost budgets remain. Cancel is idempotent
for CANCELLED, rejects APPLIED, and invalidates any active lease by incrementing
lease_generation before changing an allowed non-APPLIED state to CANCELLED.

- [ ] **Step 6: Run focused tests**

Run:

~~~bash
cd backend
pytest tests/test_declaration_review_api.py tests/test_declaration_policy.py tests/test_declaration_import_api.py -q
~~~

Expected: PASS.

- [ ] **Step 7: Inspect the task diff**

Run:

~~~bash
git diff -- backend/app/declarations/review_schemas.py backend/app/repositories/declaration_review_repo.py backend/app/services/declaration_review_service.py backend/app/api/declaration_imports.py backend/tests/test_declaration_review_api.py
git diff --check
~~~

Checkpoint: no canonical property write exists yet.

## Task 3: Serve property documents through an authorized integrity-checked endpoint

**Files:**

- Create: backend/app/api/property_documents.py
- Modify: backend/app/api/declaration_imports.py
- Modify: backend/app/services/property_document_service.py
- Modify: backend/app/main.py
- Create: backend/tests/test_property_document_content.py

- [ ] **Step 1: Write failing document-list and content tests**

Test:

- an authenticated user can list documents bound to their property;
- READ_ONLY can list and read approved property documents;
- a write-role reviewer can read an unbound draft document only through its
  own import route;
- READ_ONLY cannot read an unbound draft document;
- an unbound draft document is invisible through property routes;
- a document from another organization returns 404;
- a document attached to another property in the same organization returns 404;
- response headers include Content-Type application/pdf, Content-Disposition inline with a sanitized filename, X-Content-Type-Options nosniff, Content-Length, and an ETag derived from SHA-256;
- corrupt size or SHA-256 causes 409 DOCUMENT_INTEGRITY_FAILED before a byte is streamed;
- Range is not advertised in this first implementation and a supplied page query never reaches storage path resolution.

Representative test:

~~~python
async def test_content_is_property_scoped_and_inline(
    client,
    readonly_headers,
    approved_property_document,
) -> None:
    response = await client.get(
        (
            f"/api/properties/{approved_property_document.property_id}"
            f"/documents/{approved_property_document.id}/content?page=7"
        ),
        headers=readonly_headers,
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["etag"] == f'"{approved_property_document.content_sha256}"'
    assert response.content.startswith(b"%PDF-")
~~~

- [ ] **Step 2: Run the tests and verify they fail**

Run:

~~~bash
cd backend
pytest tests/test_property_document_content.py -q
~~~

- [ ] **Step 3: Implement service-level authorization and integrity checks**

PropertyDocumentService.get_approved_content must first load a row with this complete predicate:

~~~sql
organization_id = :organization_id
AND property_id = :property_id
AND id = :document_id
AND archived_at IS NULL
~~~

Then call DocumentStore verification for both expected content_byte_size and sha256. Return a file-like stream only after verification succeeds. Never derive a filesystem path from filename, page, property ID, or document ID; resolve only the stored content-addressed storage_uri through DocumentStore.

- [ ] **Step 4: Add property document routes**

Create:

- GET /api/properties/{property_id}/documents
- GET /api/properties/{property_id}/documents/{document_id}/content
- GET /api/declaration-imports/{import_id}/documents/{document_id}/content

The property routes use get_current_user and get_db, so READ_ONLY can use
approved documents. The import content route uses require_write_dep, requires
the document to be linked to that exact tenant-local import, and is available
only while reviewing a draft. Verify the property or import exists within the
tenant before loading metadata. The page query is accepted as a positive
integer for viewer compatibility but is ignored by storage.

Register the router before the SPA fallback in backend/app/main.py.

- [ ] **Step 5: Run tests**

Run:

~~~bash
cd backend
pytest tests/test_property_document_content.py tests/test_declaration_preflight.py tests/test_tenant_isolation.py -q
~~~

Expected: PASS.

- [ ] **Step 6: Inspect the task diff**

Run:

~~~bash
git diff -- backend/app/api/property_documents.py backend/app/api/declaration_imports.py backend/app/services/property_document_service.py backend/app/main.py backend/tests/test_property_document_content.py
git diff --check
~~~

## Task 4: Atomically apply a reviewed import to a new property

**Files:**

- Create: backend/app/repositories/property_knowledge_repo.py
- Create: backend/app/declarations/approval.py
- Modify: backend/app/repositories/property_repo.py
- Modify: backend/app/repositories/unit_repo.py
- Modify: backend/app/repositories/declaration_import_repo.py
- Modify: backend/app/api/declaration_imports.py
- Modify: backend/tests/test_declaration_approval_new_property.py

- [ ] **Step 1: Write the failing complete-apply test**

Build a reviewed NEW_PROPERTY import with:

- name, street, zip_code, city, management_type;
- a confirmed MEA denominator;
- two UNIT_CREATE candidates using stable keys unit:1 and unit:2 with
  500,000/1,000,000 each;
- a legal reference;
- a parking assignment linked to unit:2;
- an effective heating-cost rule with two evidence sources;
- a document AMENDS relation;
- all mandatory candidates individually reviewed.

Then assert:

~~~python
async def test_new_property_approval_is_atomic_and_traceable(
    client,
    clerk_headers,
    fully_reviewed_new_property_import,
    tenant_session,
) -> None:
    response = await client.post(
        f"/api/declaration-imports/{fully_reviewed_new_property_import.id}/approve",
        headers=clerk_headers,
        json={
            "draft_revision": fully_reviewed_new_property_import.draft_revision,
            "idempotency_key": "new-property-approval-0001",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "APPLIED"
    assert body["property_id"]

    state = await load_canonical_property_graph(tenant_session, body["property_id"])
    assert state.property.mea_denominator == 1_000_000
    assert state.property.mea_scale_status == "CONFIRMED"
    assert [unit.mea for unit in state.units] == [500000, 500000]
    assert state.assignments[0].unit_id == state.units[1].id
    assert state.rules[0].status == "EFFECTIVE"
    assert len(state.rule_sources) == 2
    assert all(source.source_import_id == fully_reviewed_new_property_import.id for source in state.fact_sources)
~~~

Also test that the same idempotency key returns the same property and creates no duplicates.

- [ ] **Step 2: Run the focused test and confirm failure**

Run:

~~~bash
cd backend
pytest tests/test_declaration_approval_new_property.py -q
~~~

- [ ] **Step 3: Implement approval validation as a pure function**

Create an ApprovalPlan value object containing:

- resolved property fields;
- confirmed MEA denominator;
- ordered units keyed by extraction subject_key;
- legal references;
- assignments with resolved subject keys;
- rules and sources;
- document relations;
- approved fact sources;
- a deterministic summary.

The pure build_approval_plan function must:

1. Accept only ACCEPTED or CORRECTED candidates.
2. Use corrected_value for CORRECTED, proposed_value_json otherwise.
3. Require all mandatory property fields for NEW_PROPERTY.
4. Reject PENDING or NEEDS_CLARIFICATION critical candidates.
5. Reject every unresolved BLOCKER.
6. Require evidence for every accepted critical fact and rule.
7. Reject dangling unit, rule, document, and evidence keys.
8. Reject duplicate unit numbers after Unicode normalization and whitespace trimming.
9. Require every unit MEA denominator to equal the property denominator.
10. Reject a proposed delete operation.

Map the fixed unit fields number, location, building_part, area, heated_area,
usage_type, and mea to Unit columns. No accepted fixed-core field may be
silently dropped; an unsupported mapping is APPROVAL_SCHEMA_UNSUPPORTED and
blocks before writes.

For NEW_PROPERTY, an accepted positive mea_denominator is mandatory and the
created Property is CONFIRMED. For EXISTING_PROPERTY in
LEGACY_UNCONFIRMED, an accepted PROPERTY_FIELD mea_denominator candidate is an
explicit scale confirmation: it is always individually reviewed, the preview
states that existing Unit.mea values will be interpreted as numerators, and
approval applies the same locked validation/audit rules as the manual
confirmation endpoint before setting CONFIRMED.

Use typed Pydantic candidate payloads from plan 1 rather than inspecting arbitrary JSON paths in the write layer.

- [ ] **Step 4: Implement one transactional approval service**

DeclarationApprovalService.approve must run in this order:

1. Lock the import FOR UPDATE inside the tenant session.
2. If status APPLIED and idempotency_key matches, return
   approval_result_json; if APPLIED with a different key, return
   ALREADY_APPLIED.
3. Require status NEEDS_REVIEW and matching draft_revision.
4. Lock candidates, evidence, and unresolved issues.
5. Recompute all CandidatePolicy decisions.
6. Build and validate ApprovalPlan before canonical writes.
7. Create Property with mea_scale_status CONFIRMED.
8. Create Units and fill a subject_key to UUID map.
9. Create legal references and assignments using the map.
10. Bind every import PropertyDocument to the new property.
11. Create approved document relations.
12. Create rules, then rule relations, then property_rule_source rows.
13. Create approved_fact_source rows using canonical subject IDs, including
    approved_value_json/value_fingerprint/status validity and
    DOCUMENT_RELATION source records for accepted document relationships.
14. Store approved_by, approved_at, approval_idempotency_key,
    approval_result_json, and applied_property_id on the import.
15. Insert declaration_import.applied AuditEvent with IDs and count summary,
    never source text or corrected values.
16. Transition to APPLIED and flush.

Do not call session.commit inside the service; get_db owns the transaction. Any exception must roll back all fifteen effects.

- [ ] **Step 5: Expose POST approve**

Add POST /api/declaration-imports/{id}/approve using require_write_dep. Return:

~~~json
{
  "import_id": "uuid",
  "status": "APPLIED",
  "property_id": "uuid",
  "draft_revision": 12,
  "summary": {
    "properties_created": 1,
    "units_created": 2,
    "units_updated": 0,
    "rules_created": 1,
    "documents_bound": 2
  }
}
~~~

Map validation conflicts to 409 with stable error codes and invalid request bodies to 422.

- [ ] **Step 6: Run new-property approval tests**

Run:

~~~bash
cd backend
pytest tests/test_declaration_approval_new_property.py tests/test_declaration_review_api.py -q
~~~

Expected: PASS.

- [ ] **Step 7: Inspect the task diff**

Run:

~~~bash
git diff -- backend/app/declarations/approval.py backend/app/repositories/property_knowledge_repo.py backend/app/repositories/property_repo.py backend/app/repositories/unit_repo.py backend/app/api/declaration_imports.py backend/tests/test_declaration_approval_new_property.py
git diff --check
~~~

## Task 5: Apply an amendment to an existing property without silent deletion or MEA corruption

**Files:**

- Modify: backend/app/declarations/approval.py
- Modify: backend/app/repositories/property_repo.py
- Modify: backend/app/repositories/unit_repo.py
- Create: backend/tests/test_declaration_approval_existing_property.py

- [ ] **Step 1: Write failing existing-property tests**

Cover:

- unchanged values become KEEP and do not rewrite canonical audit fields;
- changed fields require individual review and preserve previous_value_json;
- no extraction candidate can delete an existing unit, assignment, rule, or source;
- a new unit subject key can be resolved by number only when exactly one tenant/property-local match exists;
- ambiguous unit numbers produce BLOCKER AMBIGUOUS_UNIT_MATCH;
- LEGACY_UNCONFIRMED blocks any MEA create/update until the denominator is explicitly confirmed;
- an individually accepted denominator candidate confirms a legacy property
  and records property.mea_scale_confirmed in the approval transaction;
- exact denominator conversion preserves the legal fraction;
- MEA_RESCALE locks the property and all units, changes every numerator plus the denominator atomically, and is always individual review;
- mixed or non-proportional scales produce BLOCKER MIXED_MEA_SCALE;
- a failing last rule-source insert rolls back every preceding property and unit update.
- changing an address or MEA supersedes its old approved_fact_source rows,
  inserts an EFFECTIVE value snapshot for the new value, and leaves the old
  source available only as history.

Use the Talstraße amendment shape as a synthetic fixture: split two 95,464/1,000,000 interests into four 47,732/1,000,000 interests, while changing an elevator rule from 1/6 to 1/7.

- [ ] **Step 2: Run the tests and confirm failures**

Run:

~~~bash
cd backend
pytest tests/test_declaration_approval_existing_property.py -q
~~~

- [ ] **Step 3: Build a locked existing-property snapshot**

Before planning any write:

- lock Property FOR UPDATE;
- load all Units in stable ID order FOR UPDATE;
- load active assignments and rules;
- verify target_property_id belongs to organization_id;
- require candidate previous_value_json to equal the current canonical value for every UPDATE;
- return 409 TARGET_CHANGED when canonical data changed since extraction.

The comparison must normalize Decimal and date values without floating-point conversion.

- [ ] **Step 4: Implement exact MEA handling**

Use integer arithmetic:

~~~python
from math import gcd


def convert_mea_exact(numerator: int, source_denominator: int, target_denominator: int) -> int:
    if numerator < 0 or source_denominator <= 0 or target_denominator <= 0:
        raise ApprovalConflict("INVALID_MEA")
    scaled = numerator * target_denominator
    quotient, remainder = divmod(scaled, source_denominator)
    if remainder:
        raise ApprovalConflict("MEA_NOT_EXACTLY_CONVERTIBLE")
    return quotient


def common_denominator(denominators: list[int]) -> int:
    value = 1
    for denominator in denominators:
        value = value * denominator // gcd(value, denominator)
        if value > 1_000_000_000_000:
            raise ApprovalConflict("MEA_SCALE_TOO_LARGE")
    return value
~~~

For MEA_RESCALE, require a complete mapping for every current unit. Verify that every old relative share equals its proposed new fraction by cross multiplication. Update all unit numerators and Property.mea_denominator in one locked transaction. Never derive a scale from partial candidates.

- [ ] **Step 5: Implement non-destructive amendment writes**

Existing-property approval may:

- update explicitly accepted Property or Unit fields;
- create explicitly accepted new Units, legal references, assignments, rules,
  relations, and sources;
- update explicitly accepted legal-reference or assignment fields/status while
  preserving all prior source rows;
- mark an existing rule SUPERSEDED only through an accepted RULE_RELATION that identifies both rule IDs;
- bind new PropertyDocuments.

It may not delete rows. A rejected or absent candidate means no change. A superseded rule and its prior sources remain queryable.
Every accepted UPDATE applies the approved-fact versioning rule from Task 1;
current reads use only EFFECTIVE sources whose value_fingerprint matches the
canonical field. History reads may include SUPERSEDED rows.

- [ ] **Step 6: Run existing-property and regression tests**

Run:

~~~bash
cd backend
pytest tests/test_declaration_approval_existing_property.py tests/test_declaration_approval_new_property.py tests/engine/test_invariants_weg.py tests/engine/test_golden_weg.py -q
~~~

Expected: PASS, including unchanged settlement weights.

- [ ] **Step 7: Inspect the task diff**

Run:

~~~bash
git diff -- backend/app/declarations/approval.py backend/app/repositories/property_repo.py backend/app/repositories/unit_repo.py backend/tests/test_declaration_approval_existing_property.py
git diff --check
~~~

## Task 6: Add frontend test infrastructure and typed declaration API calls

**Files:**

- Modify: frontend/package.json
- Modify: frontend/package-lock.json
- Modify: frontend/vite.config.ts
- Create: frontend/src/test/setup.ts
- Modify: frontend/src/api/client.ts
- Modify: frontend/src/api/types.ts
- Modify: frontend/src/api/endpoints.ts
- Create: frontend/src/pages/DeclarationImportPage.test.tsx

- [ ] **Step 1: Add the failing endpoint and URL-helper tests**

Install and lock development dependencies:

~~~bash
cd frontend
npm install --save-dev vitest jsdom @testing-library/react @testing-library/jest-dom @testing-library/user-event
~~~

Add scripts:

~~~json
{
  "test": "vitest run",
  "test:watch": "vitest"
}
~~~

Configure test.environment as jsdom and setupFiles as ./src/test/setup.ts in frontend/vite.config.ts. Import @testing-library/jest-dom/vitest in setup.ts.

Start frontend/src/pages/DeclarationImportPage.test.tsx with tests that mock fetch and assert:

- createImport sends mode and optional target_property_id;
- upload uses multipart FormData without an explicit Content-Type;
- propertyDocumentContentUrl returns the authorized API path and keeps the page fragment client-side;
- declarationImportDocumentContentUrl returns the draft API path and keeps the
  page fragment client-side;
- status polling stops at NEEDS_REVIEW, FAILED, CANCELLED, or APPLIED.

- [ ] **Step 2: Run tests and verify missing exports fail**

Run:

~~~bash
cd frontend
npm test -- DeclarationImportPage.test.tsx
~~~

- [ ] **Step 3: Add exact frontend DTOs**

Add string unions and interfaces for:

- DeclarationImportMode and DeclarationImportStatus;
- DeclarationDocument, DeclarationImport, DeclarationCandidate;
- CandidateEvidence and CandidateIssue;
- CandidateDecision and CandidateReviewRequest;
- ApprovalPreview and ApprovalResult;
- PropertyDocument;
- Property mea_denominator and mea_scale_status;
- Unit building_part. MEA display combines Unit.mea with the enclosing
  Property.mea_denominator; Unit has no denominator field.

Keep decimal and integer legal fractions as strings or safe integer fields according to the backend response; never coerce MEA through JavaScript floating point.

- [ ] **Step 4: Add endpoints**

Implement:

- createDeclarationImport
- uploadDeclarationDocument
- updateDeclarationDocument
- startDeclarationImport
- getDeclarationImport
- listDeclarationCandidates
- reviewDeclarationCandidate
- bulkReviewDeclarationCandidates
- getDeclarationApprovalPreview
- approveDeclarationImport
- retryDeclarationImport
- cancelDeclarationImport
- listPropertyDocuments
- propertyDocumentContentUrl
- declarationImportDocumentContentUrl

Use string concatenation for the PDF fragment:

~~~typescript
export function propertyDocumentContentUrl(
  propertyId: string,
  documentId: string,
  page?: number,
): string {
  const path = "/api/properties/" + propertyId + "/documents/" + documentId + "/content";
  return page ? path + "#page=" + page : path;
}
~~~

The fragment never goes to the server and never becomes a storage locator.

- [ ] **Step 5: Run frontend tests, typecheck, and lint**

Run:

~~~bash
cd frontend
npm test
npm run build
npm run lint
~~~

Expected: PASS.

- [ ] **Step 6: Inspect the task diff**

Run:

~~~bash
git diff -- frontend/package.json frontend/package-lock.json frontend/vite.config.ts frontend/src/test frontend/src/api
git diff --check
~~~

## Task 7: Build the new-property and amendment upload wizard

**Files:**

- Create: frontend/src/pages/DeclarationImportPage.tsx
- Modify: frontend/src/pages/DeclarationImportPage.test.tsx
- Modify: frontend/src/App.tsx
- Modify: frontend/src/pages/PropertiesPage.tsx
- Modify: frontend/src/pages/PropertyDetailPage.tsx
- Modify: frontend/src/pages/UnitDetailPage.tsx
- Modify: frontend/src/index.css

- [ ] **Step 1: Write failing wizard behavior tests**

Test:

- /teilungserklaerung/import defaults to NEW_PROPERTY;
- /objekte/:propertyId/teilungserklaerung/import locks mode to EXISTING_PROPERTY;
- multiple PDFs can be selected and removed before upload;
- non-PDF files and files above 25 MiB are rejected client-side but server limits remain authoritative;
- each successful upload can be classified DECLARATION, AMENDMENT, ANNEX, or OTHER and reordered;
- the disclosure states that required document sections are processed through the OpenAI API;
- Start is disabled until at least one PDF has uploaded and no upload is pending;
- closing/reopening via the import URL reloads durable status;
- polling backs off to five seconds and stops at a terminal/review state;
- failure codes show an actionable message and Retry only for retryable failures.

- [ ] **Step 2: Run the component test and confirm failure**

Run:

~~~bash
cd frontend
npm test -- DeclarationImportPage.test.tsx
~~~

- [ ] **Step 3: Implement a four-stage wizard**

Stages:

1. Ziel: new property or the route-bound existing property.
2. Dokumente: multi-file selection, progress, type, title, order, and removal.
3. Verarbeitung: disclosure, start, persisted status, per-stage progress, close-safe copy.
4. Ergebnis: route to review at NEEDS_REVIEW or show normalized failure/retry.

Store only the import ID in the route/query state. Do not store document bytes, extracted candidates, or auth data in localStorage.

- [ ] **Step 4: Add entry points and routes**

Add:

- /teilungserklaerung/import
- /objekte/:propertyId/teilungserklaerung/import
- /teilungserklaerung/import/:importId
- /teilungserklaerung/import/:importId/review

PropertiesPage gets Teilungserklärung importieren for a new object. PropertyDetailPage gets Nachtrag oder Teilungserklärung einlesen for the current property. Preserve existing create/edit controls.
Extend the existing unit table/form with building_part so manually created and
imported unit building sections remain visible and editable.
PropertyDetailPage and UnitDetailPage format a legal share from the loaded
Property.mea_denominator plus Unit.mea; neither treats the denominator as a
Unit field.

- [ ] **Step 5: Add accessible styles**

Use semantic fieldsets, labels, progress text, aria-live for upload/processing status, keyboard-operable reorder controls, visible focus styles, and a responsive single-column layout below 900px. Do not encode candidate risk by color alone.

- [ ] **Step 6: Run frontend verification**

Run:

~~~bash
cd frontend
npm test -- DeclarationImportPage.test.tsx
npm run build
npm run lint
~~~

Expected: PASS.

- [ ] **Step 7: Inspect the task diff**

Run:

~~~bash
git diff -- frontend/src/pages/DeclarationImportPage.tsx frontend/src/pages/DeclarationImportPage.test.tsx frontend/src/App.tsx frontend/src/pages/PropertiesPage.tsx frontend/src/pages/PropertyDetailPage.tsx frontend/src/pages/UnitDetailPage.tsx frontend/src/index.css
git diff --check
~~~

## Task 8: Build grouped review, inline source viewing, correction, and final approval

**Files:**

- Create: frontend/src/pages/DeclarationReviewPage.tsx
- Create: frontend/src/components/declarations/CandidateDecision.tsx
- Create: frontend/src/components/declarations/EvidenceViewer.tsx
- Create: frontend/src/components/declarations/ReviewGroup.tsx
- Create: frontend/src/components/declarations/ApprovalSummary.tsx
- Create: frontend/src/pages/DeclarationReviewPage.test.tsx
- Modify: frontend/src/App.tsx
- Modify: frontend/src/index.css

- [ ] **Step 1: Write failing review-flow tests**

Test:

- candidates are grouped as Stammdaten, Einheiten, Zuordnungen, Regeln, and Klärungsbedarf;
- each card shows old value, proposed value, operation, confidence, risk text, issues, and source count;
- selecting an evidence item opens the authorized content URL at the one-based PDF page;
- RULE, UPDATE, DOCUMENT_RELATION, MEA_RESCALE, low-confidence, warning, conflict, and blocker candidates never expose bulk selection;
- a safe low-risk group can be bulk accepted;
- correction submits typed JSON rather than a display string for MEA/formulas;
- a 409 STALE_DRAFT_REVISION reloads candidates and preserves an unsent correction in component state;
- final approval stays disabled while can_approve is false;
- the final dialog shows creates, updates, unchanged values, rejected proposals, unresolved issues, documents, and MEA scale effects;
- successful approval navigates to the canonical property.

- [ ] **Step 2: Run the tests and confirm failure**

Run:

~~~bash
cd frontend
npm test -- DeclarationReviewPage.test.tsx
~~~

- [ ] **Step 3: Implement source-first review components**

CandidateDecision owns local correction/comment state but receives candidate and draft_revision from the page. It must send exactly one mutation at a time and disable controls while pending.

EvidenceViewer renders:

~~~tsx
<iframe
  title={"Originalquelle: " + evidence.document_title}
  src={declarationImportDocumentContentUrl(
    importId,
    evidence.document_id,
    evidence.pdf_page,
  )}
/>
~~~

For a NEW_PROPERTY draft with unbound documents, use the write-role-only
/api/declaration-imports/{import_id}/documents/{document_id}/content endpoint
implemented in Task 3. It uses the same integrity checks as the property route
and remains inaccessible to READ_ONLY.

- [ ] **Step 4: Implement grouping and bulk safety**

Derive display groups from candidate.candidate_type, but trust
requires_individual_review from the server for eligibility. If it is true,
never include the candidate ID in a bulk request. The server remains
authoritative and may still reject the entire request.

Show issue severity in text:

- INFO: Hinweis
- WARNING: Prüfen
- BLOCKER: Freigabe gesperrt

Show rule scope and effect as readable structured sections, including formulas, responsible parties, exceptions, validity, and superseding relationships. Never flatten them into an untraceable prose edit field.

- [ ] **Step 5: Implement approval preview and apply**

Reload preview after every successful decision. Require an explicit final confirmation. Generate a browser idempotency key once per open confirmation dialog with crypto.randomUUID and reuse it on network retry. Do not regenerate it until the dialog is closed or approval succeeds.

- [ ] **Step 6: Run all frontend tests**

Run:

~~~bash
cd frontend
npm test
npm run build
npm run lint
~~~

Expected: PASS.

- [ ] **Step 7: Inspect the task diff**

Run:

~~~bash
git diff -- frontend/src/pages/DeclarationReviewPage.tsx frontend/src/components/declarations frontend/src/pages/DeclarationReviewPage.test.tsx frontend/src/App.tsx frontend/src/index.css
git diff --check
~~~

## Task 9: Verify the full review and approval slice

**Files:**

- Verify only: no planned file changes. If a check fails, return to the owning
  task above, add the failing regression first, and make the correction within
  that task's declared files.

- [ ] **Step 1: Run backend focused acceptance**

Run:

~~~bash
cd backend
pytest \
  tests/test_declaration_review_api.py \
  tests/test_property_document_content.py \
  tests/test_declaration_approval_new_property.py \
  tests/test_declaration_approval_existing_property.py \
  tests/test_declaration_policy.py \
  tests/test_declaration_pipeline.py \
  tests/test_rls_coverage.py \
  tests/test_tenant_isolation.py \
  -q
~~~

Expected: PASS.

- [ ] **Step 2: Run the complete backend regression suite**

Run:

~~~bash
cd backend
pytest -q
~~~

Expected: PASS.

- [ ] **Step 3: Run complete frontend verification**

Run:

~~~bash
cd frontend
npm test
npm run build
npm run lint
~~~

Expected: PASS.

- [ ] **Step 4: Perform browser acceptance without a live provider call**

Use the configured fake provider and synthetic PDFs:

1. Sign in as CLERK.
2. Start a NEW_PROPERTY import from Objekte.
3. Upload base plus amendment PDFs, classify, reorder, and start.
4. Wait for NEEDS_REVIEW after leaving and reopening the route.
5. Verify a safe group can be approved.
6. Verify every rule/update/MEA candidate requires individual review.
7. Open two source pages and confirm the page fragments.
8. Correct one value and verify draft revision increments.
9. Confirm the approval summary and apply.
10. Verify the new property, units, exact MEA fraction, documents, rules, and source links.
11. Start an EXISTING_PROPERTY amendment and verify before/after plus no-delete behavior.
12. Sign in as READ_ONLY and verify approved documents are visible but draft and write controls are not.

- [ ] **Step 5: Check migration downgrade/upgrade in an isolated test database**

Confirm the exported URLs point to the plan's disposable port 55432, then run:

~~~bash
cd backend
alembic downgrade 0024
alembic upgrade head
pytest tests/test_rls_coverage.py -q
~~~

Expected: downgrade/upgrade recreates the approval schema cleanly and RLS tests
pass. No persistence claim is made across a destructive downgrade.

- [ ] **Step 6: Stop the exact isolated test project**

Run even after a failed verification:

~~~bash
docker compose -p hv-declaration-tests -f infra/docker-compose.declaration-test.yml ps
docker compose -p hv-declaration-tests -f infra/docker-compose.declaration-test.yml down
~~~

Expected: only the tmpfs test project is stopped and no persistent volume is
removed.

- [ ] **Step 7: Final scope and whitespace audit**

Run:

~~~bash
git status --short
git diff --stat
git diff --check
git diff -- backend frontend
~~~

Verify:

- no secrets, raw PDF text, or original PDFs entered the repository;
- no write path bypasses get_db tenant scoping;
- draft APIs are write-role-only and approved property documents remain readable to READ_ONLY;
- every canonical field/rule created by an import has a source row;
- no amendment path performs implicit deletion;
- exact MEA arithmetic has no float conversion;
- status becomes APPLIED only within the canonical write transaction.

## Review and approval acceptance checklist

- [ ] Every new table is RLS-covered with ENABLE and FORCE.
- [ ] Candidate review is optimistic-lock safe and server policy is recalculated.
- [ ] Bulk review is all-or-nothing and cannot include individually reviewed candidates.
- [ ] PDF content is tenant/property scoped and integrity checked before streaming.
- [ ] NEW_PROPERTY creates a complete, source-backed canonical graph atomically.
- [ ] EXISTING_PROPERTY detects stale targets, never deletes implicitly, and preserves history.
- [ ] LEGACY_UNCONFIRMED and MEA_RESCALE behaviors are covered by tests.
- [ ] The UI exposes old/new values, issues, sources, correction, and explicit final approval.
- [ ] READ_ONLY can read approved knowledge documents but cannot see or mutate drafts.
- [ ] Backend and frontend regression suites pass.
