"""Build the canonical snapshot payload (dict) from input + result (section 1.7).

Pure: produces a plain dict with money/quotas as fixed-scale STRINGS, dates as
ISO strings, ids as strings, lists sorted by id -- ready for canonical_json /
sha256.  The eingefrorene inputs are COPIED (not referenced) so the snapshot is
independent of later Stammdatenaenderungen (section 1.2).

Scales (mirror the DB columns): money NUMERIC(14,2) -> 2 dp; quota/area/mea
NUMERIC(12,6) -> 6 dp.  Day counts and integers stay integers (small,
exact).  This keeps the canonical bytes -- and thus the hash -- stable.
"""

from __future__ import annotations

from decimal import Decimal

from app.engine.types import EngineResult, SettlementInput

MONEY = Decimal("0.01")
QUOTA = Decimal("0.000001")


def _m(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return str(value.quantize(MONEY))


def _q(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return str(value.quantize(QUOTA))


def build_snapshot_payload(inp: SettlementInput, result: EngineResult) -> dict:
    """Assemble the canonical snapshot payload dict (section 1.7)."""
    period_days = (inp.period_end - inp.period_start).days + 1

    target = next(
        l for l in inp.lease_periods if l.lease_agreement_id == inp.target_lease_agreement_id
    )

    units = sorted(inp.units, key=lambda u: str(u.id))
    leases = sorted(inp.lease_periods, key=lambda l: str(l.lease_agreement_id))
    owners = sorted(
        inp.owner_periods, key=lambda o: (str(o.unit_id), o.valid_from.isoformat())
    )
    positions = sorted(
        inp.cost_positions, key=lambda p: (p.category_code, str(p.cost_entry_id))
    )

    payload = {
        "engine_version": inp.engine_version,
        "run_type": "BK_MIETER",
        "period": {
            "start": inp.period_start.isoformat(),
            "end": inp.period_end.isoformat(),
            "days": period_days,
        },
        "property": {
            "id": str(inp.property.id),
            "name": inp.property.name,
            "management_type": inp.property.management_type,
        },
        "addressee": {
            "lease_agreement_id": str(target.lease_agreement_id),
            "unit_id": str(target.unit_id),
            "tenant_profile_id": str(target.tenant_profile_id),
            "tenant_name": target.tenant_name,
            "valid_from": target.valid_from.isoformat(),
            "valid_to": target.valid_to.isoformat() if target.valid_to else None,
        },
        "inputs": {
            "units": [
                {
                    "id": str(u.id),
                    "number": u.number,
                    "usage_type": u.usage_type,
                    "area": _q(u.area),
                    "heated_area": _q(u.heated_area),
                    "mea": _q(u.mea),
                    "person_count": u.person_count,
                    "consumption": _q(u.consumption),
                }
                for u in units
            ],
            "lease_periods": [
                {
                    "lease_agreement_id": str(l.lease_agreement_id),
                    "unit_id": str(l.unit_id),
                    "tenant_profile_id": str(l.tenant_profile_id),
                    "from": l.valid_from.isoformat(),
                    "to": l.valid_to.isoformat() if l.valid_to else None,
                    "advance_payment": _m(l.advance_payment),
                }
                for l in leases
            ],
            "owner_periods": [
                {
                    "unit_id": str(o.unit_id),
                    "owner_profile_id": str(o.owner_profile_id),
                    "from": o.valid_from.isoformat(),
                    "to": o.valid_to.isoformat() if o.valid_to else None,
                    "mea_share": _q(o.mea_share),
                }
                for o in owners
            ],
            "cost_positions": [
                {
                    "cost_entry_id": str(p.cost_entry_id),
                    "category_code": p.category_code,
                    "gross_amount": _m(p.gross_amount),
                    "service_period": {
                        "from": p.service_period_from.isoformat(),
                        "to": p.service_period_to.isoformat(),
                    },
                    "tenant_distribution_key": p.tenant_distribution_key,
                    "tenant_apportionable": p.tenant_apportionable,
                    "apportionable_tenant_amount": _m(p.apportionable_tenant_amount),
                    "external_amounts": [
                        {
                            "unit_id": str(e.unit_id),
                            "tenant_amount": _m(e.tenant_amount),
                            "from": e.period_from.isoformat() if e.period_from else None,
                            "to": e.period_to.isoformat() if e.period_to else None,
                        }
                        for e in sorted(
                            p.external_amounts,
                            key=lambda e: (
                                str(e.unit_id),
                                e.period_from.isoformat() if e.period_from else "",
                            ),
                        )
                    ],
                    "manual_amounts": [
                        {"unit_id": str(m.unit_id), "amount": _m(m.amount)}
                        for m in sorted(p.manual_amounts, key=lambda m: str(m.unit_id))
                    ],
                }
                for p in positions
            ],
            "advance_payments_total": _m(inp.advance_payments_total),
            "advance_payments_source": inp.advance_payments_source,
        },
        "computation": [
            {
                "cost_entry_id": str(c.cost_entry_id),
                "category_code": c.category_code,
                "distribution_key": c.distribution_key,
                "total_amount": _m(c.total_amount),
                "key_base_total": _q(c.key_base_total),
                "key_base_share": _q(c.key_base_share),
                "time_share_days": c.time_share_days,
                "time_share_total_days": c.time_share_total_days,
                "rounded_amount": _m(c.rounded_amount),
                "note": c.note,
            }
            for c in result.computation
        ],
        "vacancy_attribution": [
            {
                "cost_entry_id": str(v.cost_entry_id),
                "unit_id": str(v.unit_id),
                "owner_profile_id": str(v.owner_profile_id) if v.owner_profile_id else None,
                "days": v.days,
                "amount": _m(v.amount),
            }
            for v in sorted(
                result.vacancy_attribution,
                key=lambda v: (str(v.cost_entry_id), str(v.unit_id)),
            )
        ],
        "result": {
            "lines": [
                {
                    "line_type": ln.line_type,
                    "label": ln.label,
                    "amount": _m(ln.amount),
                    "sort_order": ln.sort_order,
                    "cost_entry_id": str(ln.cost_entry_id) if ln.cost_entry_id else None,
                    "category_code": ln.category_code,
                    "distribution_key": ln.distribution_key,
                    "total_amount": _m(ln.total_amount),
                    "distribution_base_total": _q(ln.distribution_base_total),
                    "distribution_base_share": _q(ln.distribution_base_share),
                    "time_share_days": ln.time_share_days,
                    "time_share_total_days": ln.time_share_total_days,
                    "note": ln.note,
                }
                for ln in result.lines
            ],
            "total_cost": _m(result.total_cost),
            "advance_payments_total": _m(result.advance_payments_total),
            "balance": _m(result.balance),
        },
    }
    return payload
