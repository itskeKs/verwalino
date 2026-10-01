"""Scoped append-only coverage and revision of financial source data."""
import hashlib
from sqlalchemy import text
from app.models.coverage_declaration import CoverageDeclaration
from app.repositories.base import TenantRepository


class CoverageDeclarationRepository(TenantRepository[CoverageDeclaration]):
    model = CoverageDeclaration

    async def latest(self, subject_id, kind):
        column = CoverageDeclaration.economic_plan_id if kind == "economic_plan" else CoverageDeclaration.settlement_run_id
        result = await self._session.execute(self._base_query().where(column == subject_id)
            .order_by(CoverageDeclaration.revision.desc()).limit(1))
        return result.scalar_one_or_none()

    async def lock_sources(self):
        # Shared with database triggers so direct writes cannot race finalization.
        await self._session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                                    {"key": f"financial-inputs:{self._org_id}"})

    async def source_revision(self, property_id):
        parts = []
        for table in ("property", "unit", "cost_entry", "payment", "bank_account"):
            column = "id" if table == "property" else "property_id"
            result = await self._session.execute(text(
                f"SELECT t::text FROM {table} t WHERE t.organization_id=:org AND t.{column}=:property ORDER BY t.id"),
                {"org": self._org_id, "property": property_id})
            parts.extend([table, *result.scalars()])
        for table in ("lease_agreement", "ownership_history"):
            result = await self._session.execute(text(
                f"SELECT t::text FROM {table} t JOIN unit u ON u.id=t.unit_id AND u.organization_id=t.organization_id "
                "WHERE t.organization_id=:org AND u.property_id=:property ORDER BY t.id"),
                {"org": self._org_id, "property": property_id})
            parts.extend([table, *result.scalars()])
        return hashlib.sha256("\n".join(parts).encode()).hexdigest()

    async def owner_ids(self, subject):
        result = await self._session.execute(text(
            "SELECT DISTINCT o.owner_profile_id FROM ownership_history o JOIN unit u "
            "ON u.id=o.unit_id AND u.organization_id=o.organization_id WHERE o.organization_id=:org "
            "AND u.property_id=:property AND o.valid_from<=:end AND (o.valid_to IS NULL OR o.valid_to>:start)"),
            {"org": self._org_id, "property": subject.property_id, "start": subject.period_start, "end": subject.period_end})
        return {str(i) for i in result.scalars()}

    async def bank_accounts(self, property_id):
        result = await self._session.execute(text("SELECT id, account_type FROM bank_account WHERE organization_id=:org AND property_id=:property"),
            {"org": self._org_id, "property": property_id})
        return {str(row.id): row.account_type for row in result}

    async def linked_plan(self, plan_id):
        result = await self._session.execute(text(
            "SELECT t::text AS revision, status, superseded_by_id, property_id, period_start, period_end "
            "FROM economic_plan t WHERE organization_id=:org AND id=:id"),
            {"org": self._org_id, "id": plan_id})
        return result.mappings().first()
