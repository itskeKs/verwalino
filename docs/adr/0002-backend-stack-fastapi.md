# ADR-0002: Backend-Stack — Python/FastAPI (Empfehlung)

Status: **Vorgeschlagen** (Bestätigung durch den Menschen ausstehend — noch kein Code/Commit)
Datum: 2026-06-01 · Kontext: M0 · Bezug: [../architecture.md](../architecture.md) Abschnitt 6 · SDD §16

## Kontext

SDD §16 lässt das Backend offen: Python/FastAPI vs. Node.js/NestJS. Das Frontend ist auf React/TypeScript festgelegt. Wertbestimmend und am riskantesten ist die korrekte, prüffeste Abrechnung mit exakter Dezimalrechnung (Roadmap: M3 ist der größte/riskanteste Brocken). Daneben relevant: RLS-/Migrations-Handling (I1/I2), XRechnung/ZUGFeRD-Parsing (M5), HTML→PDF (SDD §16), Testbarkeit.

## Entscheidung

**Python/FastAPI** fürs Backend, mit **SQLAlchemy + Alembic** für ORM/Migrationen. Frontend bleibt React/TypeScript (SDD-Vorgabe).

## Begründung (domänenspezifisch)

- **Geldexaktheit (I4):** `decimal.Decimal` ist erstklassiger Standardtyp; `NUMERIC ↔ Decimal` verlustfrei. In Node ist `number` IEEE-754-Float → man muss dauerhaft eine Decimal-Lib erzwingen und sicherstellen, dass nirgends ein `number` für Geld durchrutscht (fehleranfälliger für ein korrektheitskritisches Finanzprodukt).
- **RLS/Migrationen (I1/I2):** Alembic versioniert RLS-Policies und Trigger als explizite, reviewbare DDL-Schritte — zentral für die Erzwingung der Invarianten.
- **E-Rechnung (M5):** reiferes Python-Ökosystem für XML/EN-16931 und PDF/A-3-Extraktion (ZUGFeRD).
- **Testbarkeit:** pytest + Hypothesis (Property-based Testing) passt besonders gut zu Verteilungs-/Rundungslogik.

## Alternative

**Node.js/NestJS:** vor allem attraktiv wegen *einer* Sprache über Front- und Backend (geteilte Typen/DTOs). Verworfen, weil der Geld-/Dezimalsicherheits- und RLS-/Migrationsvorteil von Python in dieser Domäne schwerer wiegt als der Single-Language-Vorteil. PDF (Playwright/Puppeteer) wäre in Node minimal komfortabler, ist aber sprachunabhängig anbindbar.

## Konsequenzen

**Positiv:** geringster Reibungswiderstand für die korrektheitskritische Abrechnung; sauberes RLS-/Migrations-Handling; starkes Test- und E-Rechnungs-Ökosystem.

**Negativ:** zwei Sprachen im Stack (TS-Frontend, Python-Backend) → Kontextwechsel, keine geteilten Typen out-of-the-box. Bewusst in Kauf genommen.

**Nicht verhandelbar, unabhängig vom Stack:** (1) Geld als dezimaler Typ, nie Float; (2) RLS + `FORCE`, App-Rolle ohne `BYPASSRLS`; (3) transaktionslokaler Tenant-Context; (4) RLS-Policies/Trigger als explizit versionierte Migrationen.
