"""RLS coverage test -- architecture.md section 7.3.

Ensures that every tenant table has RLS enabled + FORCE'd + at least one policy,
and every table in the DB schema is accounted for (either TENANT_TABLES or GLOBAL_TABLES).

An unknown table (e.g. a new migration that forgot RLS) will fail this test,
forcing a conscious decision about its tenant status.
"""

import pytest
from sqlalchemy import text


# Central table registry -- kept in sync with migrations.
# Every tenant-behaftet table must be listed here.
PRIVATE_POLICIES = {
    **{table:"intake_private" for table in ("intake_batch","intake_item","intake_upload_admission","workflow_job","job_attempt","outbox_event","intake_retry_receipt")},
    **{table:"outbox_private" for table in ("outbox_delivery","outbox_delivery_attempt")},
    **{table:"extraction_private" for table in ("intake_extraction_job","intake_extraction_attempt","intake_extraction_retry")},
    "access_grant_proposal": "coverage_private", "access_grant_acceptance": "coverage_private",
    "access_coverage_snapshot": "coverage_private", "access_coverage_review": "coverage_private",
    "invoice_upload_admission": "invoice_admission_private", "dunning_quarantine_disposition": "dunning_disposition_private",
    "mandate_quarantine_disposition": "mandate_disposition_private", "mandate_legacy_observation": "mandate_observation_private",
    "declaration_worker_context": "worker_context_private", "declaration_review_context": "review_context_private",
    "declaration_review_receipt": "review_receipt_private", "runtime_budget_attestation": "runtime_budget_private",
    "runtime_budget_review": "runtime_budget_private", "access_disposition_acceptance": "disposition_private",
    "source_security_authorization": "authorization_private", "source_security_transition": "transition_private",
    "mapping_version": "mapping_private",
    "allocation_integrity_attestation": "integrity_private",
    "allocation_correction": "correction_private", "allocation_correction_impact": "correction_private",
    "allocation_correction_context": "followup_private", "allocation_correction_completion": "followup_private",
    "allocation_source_member": "allocation_source_private", "allocation_source_claim": "allocation_source_private",
    "visual_extract_artifact": "visual_extract_private", "visual_extract_attestation": "visual_extract_private",
    "visual_disclosure_event": "visual_disclosure_private", "visual_disclosure_attestation": "visual_disclosure_private",
    "financial_computation_receipt": "computation_private", "financial_computation_attestation": "computation_private",
}

