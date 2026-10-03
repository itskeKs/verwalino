"""Explicit financial input attestation, independently of computation/release."""
import uuid
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from app.core.tenant import AuthContext, get_db, require_write_dep
from app.repositories.settlement_repo import SettlementRunRepository
from app.repositories.economic_plan_repo import EconomicPlanRepository
from app.services.financial_input_policy import FinancialInputPolicy, normalize_values, rental_values

router = APIRouter(prefix="/api/financial-input-coverage", tags=["financial-input-coverage"])


class Attestation(BaseModel):
    subject_kind: Literal["settlement", "economic_plan"]
    subject_id: uuid.UUID
    values: dict
    confirmed_complete: Literal[True]
    reason: str = Field(min_length=1, max_length=2000)
    source_document_ids: list[uuid.UUID] = Field(default_factory=list, max_length=100)


@router.get('/source-options')
async def source_options(subject_kind:Literal['settlement','economic_plan'],subject_id:uuid.UUID,after:uuid.UUID|None=None,
                         auth:AuthContext=Depends(require_write_dep),db:AsyncSession=Depends(get_db)):
    from app.services.financial_scope_service import FinancialScopeService
    from app.core.rbac import ScopeCapability
    repo=EconomicPlanRepository(db,auth.organization_id) if subject_kind=='economic_plan' else SettlementRunRepository(db,auth.organization_id)
    subject=await repo.get_by_id(subject_id)
    if subject is None:raise HTTPException(404,'Financial subject not found')
    await FinancialScopeService(db,auth.organization_id,auth.user_id).enter(subject,capability=ScopeCapability.CONFIRM,computation=True)
    rows=(await db.execute(text('''SELECT d.id,d.original_filename,d.version,c.document_number FROM invoice_document d
      JOIN cost_entry c ON c.organization_id=d.organization_id AND c.id=d.cost_entry_id
      WHERE d.organization_id=:org AND c.property_id=:property AND c.superseded_by_id IS NULL
        AND d.status='active' AND d.released_at IS NOT NULL AND (CAST(:after AS uuid) IS NULL OR d.id>CAST(:after AS uuid))
      ORDER BY d.id LIMIT 26'''),{'org':auth.organization_id,'property':subject.property_id,'after':after})).mappings().all()
    return {'items':[dict(row) for row in rows[:25]],'next_cursor':str(rows[24]['id']) if len(rows)>25 else None}


@router.post("", status_code=201)
async def attest(body: Attestation, auth: AuthContext = Depends(require_write_dep), db: AsyncSession = Depends(get_db)):
    from app.services.financial_scope_service import FinancialScopeService
    await FinancialScopeService(db,auth.organization_id,auth.user_id).lock_inputs()
    repo = EconomicPlanRepository(db, auth.organization_id) if body.subject_kind == "economic_plan" else SettlementRunRepository(db, auth.organization_id)
    subject = await repo.get_by_id_for_update(body.subject_id)
    if subject is None:
        raise HTTPException(404, "Financial subject not found")
    # Reuse the same typed adapters as compute/finalize; omitted fields stay unknown.
    try:
        if body.subject_kind == "economic_plan":
            from app.api.economic_plans import PlanComputeRequest, _to_inputs
            values = _to_inputs(PlanComputeRequest.model_validate(body.values))
        elif subject.run_type == "BK_MIETER":
            values = rental_values(subject)
            if normalize_values(body.values) != normalize_values(values):
                raise HTTPException(409, "Advance payment input changed; reload before attesting")
        elif subject.run_type == "WEG_JAHRESABRECHNUNG":
            from app.api.weg_settlements import WegComputeRequest, _weg_extras
            values = _weg_extras(WegComputeRequest.model_validate(body.values))
        elif subject.run_type == "VERMOEGENSBERICHT":
            from app.api.weg_settlements import AssetReportComputeRequest, _report_inputs
            values = _report_inputs(AssetReportComputeRequest.model_validate(body.values))
        else:
            raise HTTPException(422, "Unsupported coverage subject")
    except (ValidationError, ValueError):
        raise HTTPException(422, "All financial inputs must be explicit before coverage can be attested")
    declaration = await FinancialInputPolicy(db, auth.organization_id, auth.user_id).attest(
        subject, body.subject_kind, values, body.reason, [str(i) for i in body.source_document_ids])
    return {"id": str(declaration.id), "revision": declaration.revision, "state": declaration.state,
            "values": declaration.values, "source_revision": declaration.source_revision}
