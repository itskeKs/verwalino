"""Read-only suggestions over an explicitly bounded authorized comparison domain."""
from sqlalchemy import text
from app.core.context_candidates import MAX_SCOPES,source_signals,candidate_matches
from app.services.intake_service import IntakeService


class ContextCandidateService(IntakeService):
    async def discover(self,logical,partition,extraction):
        source=await self.call('SELECT assignment_candidate_source(:org,:logical,:partition,:extraction)',logical=logical,partition=partition,extraction=extraction)
        response={key:source[key] for key in ('logical_document_id','partition_revision_id','extraction_revision_id')}
        response.update(items=[],status='manual_review',conflicting_properties=False,ambiguous_scopes=False,can_prepare=source['can_prepare'],period_basis='unreviewed_extraction',unmatched_evidence=[])
        if not source['pages']:
            return {**response,'status':'unsupported_source'}
        signals=source_signals(source['pages'])
        if signals is None:return {**response,'status':'source_limit'}
        if not signals:return {**response,'status':'no_labelled_evidence'}
        scopes=(await self.db.execute(text('''SELECT s.id,s.property_id,s.label,s.scope_type,s.whole_property,s.valid_from,s.valid_to,
          p.name AS property_name,p.street,p.zip_code,p.city FROM accounting_scope s JOIN property p ON p.organization_id=s.organization_id AND p.id=s.property_id
          WHERE s.organization_id=:org AND has_scope_capability(s.organization_id,s.id,'read') AND has_scope_capability(s.organization_id,s.id,'prepare')
          AND property_master_access(s.organization_id,s.property_id,'read')
          AND (NULLIF(current_setting('app.current_scope_id',true),'') IS NULL OR s.id=NULLIF(current_setting('app.current_scope_id',true),'')::uuid)
          ORDER BY s.id LIMIT :take'''),{'org':self.auth.organization_id,'take':MAX_SCOPES+1})).mappings().all()
        if len(scopes)>MAX_SCOPES:return {**response,'status':'comparison_limit'}
        meters=[]
        if any(signal['kind']=='meter_number' for signal in signals) and scopes:
            meters=(await self.db.execute(text('''SELECT m.property_id,m.meter_number FROM meter m WHERE m.organization_id=:org AND m.property_id=ANY(CAST(:properties AS uuid[]))
              AND property_master_access(m.organization_id,m.property_id,'read')
              AND (m.unit_id IS NULL OR EXISTS(SELECT 1 FROM unit u WHERE u.organization_id=m.organization_id AND u.id=m.unit_id AND u.property_id=m.property_id))
              ORDER BY m.id LIMIT 501'''),{'org':self.auth.organization_id,'properties':list({scope['property_id'] for scope in scopes})})).mappings().all()
            if len(meters)>500:return {**response,'status':'comparison_limit'}
        rows,conflicting=candidate_matches(scopes,meters,signals,source,source['scope_hint_id'])
        mappings=await self.call('SELECT context_mapping_candidates(:org,:logical,:partition,:extraction)',logical=logical,partition=partition,extraction=extraction)
        for row in rows:
            row['mappings']=[item for item in mappings['items'] if item['scope_id']==row['scope_id'] and not row['exclusions']]
        return {**response,'items':rows,'conflicting_properties':conflicting,'ambiguous_scopes':len(rows)>1,
                'mapping_status':mappings['status'],
                'unmatched_evidence':[signal for signal in signals if not any(signal in row['evidence'] for row in rows)],
                'status':'manual_review' if rows else 'no_matching_context'}
