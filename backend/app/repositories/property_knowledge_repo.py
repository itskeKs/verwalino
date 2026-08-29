"""Transactional persistence primitives for an approved declaration plan."""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Mapping, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.declarations.approval import (
    ApprovalPlan,
    ExistingApprovalPlan,
    ExistingFieldUpdate,
    deterministic_id,
    value_fingerprint,
)
from app.models.approved_fact_source import ApprovedFactSource
from app.models.property import Property
from app.models.property_assignment import PropertyAssignment
from app.models.property_document import PropertyDocument
from app.models.property_document_relation import PropertyDocumentRelation
from app.models.property_legal_reference import PropertyLegalReference
from app.models.property_rule import PropertyRule, PropertyRuleRelation, PropertyRuleSource
from app.models.unit import Unit


class PropertyKnowledgeRepository:
    """Adds a fully validated plan to the caller-owned tenant transaction."""

    def __init__(self, session: AsyncSession, organization_id: uuid.UUID):
        self._session = session
        self._organization_id = organization_id

    async def apply(
        self,
        plan: ApprovalPlan,
        approved_by: uuid.UUID,
        approved_at: datetime,
        documents: Sequence[PropertyDocument],
    ) -> None:
        property_row = Property(
            id=plan.property_id,
            organization_id=self._organization_id,
            name=str(plan.property_fields["name"]),
            street=str(plan.property_fields["street"]),
            zip_code=str(plan.property_fields["zip_code"]),
            city=str(plan.property_fields["city"]),
            management_type=str(plan.property_fields["management_type"]),
            mea_denominator=plan.mea_denominator,
            mea_scale_status="CONFIRMED",
        )
        self._session.add(property_row)
        for unit in plan.units:
            self._session.add(Unit(
                id=unit.id,
                organization_id=self._organization_id,
                property_id=plan.property_id,
                number=unit.number,
                location=unit.location,
                building_part=unit.building_part,
                area=unit.area,
                heated_area=unit.heated_area,
                usage_type=unit.usage_type,
                mea=Decimal(unit.mea_numerator),
            ))
        await self._session.flush()

        for legal in plan.legal_references:
            self._session.add(PropertyLegalReference(
                id=legal.id,
                organization_id=self._organization_id,
                property_id=plan.property_id,
                reference_type=str(legal.values["reference_type"]),
                district=_optional_string(legal.values.get("district")),
                cadastral_district=_optional_string(legal.values.get("cadastral_district")),
                field_number=_optional_string(legal.values.get("field_number")),
                parcel=_optional_string(legal.values.get("parcel")),
                sheet=_optional_string(legal.values.get("sheet")),
                details_json=_details(legal.values.get("details")),
                source_import_id=plan.import_id,
            ))
        unit_ids = {unit.subject_key: unit.id for unit in plan.units}
        for assignment in plan.assignments:
            self._session.add(PropertyAssignment(
                id=assignment.id,
                organization_id=self._organization_id,
                property_id=plan.property_id,
                unit_id=unit_ids.get(assignment.unit_subject_key) if assignment.unit_subject_key else None,
                stable_key=assignment.stable_key,
                assignment_type=str(assignment.values["assignment_type"]),
                designation=str(assignment.values["designation"]),
                location=_optional_string(assignment.values.get("location")),
                description=_optional_string(assignment.values.get("description")),
                details_json=_details(assignment.values.get("details")),
                source_import_id=plan.import_id,
            ))
        await self._session.flush()

        documents_by_id = {document.id: document for document in documents}
        for document_id in plan.document_ids:
            document = documents_by_id.get(document_id)
            if document is None or document.organization_id != self._organization_id or document.property_id is not None:
                raise ValueError("document binding changed after planning")
            document.property_id = plan.property_id
        await self._session.flush()

        for relation in plan.document_relations:
            self._session.add(PropertyDocumentRelation(
                id=relation.id,
                organization_id=self._organization_id,
                property_id=plan.property_id,
                source_document_id=relation.source_document_id,
                target_document_id=relation.target_document_id,
                relation_type=relation.relation_type,
                source_import_id=plan.import_id,
            ))
        rule_ids: dict[str, uuid.UUID] = {}
        for rule in plan.rules:
            rule_ids[rule.source_stable_key] = rule.id
            self._session.add(PropertyRule(
                id=rule.id,
                organization_id=self._organization_id,
                property_id=plan.property_id,
                stable_key=rule.stable_key,
                topic=rule.topic,
                title=rule.title,
                summary=rule.summary,
                scope_json=rule.scope_json,
                effect_json=rule.effect_json,
                status=rule.status,
                source_import_id=plan.import_id,
                approved_by=approved_by,
                approved_at=approved_at,
                search_text=build_rule_search_text(
                    rule.topic, rule.title, rule.summary, rule.scope_json, rule.effect_json, rule.evidence,
                ),
            ))
        await self._session.flush()
        for relation in plan.rule_relations:
            self._session.add(PropertyRuleRelation(
                id=relation.id,
                organization_id=self._organization_id,
                property_id=plan.property_id,
                source_rule_id=rule_ids[relation.source_rule_stable_key],
                target_rule_id=rule_ids[relation.target_rule_subject_key],
                relation_type=relation.relation_type,
                source_import_id=plan.import_id,
            ))
        for rule in plan.rules:
            for index, source in enumerate(rule.evidence):
                self._session.add(PropertyRuleSource(
                    id=deterministic_id(
                        plan.organization_id,
                        plan.import_id,
                        "rule_source",
                        f"{rule.source_stable_key}:{source.document_id}:{source.pdf_page}:{source.field_path or ''}:{index}",
                    ),
                    organization_id=self._organization_id,
                    property_id=plan.property_id,
                    property_rule_id=rule.id,
                    property_document_id=source.document_id,
                    pdf_page=source.pdf_page,
                    printed_page=source.printed_page,
                    section=source.section,
                    quote=source.quote,
                    field_path=source.field_path,
                ))
        await self._session.flush()
        for rule_id in rule_ids.values():
            await self._refresh_rule_search_text(rule_id)

        subject_ids: Mapping[tuple[str, str], uuid.UUID] = {
            ("PROPERTY", "property"): plan.property_id,
            **{("UNIT", unit.subject_key): unit.id for unit in plan.units},
            **{("LEGAL_REFERENCE", legal.stable_key): legal.id for legal in plan.legal_references},
            **{("ASSIGNMENT", assignment.stable_key): assignment.id for assignment in plan.assignments},
            **{("RULE", rule.source_stable_key): rule.id for rule in plan.rules},
            **{("DOCUMENT_RELATION", relation.stable_key): relation.id for relation in plan.document_relations},
        }
        subject_labels = {
            ("PROPERTY", "property"): _property_label(property_row),
            **{("UNIT", unit.subject_key): f"Einheit {unit.number}" for unit in plan.units},
            **{("LEGAL_REFERENCE", legal.stable_key): f"Rechtsreferenz {legal.values['reference_type']}" for legal in plan.legal_references},
            **{("ASSIGNMENT", assignment.stable_key): f"Zuordnung {assignment.values['designation']}" for assignment in plan.assignments},
            **{("DOCUMENT_RELATION", relation.stable_key): "Dokumentbeziehung" for relation in plan.document_relations},
            **{("RULE", rule.source_stable_key): f"Regel {rule.title}" for rule in plan.rules},
        }
        for source in plan.fact_sources:
            subject_id = subject_ids.get((source.subject_kind, source.subject_ref))
            if subject_id is None:
                raise ValueError("approved fact source subject is not planned")
            from app.declarations.approval import value_fingerprint
            self._session.add(ApprovedFactSource(
                id=source.id,
                organization_id=self._organization_id,
                property_id=plan.property_id,
                subject_kind=source.subject_kind,
                subject_id=subject_id,
                field_key=source.field_key,
                approved_value_json=source.approved_value_json,
                value_fingerprint=value_fingerprint(source.approved_value_json),
                valid_from=approved_at,
                property_document_id=source.evidence.document_id,
                pdf_page=source.evidence.pdf_page,
                printed_page=source.evidence.printed_page,
                section=source.evidence.section,
                quote=source.evidence.quote,
                source_import_id=plan.import_id,
                search_text=build_fact_search_text(
                    source.subject_kind, subject_labels[(source.subject_kind, source.subject_ref)],
                    _field_label(source.field_key), source.display_value, source.evidence,
                ),
            ))
        await self._session.flush()

    async def apply_existing(
        self, plan: ExistingApprovalPlan, approved_by: uuid.UUID,
        approved_at: datetime, documents: Sequence[PropertyDocument],
    ) -> None:
        """Apply a locked amendment without deleting or replacing canonical rows."""
        property_row = await self._session.scalar(
            select(Property).where(Property.organization_id == self._organization_id).where(
                Property.id == plan.property_id
            ).with_for_update()
        )
        if property_row is None:
            raise ValueError("locked property disappeared")
        units = (await self._session.scalars(
            select(Unit).where(Unit.organization_id == self._organization_id).where(
                Unit.property_id == plan.property_id
            ).order_by(Unit.id).with_for_update()
        )).all()
        units_by_id = {unit.id: unit for unit in units}
        for update in plan.property_updates:
            setattr(property_row, update.field_key, update.value)
        if plan.mea_denominator is not None:
            property_row.mea_denominator = plan.mea_denominator
            if plan.mea_scale_confirmed:
                property_row.mea_scale_status = "CONFIRMED"
        for update in plan.unit_updates:
            unit = units_by_id.get(update.subject_id)
            if unit is None:
                raise ValueError("locked unit disappeared")
            setattr(unit, update.field_key, Decimal(update.value["numerator"]) if update.field_key == "mea" else update.value)
        for planned in plan.units_created:
            self._session.add(Unit(
                id=planned.id, organization_id=self._organization_id,
                property_id=plan.property_id, number=planned.number,
                location=planned.location, building_part=planned.building_part,
                area=planned.area, heated_area=planned.heated_area,
                usage_type=planned.usage_type, mea=Decimal(planned.mea_numerator),
            ))
        unit_ids = {planned.subject_key: planned.id for planned in plan.units_created}
        unit_ids.update(plan.unit_subject_ids)
        for legal in plan.legal_references:
            self._session.add(PropertyLegalReference(
                id=legal.id, organization_id=self._organization_id, property_id=plan.property_id,
                reference_type=str(legal.values["reference_type"]),
                district=_optional_string(legal.values.get("district")),
                cadastral_district=_optional_string(legal.values.get("cadastral_district")),
                field_number=_optional_string(legal.values.get("field_number")),
                parcel=_optional_string(legal.values.get("parcel")), sheet=_optional_string(legal.values.get("sheet")),
                details_json=_details(legal.values.get("details")), source_import_id=plan.import_id,
            ))
        for assignment in plan.assignments:
            self._session.add(PropertyAssignment(
                id=assignment.id, organization_id=self._organization_id, property_id=plan.property_id,
                unit_id=unit_ids.get(assignment.unit_subject_key) if assignment.unit_subject_key else None,
                stable_key=assignment.stable_key, assignment_type=str(assignment.values["assignment_type"]),
                designation=str(assignment.values["designation"]), location=_optional_string(assignment.values.get("location")),
                description=_optional_string(assignment.values.get("description")),
                details_json=_details(assignment.values.get("details")), source_import_id=plan.import_id,
            ))
        documents_by_id = {document.id: document for document in documents}
        for document_id in plan.document_ids:
            document = documents_by_id.get(document_id)
            if document is None or document.organization_id != self._organization_id or document.property_id is not None:
                raise ValueError("document binding changed after planning")
            document.property_id = plan.property_id
        await self._session.flush()
        for relation in plan.document_relations:
            self._session.add(PropertyDocumentRelation(
                id=relation.id, organization_id=self._organization_id, property_id=plan.property_id,
                source_document_id=relation.source_document_id, target_document_id=relation.target_document_id,
                relation_type=relation.relation_type, source_import_id=plan.import_id,
            ))
        rules_by_id: dict[uuid.UUID, PropertyRule] = {}
        rules_by_source: dict[str, PropertyRule] = {}
        for rule in plan.rules_created:
            row = PropertyRule(
                id=rule.id, organization_id=self._organization_id,
                property_id=plan.property_id, stable_key=rule.stable_key,
                topic=rule.topic, title=rule.title, summary=rule.summary,
                scope_json=rule.scope_json, effect_json=rule.effect_json,
                status="EFFECTIVE", source_import_id=plan.import_id,
                approved_by=approved_by, approved_at=approved_at,
                search_text=build_rule_search_text(
                    rule.topic, rule.title, rule.summary, rule.scope_json, rule.effect_json, rule.evidence,
                ),
            )
            rules_by_id[rule.id] = row
            rules_by_source[rule.source_stable_key] = row
            self._session.add(row)
        await self._session.flush()
        for rule in plan.rules_created:
            for index, evidence in enumerate(rule.evidence):
                self._session.add(PropertyRuleSource(
                    id=deterministic_id(plan.organization_id, plan.import_id, "rule_source", f"existing:{rule.source_stable_key}:{evidence.document_id}:{evidence.pdf_page}:{evidence.field_path or ''}:{index}"),
                    organization_id=self._organization_id, property_id=plan.property_id,
                    property_rule_id=rule.id, property_document_id=evidence.document_id,
                    pdf_page=evidence.pdf_page, printed_page=evidence.printed_page,
                    section=evidence.section, quote=evidence.quote, field_path=evidence.field_path,
                ))
        superseded_rule_ids: list[uuid.UUID] = []
        for new_rule_id, old_rule_id in plan.rule_supersedes:
            old_rule = await self._session.scalar(
                select(PropertyRule).where(
                    PropertyRule.organization_id == self._organization_id,
                    PropertyRule.property_id == plan.property_id,
                    PropertyRule.id == old_rule_id,
                    PropertyRule.status == "EFFECTIVE",
                ).with_for_update()
            )
            if old_rule is None or new_rule_id not in rules_by_id:
                raise ValueError("locked rule disappeared")
            self._session.add(PropertyRuleRelation(
                id=deterministic_id(plan.organization_id, plan.import_id, "rule_relation", f"existing:{new_rule_id}:SUPERSEDES:{old_rule_id}"),
                organization_id=self._organization_id, property_id=plan.property_id,
                source_rule_id=new_rule_id, target_rule_id=old_rule_id,
                relation_type="SUPERSEDES", source_import_id=plan.import_id,
            ))
            old_rule.status = "SUPERSEDED"
            old_rule.valid_to = approved_at
            superseded_rule_ids.append(old_rule.id)
        await self._session.flush()
        for rule_id in (*rules_by_id, *superseded_rule_ids):
            await self._refresh_rule_search_text(rule_id)
        for update in (*plan.property_updates, *plan.unit_updates):
            label = _property_label(property_row) if update.subject_kind == "PROPERTY" else _unit_label(units_by_id[update.subject_id])
            await self._supersede_and_insert_fact_source(plan, update, approved_at, label)
        for planned in plan.units_created:
            for field_key, value_json in planned.value_fields.items():
                for index, evidence in enumerate(planned.evidence):
                    self._session.add(ApprovedFactSource(
                        id=deterministic_id(plan.organization_id, plan.import_id, "fact_source", f"{planned.stable_key}:{field_key}:{value_fingerprint(value_json)}:{evidence.document_id}:{evidence.pdf_page}:{index}"),
                        organization_id=self._organization_id, property_id=plan.property_id,
                        subject_kind="UNIT", subject_id=planned.id, field_key=field_key,
                        approved_value_json=value_json, value_fingerprint=value_fingerprint(value_json),
                        valid_from=approved_at, property_document_id=evidence.document_id,
                        pdf_page=evidence.pdf_page, printed_page=evidence.printed_page,
                        section=evidence.section, quote=evidence.quote, source_import_id=plan.import_id,
                        search_text=build_fact_search_text(
                            "UNIT", f"Einheit {planned.number}", _field_label(field_key),
                            approved_value_display(value_json), evidence,
                        ),
                    ))
        subject_ids: dict[tuple[str, str], uuid.UUID] = {
            **{("RULE", source_key): row.id for source_key, row in rules_by_source.items()},
            **{("LEGAL_REFERENCE", legal.stable_key): legal.id for legal in plan.legal_references},
            **{("ASSIGNMENT", assignment.stable_key): assignment.id for assignment in plan.assignments},
            **{("DOCUMENT_RELATION", relation.stable_key): relation.id for relation in plan.document_relations},
        }
        subject_labels = {
            **{("RULE", source_key): f"Regel {row.title}" for source_key, row in rules_by_source.items()},
            **{("LEGAL_REFERENCE", legal.stable_key): f"Rechtsreferenz {legal.values['reference_type']}" for legal in plan.legal_references},
            **{("ASSIGNMENT", assignment.stable_key): f"Zuordnung {assignment.values['designation']}" for assignment in plan.assignments},
            **{("DOCUMENT_RELATION", relation.stable_key): "Dokumentbeziehung" for relation in plan.document_relations},
        }
        for source in plan.fact_sources:
            subject_id = subject_ids.get((source.subject_kind, source.subject_ref))
            if subject_id is None:
                raise ValueError("existing fact source subject is not planned")
            self._session.add(ApprovedFactSource(
                id=source.id, organization_id=self._organization_id,
                property_id=plan.property_id, subject_kind=source.subject_kind, subject_id=subject_id,
                field_key=source.field_key, approved_value_json=source.approved_value_json,
                value_fingerprint=value_fingerprint(source.approved_value_json),
                valid_from=approved_at, property_document_id=source.evidence.document_id,
                pdf_page=source.evidence.pdf_page, printed_page=source.evidence.printed_page,
                section=source.evidence.section, quote=source.evidence.quote,
                source_import_id=plan.import_id,
                search_text=build_fact_search_text(
                    source.subject_kind, subject_labels[(source.subject_kind, source.subject_ref)],
                    _field_label(source.field_key), source.display_value, source.evidence,
                ),
            ))
        await self._session.flush()

    async def _supersede_and_insert_fact_source(
        self, plan: ExistingApprovalPlan, update: ExistingFieldUpdate, approved_at: datetime, subject_label: str
    ) -> None:
        current = (await self._session.scalars(
            select(ApprovedFactSource).where(
                ApprovedFactSource.organization_id == self._organization_id,
                ApprovedFactSource.property_id == plan.property_id,
                ApprovedFactSource.subject_kind == update.subject_kind,
                ApprovedFactSource.subject_id == update.subject_id,
                ApprovedFactSource.field_key == update.field_key,
                ApprovedFactSource.value_fingerprint == value_fingerprint(update.previous_value_json),
                ApprovedFactSource.status == "EFFECTIVE",
            ).with_for_update()
        )).all()
        for source in current:
            source.status = "SUPERSEDED"
            source.valid_to = approved_at
        fingerprint = value_fingerprint(update.value_json)
        for index, evidence in enumerate(update.evidence):
            self._session.add(ApprovedFactSource(
                id=deterministic_id(plan.organization_id, plan.import_id, "fact_source", f"existing:{update.subject_kind}:{update.subject_id}:{update.field_key}:{fingerprint}:{evidence.document_id}:{evidence.pdf_page}:{index}"),
                organization_id=self._organization_id, property_id=plan.property_id,
                subject_kind=update.subject_kind, subject_id=update.subject_id,
                field_key=update.field_key, approved_value_json=update.value_json,
                value_fingerprint=fingerprint, valid_from=approved_at,
                property_document_id=evidence.document_id, pdf_page=evidence.pdf_page,
                printed_page=evidence.printed_page, section=evidence.section,
                quote=evidence.quote, source_import_id=plan.import_id,
                search_text=build_fact_search_text(
                    update.subject_kind, subject_label, _field_label(update.field_key),
                    approved_value_display(update.value_json), evidence,
                ),
            ))

    async def _refresh_rule_search_text(self, rule_id: uuid.UUID) -> None:
        """Refresh a mutable projection after a rule's sources or relations change."""
        rule = await self._session.scalar(
            select(PropertyRule).where(
                PropertyRule.organization_id == self._organization_id,
                PropertyRule.id == rule_id,
            )
        )
        if rule is None:
            raise ValueError("rule disappeared while refreshing its search projection")
        sources = (await self._session.scalars(
            select(PropertyRuleSource).where(
                PropertyRuleSource.organization_id == self._organization_id,
                PropertyRuleSource.property_rule_id == rule_id,
            ).order_by(PropertyRuleSource.property_document_id, PropertyRuleSource.pdf_page, PropertyRuleSource.id)
        )).all()
        rule.search_text = build_rule_search_text(
            rule.topic, rule.title, rule.summary, rule.scope_json, rule.effect_json, sources,
        )


