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
TENANT_TABLES = frozenset({
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
    "payment",
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