TENANT_TABLES = frozenset({
    "source_partition_revision", "source_partition_member", "assignment_proposal",
    "exception_case", "exception_dependency", "exception_case_event", "invoice_review_revision",
    "mapping_version", "context_decision", "duplicate_comparison", "duplicate_exception_dependency",
    "allocation_proposal", "allocation_proposal_line", "allocation_treatment_review", "allocation_treatment_line",
    "allocation_set", "document_cost_link", "allocation_integrity_attestation",
    "allocation_correction", "allocation_correction_impact", "allocation_correction_context", "allocation_correction_completion",
    "allocation_source_member", "allocation_source_claim", "exception_completion", "exception_completion_member",
    "visual_extract_artifact", "visual_extract_attestation", "visual_disclosure_event", "visual_disclosure_attestation",
    "financial_computation_receipt", "financial_computation_attestation",
    "intake_extraction_job", "intake_extraction_attempt", "intake_extraction_retry",
    "document_ref", "logical_document", "extraction_revision", "field_evidence", "intake_batch", "intake_item", "intake_upload_admission", "intake_original", "workflow_job", "job_attempt", "outbox_event", "intake_retry_receipt", "outbox_delivery", "outbox_delivery_attempt",
    "runtime_budget_attestation", "runtime_budget_review", "access_disposition_acceptance",
    "source_security_authorization", "source_security_transition",
    "access_grant_proposal", "access_grant_acceptance", "access_coverage_snapshot", "access_coverage_review", "invoice_upload_admission", "dunning_quarantine_disposition", "mandate_quarantine_disposition", "mandate_legacy_observation", "declaration_review_receipt", "declaration_review_context", "declaration_worker_context", "person_data_domain", "person_data_grant",
    "property_creation_receipt", "property_source_classification", "knowledge_budget_policy", "property_source_security", "property_data_domain", "property_data_grant", "property_source_admission", "property_source_admission_review",
    # B1 accounting scope foundation
    "accounting_scope", "accounting_scope_unit", "accounting_scope_binding",
    "scope_grant", "scope_migration_exception", "scope_control_event",
    "scope_payment_reconciliation", "scope_payment_reconciliation_item",
    # M0
    "user", "membership", "management_mandate", "audit_event",
    # M1 Stammdaten
    "property", "unit", "person", "owner_profile", "tenant_profile",
    "vendor", "ownership_history", "lease_agreement", "bank_account",
    "meter", "meter_reading",
    # M2 Kosten & GoBD-Belegfluss
    "cost_entry", "invoice_document", "operation_receipt", "invoice_processing_attempt",
    # M3 Verteilungs-/Abrechnungs-Engine
    "settlement_run", "settlement_line", "coverage_declaration",
    # M4 WEG-Abrechnung (Wirtschaftsplan eigene Entitaet)
    "economic_plan", "economic_plan_line",
    # M6 Zahlungen, Mietkonto & offene Posten
    "payment", "bank_transaction",
    # M7 Mahnwesen
    "dunning_notice", "dunning_scope_context",
    # M8 Eigentümerversammlung & Beschluss-Sammlung
    "owners_meeting",
    "agenda_item",
    "resolution",
    "resolution_register_entry",
    # Mandate documents (Verträge & Unterlagen am Mandat)
    "mandate_document",
    # Declaration import legal source and review graph
    "property_document",
    "declaration_import", "declaration_queue_request",
    "declaration_import_document",
    "declaration_extraction_run",
    "declaration_import_pass",
    "declaration_candidate",
    "declaration_candidate_evidence",
    "declaration_candidate_issue",
    # Canonical approved declaration facts and provenance
    "property_legal_reference",
    "property_assignment",
    "property_document_relation",
    "property_rule",
    "property_rule_relation",
    "property_rule_source",
    "approved_fact_source",
    "property_knowledge_query_run",
})

# Global tables that intentionally have no RLS.
GLOBAL_TABLES = frozenset({"organization", "cost_category"})

# Alembic's internal table (not a domain table).
INFRASTRUCTURE_TABLES = frozenset({"alembic_version"})

APPROVAL_TABLES = frozenset({
    "property_legal_reference", "property_assignment", "property_document_relation",
    "property_rule", "property_rule_relation", "property_rule_source",
    "approved_fact_source",
})