def _optional_string(value: object | None) -> str | None:
    return value if isinstance(value, str) else None


def _details(value: object | None) -> dict:
    return {"value": value} if value is not None else {}


_FIELD_LABELS = {
    "name": "Objektname", "street": "Straße", "zip_code": "Postleitzahl", "city": "Ort",
    "management_type": "Verwaltungsart", "mea_denominator": "MEA Nenner", "number": "Einheitsnummer",
    "location": "Lage", "building_part": "Gebäudeteil", "area": "Fläche", "heated_area": "Beheizte Fläche",
    "usage_type": "Nutzungsart", "mea": "Miteigentumsanteil", "reference_type": "Referenztyp",
    "district": "Amtsgericht", "cadastral_district": "Gemarkung", "field_number": "Flur",
    "parcel": "Flurstück", "sheet": "Blatt", "details": "Details", "assignment_type": "Zuordnungsart",
    "designation": "Bezeichnung", "description": "Beschreibung", "unit_subject_key": "Einheit",
    "relation": "Dokumentbeziehung", "rule": "Regel",
}


def build_fact_search_text(
    subject_kind: str, subject_label: str, field_label: str, display_value: str, source: object,
) -> str:
    """Build a stable, content-only projection for one approved fact source."""
    return _collapse((
        subject_kind.replace("_", " "), subject_label, field_label, display_value,
        _source_value(source, "section"), _truncate_quote(_source_value(source, "quote")),
    ))


