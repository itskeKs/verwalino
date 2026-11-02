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

from app.engine.types import (
    AssetReportResult,
    EngineResult,
    PlanResult,
    SettlementInput,
    VermoegensberichtInput,
    WegResult,
    WegSettlementInput,
    WirtschaftsplanInput,
)

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
            **({"fiscal_year_start_month": inp.property.fiscal_year_start_month}
               if inp.property.fiscal_year_start_month != 1 else {}),
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
                    **({"consumption_periods": [
                        {"from": a.isoformat(), "to": b.isoformat(), "consumption": str(q)}
                        for a, b, q in u.consumption_periods]} if u.consumption_periods else {}),
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
                    "heating_advance_payment": _m(l.heating_advance_payment),
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
            "heating_advance_payments_total": _m(inp.heating_advance_payments_total),
            "heating_advance_payments_source": inp.heating_advance_payments_source,
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
            "heating_advance_payments_total": _m(result.heating_advance_payments_total),
            "heating_cost_total": _m(result.heating_cost_total),
            "balance": _m(result.balance),
        },
    }
    if inp.uncredited_advance_payments_total:
        payload["inputs"]["uncredited_advance_payments_total"] = _m(inp.uncredited_advance_payments_total)
    if result.period_inputs:
        payload['inputs']['property_periods'] = [build_snapshot_payload(part, part_result)
            for part, part_result in zip(result.period_inputs, result.period_results, strict=True)]
    if result.heating_costs:
        payload['heating_costs'] = [_heating_payload(h) for h in result.heating_costs]
    return payload


def _heating_payload(allocation):
    pool = allocation.pool
    slices = {s.id:s for s in allocation.slices}
    rows = []
    selected = {identity: (a, b, amount) for identity, a, b, amount in allocation.statement_amounts}
    for row in pool.rows:
        s = slices[row.unit_id]
        tenant = s.lease_agreement_id is not None
        statement = selected.get(s.id)
        selection = ({'statement_from':statement[0].isoformat(), 'statement_to':statement[1].isoformat(),
                      'statement_amount':_m(statement[2])} if statement is not None and
                     (statement[0], statement[1]) != (s.period_from, s.period_to) else {})
        if allocation.statement_amounts and statement is None:
            selection = {'statement_amount':'0.00', 'statement_from':None, 'statement_to':None}
        rows.append({
            **selection,
            'slice_id':str(s.id),'unit_id':str(s.unit_id),
            'lease_agreement_id':str(s.lease_agreement_id) if tenant else None,
            'owner_profile_id':str(s.owner_profile_id) if s.owner_profile_id else None,
            'period_from':s.period_from.isoformat(),'period_to':s.period_to.isoformat(),
            'base_quantity':_q(s.base_quantity),'weighted_base_quantity':_q(row.base_quantity),
            'consumption':_q(row.consumption),'base_amount':_m(row.base_amount),
            'consumption_amount':_m(row.consumption_amount),'gross_amount':_m(row.gross_amount),
            'reduction_amount':_m(row.reduction_amount) if tenant else '0.00',
            'amount_due':_m(row.amount_due if tenant else row.gross_amount),
            'reduction_reason':row.reduction_reason if tenant else None,
        })
    return {
        'cost_entry_id':str(allocation.cost_entry_id),'kind':pool.kind,'base_key':pool.base_key,
        'consumption_fraction':str(pool.consumption_fraction),'method':pool.method,
        'total_amount':_m(pool.total_amount),'base_cost_total':_m(pool.base_cost_total),
        'consumption_cost_total':_m(pool.consumption_cost_total),
        'base_total_quantity':_q(sum((r.base_quantity for r in pool.rows),Decimal(0))),
        'consumption_total_quantity':_q(sum((r.consumption for r in pool.rows),Decimal(0))) if pool.consumption_recorded else None,
        'rows':rows,
    }


# ===========================================================================
# M4 (WEG) snapshot payloads (m4 section 5.4 / 1.5 / 6.5). Same canonical rules:
# money/quotas as fixed-scale STRINGS, dates ISO, ids strings, lists sorted by
# id -> stable bytes -> stable hash. The eingefrorene inputs are COPIED so the
# snapshot is independent of later Stammdatenaenderungen (m4 section 1.5).
# ===========================================================================


def _units_payload(units) -> list[dict]:
    return [
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
        for u in sorted(units, key=lambda u: str(u.id))
    ]


def _owner_periods_payload(owner_periods) -> list[dict]:
    return [
        {
            "unit_id": str(o.unit_id),
            "owner_profile_id": str(o.owner_profile_id),
            "from": o.valid_from.isoformat(),
            "to": o.valid_to.isoformat() if o.valid_to else None,
            "mea_share": _q(o.mea_share),
        }
        for o in sorted(
            owner_periods, key=lambda o: (str(o.unit_id), o.valid_from.isoformat())
        )
    ]