class TestRLSCoverage:
    @pytest.mark.asyncio
    async def test_knowledge_query_run_has_no_delete_policy_or_privilege(self, migrator_session):
        policies = await migrator_session.execute(
            text("SELECT cmd FROM pg_policies WHERE tablename = 'property_knowledge_query_run'")
        )
        assert {row.cmd for row in policies} == {"SELECT", "INSERT", "UPDATE"}
        privileges = await migrator_session.execute(
            text(
                "SELECT privilege_type FROM information_schema.table_privileges "
                "WHERE table_name = 'property_knowledge_query_run' AND grantee = 'app_rw'"
            )
        )
        assert {row.privilege_type for row in privileges} == {"SELECT", "INSERT", "UPDATE"}

    @pytest.mark.asyncio
    async def test_approval_tables_have_exact_tenant_policy_commands(self, migrator_session):
        for table in APPROVAL_TABLES:
            result = await migrator_session.execute(
                text("SELECT cmd FROM pg_policies WHERE tablename = :name"),
                {"name": table},
            )
            assert {row.cmd for row in result} == {"SELECT", "INSERT", "UPDATE", "DELETE"}

    @pytest.mark.asyncio
    async def test_every_tenant_table_has_rls_enabled(self, migrator_session):
        """Check pg_class.relrowsecurity for each tenant table."""
        for table in TENANT_TABLES:
            result = await migrator_session.execute(
                text(
                    "SELECT relrowsecurity, relforcerowsecurity "
                    "FROM pg_class WHERE relname = :name"
                ),
                {"name": table},
            )
            row = result.first()
            assert row is not None, f"Table '{table}' not found in pg_class"
            assert row.relrowsecurity is True, f"RLS not ENABLED on '{table}'"
            assert row.relforcerowsecurity is True, f"RLS not FORCE'd on '{table}'"

    @pytest.mark.asyncio
    async def test_every_tenant_table_has_policies(self, migrator_session):
        """Check pg_policies for each tenant table -- at least SELECT + INSERT policies."""
        for table in TENANT_TABLES:
            result = await migrator_session.execute(
                text("SELECT policyname, cmd FROM pg_policies WHERE tablename = :name"),
                {"name": table},
            )
            policies = result.fetchall()
            policy_names = {p.policyname for p in policies}
            policy_cmds = {p.cmd for p in policies}

            if table in PRIVATE_POLICIES:
                assert policy_names == {PRIVATE_POLICIES[table]}
                assert policy_cmds == {"ALL"}
                assert (await migrator_session.execute(text(
                    "SELECT qual,with_check FROM pg_policies WHERE tablename=:name"
                ), {"name": table})).one() == ("false", "false")
                continue

            assert len(policies) >= 2, (
                f"Table '{table}' has fewer than 2 policies: {policy_names}"
            )
            # Must have at least SELECT and INSERT policies
            assert "SELECT" in policy_cmds or "*" in policy_cmds, (
                f"Table '{table}' missing SELECT policy. Commands: {policy_cmds}"
            )

    @pytest.mark.asyncio
    async def test_no_unknown_tables(self, migrator_session):
        """Every table in the public schema must be in TENANT_TABLES, GLOBAL_TABLES, or INFRASTRUCTURE."""
        result = await migrator_session.execute(
            text(
                "SELECT tablename FROM pg_tables "
                "WHERE schemaname = 'public' ORDER BY tablename"
            )
        )
        all_tables = {row.tablename for row in result.fetchall()}
        known = TENANT_TABLES | GLOBAL_TABLES | INFRASTRUCTURE_TABLES
        unknown = all_tables - known

        assert unknown == set(), (
            f"Unknown tables found in DB that are not in TENANT_TABLES or GLOBAL_TABLES: {unknown}. "
            "Add them to the appropriate set to ensure conscious RLS decisions."
        )

    @pytest.mark.asyncio
    async def test_global_tables_have_no_rls(self, migrator_session):
        """Global tables should NOT have RLS enabled."""
        for table in GLOBAL_TABLES:
            result = await migrator_session.execute(
                text(
                    "SELECT relrowsecurity FROM pg_class WHERE relname = :name"
                ),
                {"name": table},
            )
            row = result.first()
            assert row is not None, f"Table '{table}' not found"
            assert row.relrowsecurity is False, (
                f"Global table '{table}' has RLS enabled -- this is unexpected"
            )

    @pytest.mark.asyncio
    async def test_audit_event_no_update_delete_for_app_rw(self, migrator_session):
        """audit_event should only allow INSERT for app_rw (append-only)."""
        result = await migrator_session.execute(
            text(
                "SELECT privilege_type FROM information_schema.table_privileges "
                "WHERE table_name = 'audit_event' AND grantee = 'app_rw'"
            )
        )
        privileges = {row.privilege_type for row in result.fetchall()}
        assert "UPDATE" not in privileges, "app_rw should NOT have UPDATE on audit_event"
        assert "DELETE" not in privileges, "app_rw should NOT have DELETE on audit_event"
        assert "SELECT" in privileges, "app_rw should have SELECT on audit_event"
        assert "INSERT" in privileges, "app_rw should have INSERT on audit_event"