def build_rule_search_text(
    topic: str, title: str, summary: str, scope: Mapping[str, Any], effect: Mapping[str, Any], sources: Sequence[object],
) -> str:
    """Build the deterministic searchable rule projection without review metadata."""
    return _collapse((
        topic, title, summary, _structured_projection(scope), _structured_projection(effect),
        *(_source_value(source, "section") for source in sources),
        *(_truncate_quote(_source_value(source, "quote")) for source in sources),
    ))[:100_000]


def approved_value_display(value: Mapping[str, Any]) -> str:
    """Render an approved snapshot exactly enough for German lexical search."""
    kind = value.get("kind")
    if kind in {"MEA", "FRACTION"}:
        return f"{_legal_integer_display(value.get('numerator'))}/{_legal_integer_display(value.get('denominator'))}"
    if kind == "DECIMAL":
        return _collapse((str(value.get("value", "")), str(value.get("unit") or "")))
    if kind == "PERCENTAGE":
        return f"{value.get('value')} Prozent"
    if kind == "BOOLEAN":
        return "Ja" if value.get("value") else "Nein"
    if kind in {"TEXT", "INTEGER", "DATE"}:
        return str(value.get("value", ""))
    return _structured_projection(value)


def _field_label(field_key: str) -> str:
    return _FIELD_LABELS.get(field_key, field_key.replace("_", " "))


def _legal_integer_display(value: object) -> str:
    return f"{int(value):,}".replace(",", ".")


def _property_label(property_row: Property) -> str:
    return _collapse((property_row.name, property_row.street, property_row.zip_code, property_row.city))


def _unit_label(unit: Unit) -> str:
    return f"Einheit {unit.number}"


def _source_value(source: object, key: str) -> str:
    value = source.get(key) if isinstance(source, Mapping) else getattr(source, key, None)
    return value if isinstance(value, str) else ""


def _truncate_quote(value: str) -> str:
    return _collapse((value,))[:500]


def _structured_projection(value: object) -> str:
    if isinstance(value, Mapping):
        return " ".join(
            f"{key.replace('_', ' ')} {_structured_projection(value[key])}"
            for key in sorted(value)
        )
    if isinstance(value, (list, tuple, set, frozenset)):
        return " ".join(sorted(_structured_projection(item) for item in value))
    return str(value) if value is not None else ""


def _collapse(parts: Sequence[str]) -> str:
    return re.sub(r"\s+", " ", " ".join(part for part in parts if part)).strip()