def build_weg_snapshot_payload(
    inp: WegSettlementInput, result: WegResult
) -> dict:
    """Canonical snapshot for a WEG-Jahresabrechnung (m4 section 2/1.5)."""
    period_days = (inp.period_end - inp.period_start).days + 1
    positions = sorted(
        inp.cost_positions, key=lambda p: (p.category_code, str(p.cost_entry_id))
    )
    plan_advances = sorted(inp.plan_advances, key=lambda a: str(a.owner_profile_id))
    paid_advances = sorted(inp.paid_advances, key=lambda a: str(a.owner_profile_id))

    return {
        "engine_version": inp.engine_version,
        "reserve_ledger": inp.reserve_ledger,
        "run_type": "WEG_JAHRESABRECHNUNG",
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
        "economic_plan_id": str(inp.economic_plan_id) if inp.economic_plan_id else None,
        "inputs": {
            "units": _units_payload(inp.units),
            "owner_periods": _owner_periods_payload(inp.owner_periods),
            "cost_positions": [
                {
                    "cost_entry_id": str(p.cost_entry_id),
                    "category_code": p.category_code,
                    "gross_amount": _m(p.gross_amount),
                    "service_period": {
                        "from": p.service_period_from.isoformat(),
                        "to": p.service_period_to.isoformat(),
                    },
                    "owner_distribution_key": p.owner_distribution_key,
                    "cost_group": p.cost_group,
                    "reserve_relevant": p.reserve_relevant,
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
            "plan_advances": [
                {
                    "owner_profile_id": str(a.owner_profile_id),
                    "plan_advance_total": _m(a.plan_advance_total),
                }
                for a in plan_advances
            ],
            "paid_advances": [
                {
                    "owner_profile_id": str(a.owner_profile_id),
                    "paid_total": _m(a.plan_advance_total),
                }
                for a in paid_advances
            ],
            "reserve": {
                "opening_balance": _m(inp.reserve.opening_balance),
                "withdrawals_total": _m(inp.reserve.withdrawals_total),
                "contributions_total": _m(inp.reserve.contributions_total),
                "overrides_total": _m(inp.reserve.overrides_total),
                "late_cash_total": _m(inp.reserve.late_cash_total),
            },
        },
        "computation": [
            {
                "cost_entry_id": str(c.cost_entry_id),
                "owner_profile_id": str(c.owner_profile_id),
                "category_code": c.category_code,
                "distribution_key": c.distribution_key,
                "cost_group": c.cost_group,
                "is_reserve": c.is_reserve,
                "total_amount": _m(c.total_amount),
                "key_base_total": _q(c.key_base_total),
                "key_base_share": _q(c.key_base_share),
                "time_share_days": c.time_share_days,
                "time_share_total_days": c.time_share_total_days,
                "rounded_amount": _m(c.rounded_amount),
                "note": c.note,
            }
            for c in sorted(
                result.computation,
                key=lambda c: (
                    str(c.owner_profile_id),
                    c.category_code,
                    str(c.cost_entry_id),
                ),
            )
        ],
        "result": {
            "owners": [
                {
                    "owner_profile_id": str(o.owner_profile_id),
                    "subtotal_umlagefaehig": _m(o.subtotal_umlagefaehig),
                    "subtotal_nicht_umlagefaehig": _m(o.subtotal_nicht_umlagefaehig),
                    "subtotal_reserve": _m(o.subtotal_reserve),
                    **({'reserve_notice': o.reserve_notice} if o.reserve_notice else {}),
                    "owner_cost_total": _m(o.owner_cost_total),
                    "plan_advance": _m(o.plan_advance),
                    "abrechnungsspitze": _m(o.abrechnungsspitze),
                    "paid_advances": _m(o.paid_advances),
                    "cost_lines": [
                        {
                            "line_type": ln.line_type,
                            "label": ln.label,
                            "amount": _m(ln.amount),
                            "sort_order": ln.sort_order,
                            "cost_entry_id": str(ln.cost_entry_id)
                            if ln.cost_entry_id
                            else None,
                            "category_code": ln.category_code,
                            "distribution_key": ln.distribution_key,
                            "total_amount": _m(ln.total_amount),
                            "distribution_base_total": _q(ln.distribution_base_total),
                            "distribution_base_share": _q(ln.distribution_base_share),
                            "time_share_days": ln.time_share_days,
                            "time_share_total_days": ln.time_share_total_days,
                            "note": ln.note,
                        }
                        for ln in o.cost_lines
                    ],
                }
                for o in sorted(result.owners, key=lambda o: str(o.owner_profile_id))
            ],
            "reserve_development": {
                "opening_balance": _m(result.reserve.opening_balance),
                "contributions_total": _m(result.reserve.contributions_total),
                "withdrawals_total": _m(result.reserve.withdrawals_total),
                "closing_balance": _m(result.reserve.closing_balance),
                "overrides_total": _m(result.reserve.overrides_total),
                "late_cash_total": _m(result.reserve.late_cash_total),
            },
            "cost_total": _m(result.cost_total),
            "reserve_contributions_total": _m(result.reserve_contributions_total),
            "plan_advances_total": _m(result.plan_advances_total),
            "abrechnungsspitze_total": _m(result.abrechnungsspitze_total),
        },
    }


def build_plan_snapshot_payload(
    inp: WirtschaftsplanInput, result: PlanResult
) -> dict:
    """Canonical snapshot for a Wirtschaftsplan (m4 section 5.4)."""
    planned = sorted(inp.planned_costs, key=lambda p: (p.category_code, p.label))
    return {
        "engine_version": inp.engine_version,
        "document_type": "WIRTSCHAFTSPLAN",
        "plan_year": inp.plan_year,
        "period": {
            "start": inp.period_start.isoformat(),
            "end": inp.period_end.isoformat(),
        },
        "property": {
            "id": str(inp.property.id),
            "name": inp.property.name,
            "management_type": inp.property.management_type,
        },
        "inputs": {
            "units": _units_payload(inp.units),
            "owner_periods": _owner_periods_payload(inp.owner_periods),
            "planned_costs": [
                {
                    "category_code": p.category_code,
                    "label": p.label,
                    "planned_amount": _m(p.planned_amount),
                    "distribution_key": p.distribution_key,
                    "cost_group": p.cost_group,
                }
                for p in planned
            ],
            "reserve_contribution_total": _m(inp.reserve_contribution_total),
        },
        "result": {
            "planned_lines": [
                {
                    "line_type": ln.line_type,
                    "label": ln.label,
                    "amount": _m(ln.amount),
                    "sort_order": ln.sort_order,
                    "category_code": ln.category_code,
                    "distribution_key": ln.distribution_key,
                    "total_amount": _m(ln.total_amount),
                    "distribution_base_total": _q(ln.distribution_base_total),
                    "note": ln.note,
                }
                for ln in result.planned_lines
            ],
            "subtotal_umlagefaehig": _m(result.subtotal_umlagefaehig),
            "subtotal_nicht_umlagefaehig": _m(result.subtotal_nicht_umlagefaehig),
            "reserve_contribution_total": _m(result.reserve_contribution_total),
            "total_advance_volume": _m(result.total_advance_volume),
            "owners": [
                {
                    "owner_profile_id": str(o.owner_profile_id),
                    "unit_id": str(o.unit_id),
                    "annual_advance": _m(o.annual_advance),
                    "monthly_amount": _m(o.monthly_amount),
                    "monthly_rates": [_m(r) for r in o.monthly_rates],
                    "distribution_base_share": _q(o.distribution_base_share),
                    "distribution_base_total": _q(o.distribution_base_total),
                }
                for o in sorted(
                    result.owners, key=lambda o: (str(o.owner_profile_id), str(o.unit_id))
                )
            ],
        },
    }


def build_asset_report_payload(
    inp: VermoegensberichtInput, result: AssetReportResult
) -> dict:
    """Canonical snapshot for a Vermoegensbericht (m4 section 6.5)."""
    return {
        "engine_version": inp.engine_version,
        "reserve_ledger": inp.reserve_ledger,
        "run_type": "VERMOEGENSBERICHT",
        "period": {
            "start": inp.period_start.isoformat(),
            "end": inp.period_end.isoformat(),
        },
        "property": {
            "id": str(inp.property.id),
            "name": inp.property.name,
            "management_type": inp.property.management_type,
        },
        "inputs": {
            "reserve": {
                "opening_balance": _m(inp.reserve.opening_balance),
                "withdrawals_total": _m(inp.reserve.withdrawals_total),
                "overrides_total": _m(inp.reserve.overrides_total),
                "late_cash_total": _m(inp.reserve.late_cash_total),
            },
            "reserve_contributions_total": _m(inp.reserve_contributions_total),
            "bank_balances": [
                {
                    "bank_account_id": str(b.bank_account_id),
                    "label": b.label,
                    "account_type": b.account_type,
                    "balance": _m(b.balance),
                }
                for b in sorted(
                    inp.bank_balances, key=lambda b: (b.account_type, b.label)
                )
            ],
            "receivables": [
                {"label": r.label, "amount": _m(r.amount)} for r in inp.receivables
            ],
            "payables": [
                {"label": p.label, "amount": _m(p.amount)} for p in inp.payables
            ],
        },
        "result": {
            "reserve_development": {
                "opening_balance": _m(result.reserve.opening_balance),
                "contributions_total": _m(result.reserve.contributions_total),
                "withdrawals_total": _m(result.reserve.withdrawals_total),
                "closing_balance": _m(result.reserve.closing_balance),
                "overrides_total": _m(result.reserve.overrides_total),
                "late_cash_total": _m(result.reserve.late_cash_total),
            },
            "lines": [
                {
                    "line_type": ln.line_type,
                    "label": ln.label,
                    "amount": _m(ln.amount),
                    "sort_order": ln.sort_order,
                    "is_manual": ln.is_manual,
                    "note": ln.note,
                }
                for ln in result.lines
            ],
            "bank_balance_total": _m(result.bank_balance_total),
            "receivables_total": _m(result.receivables_total),
            "payables_total": _m(result.payables_total),
        },
    }
