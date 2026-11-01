"""Shared random scenario generator (pure data, no engine import)."""

import random, uuid
from datetime import date, timedelta
from decimal import Decimal


def U(n):
    return uuid.UUID(int=n)


def rand_scenario(seed):
    r = random.Random(seed)
    year = r.choice([2024, 2025, 2026])
    ys, ye = date(year, 1, 1), date(year, 12, 31)
    ndays = (ye - ys).days + 1
    nunits = r.randint(1, 4)
    units, leases, owners = [], [], []
    lid = 100
    for i in range(nunits):
        uid = U(10 + i)
        units.append(
            dict(
                id=uid,
                area=Decimal(r.randint(10, 150)) + Decimal(r.randint(0, 99)) / 100,
                mea=Decimal(r.randint(1, 500)),
            )
        )
        # owner change maybe
        if r.random() < 0.3:
            cut = ys + timedelta(days=r.randint(1, ndays - 1))
            owners.append(
                dict(
                    unit_id=uid,
                    owner_profile_id=U(900 + i),
                    valid_from=date(year - 2, 1, 1),
                    valid_to=cut - timedelta(days=1),
                )
            )
            owners.append(
                dict(
                    unit_id=uid,
                    owner_profile_id=U(950 + i),
                    valid_from=cut,
                    valid_to=None,
                )
            )
        else:
            owners.append(
                dict(
                    unit_id=uid,
                    owner_profile_id=U(900 + i),
                    valid_from=date(year - 2, 1, 1),
                    valid_to=None,
                )
            )
        # sequential leases over [year-1 .. year+1]
        cursor = date(year - 1, 1, 1) + timedelta(days=r.randint(0, 500))
        for _ in range(r.randint(0, 3)):
            start = cursor + timedelta(days=r.choice([0, 0, r.randint(1, 60)]))
            end = (
                None if r.random() < 0.25 else start + timedelta(days=r.randint(0, 400))
            )
            leases.append(
                dict(
                    lease_agreement_id=U(lid),
                    unit_id=uid,
                    tenant_profile_id=U(lid + 1000),
                    valid_from=start,
                    valid_to=end,
                )
            )
            lid += 1
            if end is None:
                break
            cursor = end + timedelta(days=1)
    positions = []
    for p in range(r.randint(1, 4)):
        kind = r.random()
        if kind < 0.6:
            sf, st = ys, ye
        elif kind < 0.85:
            a = ys + timedelta(days=r.randint(0, ndays - 1))
            b = a + timedelta(days=r.randint(0, (ye - a).days))
            sf, st = a, b
        else:
            a = ys - timedelta(days=r.randint(1, 200))
            sf, st = a, a + timedelta(days=r.randint(200, 500))
        total = Decimal(r.randint(-50000, 900000)) / 100
        key = r.choice(["WOHNFLAECHE", "EINHEIT", "MEA", "MANUELLER_BETRAG"])
        manual = []
        if key == "MANUELLER_BETRAG":
            parts = [Decimal(r.randint(0, 50000)) / 100 for _ in units]
            manual = [dict(unit_id=u["id"], amount=a) for u, a in zip(units, parts)]
            total = sum(parts, Decimal(0))
        positions.append(
            dict(
                cost_entry_id=U(5000 + p),
                key=key,
                total=total,
                sf=sf,
                st=st,
                manual=manual,
            )
        )
    return dict(
        year=year,
        ys=ys,
        ye=ye,
        units=units,
        leases=leases,
        owners=owners,
        positions=positions,
        cuts=sorted(r.sample(range(1, ndays), r.randint(0, 5))),
    )


def in_period_fingerprints():
    """Canonical in-period snapshots for the fixed 400-scenario compatibility set."""
    from app.engine.settlement import compute_bk_settlement
    from app.engine.snapshot import build_snapshot_payload
    from app.engine.canonical import canonical_sha256
    from app.engine.types import (
        CostPositionInput,
        LeasePeriodInput,
        OwnerPeriodInput,
        PropertyInput,
        SettlementInput,
        UnitInput,
        ManualAmountInput,
    )

    records = []
    for seed in range(400):
        s = rand_scenario(seed)
        units = tuple(
            UnitInput(u["id"], str(i), "WOHNUNG", area=u["area"], mea=u["mea"])
            for i, u in enumerate(s["units"])
        )
        leases = tuple(LeasePeriodInput(**lease) for lease in s["leases"])
        owners = tuple(OwnerPeriodInput(**owner) for owner in s["owners"])
        positions = tuple(
            CostPositionInput(
                p["cost_entry_id"],
                None,
                "C" + str(p["cost_entry_id"].int),
                "x",
                p["total"],
                p["sf"],
                p["st"],
                p["key"],
                "UMLAGEFAEHIG",
                manual_amounts=tuple(ManualAmountInput(**m) for m in p["manual"]),
            )
            for p in s["positions"]
            if p["sf"] >= s["ys"] and p["st"] <= s["ye"]
        )
        for lease in leases:
            if lease.valid_from > s["ye"] or (
                lease.valid_to and lease.valid_to < s["ys"]
            ):
                continue
            inp = SettlementInput(
                s["ys"],
                s["ye"],
                PropertyInput(U(1), "p", "MIET"),
                lease.lease_agreement_id,
                units,
                leases,
                owners,
                positions,
                Decimal("0.00"),
            )
            records.append(
                (
                    seed,
                    str(lease.lease_agreement_id),
                    canonical_sha256(
                        build_snapshot_payload(inp, compute_bk_settlement(inp))
                    ),
                )
            )
    return records
