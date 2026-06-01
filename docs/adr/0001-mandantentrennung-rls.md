# ADR-0001: Mandantentrennung über Shared DB + PostgreSQL Row-Level Security

Status: **Vorgeschlagen** (Bestätigung durch den Menschen ausstehend)
Datum: 2026-06-01 · Kontext: M0 · Bezug: [../architecture.md](../architecture.md) Abschnitt 2 · SDD §4, §16

## Kontext

Das Produkt ist explizit mandantenstark (SDD §4): eine professionelle Verwaltung betreut viele fremde Eigentümer/WEGs. Mandantentrennung ist eine harte Invariante (kein Cross-Org-Zugriff) und berührt zusätzlich die Vermögens-/Treuhandtrennung — ein Datenleck ist nicht nur ein Datenschutz-, sondern ein potenziell haftungsrelevanter Fehler. Die Strategie lässt sich kaum nachrüsten und ist daher die folgenreichste M0-Entscheidung.

## Entscheidung

**Shared Database + verpflichtendes `organization_id`-Scoping + PostgreSQL Row-Level Security (RLS)**, durchgesetzt auf drei Ebenen:
1. DB-RLS mit `ENABLE` + `FORCE ROW LEVEL SECURITY` und `USING`/`WITH CHECK`-Policies, gebunden an eine Session-GUC `app.current_org_id`; App verbindet als Rolle **ohne** `BYPASSRLS` und ohne Tabelleneigentum.
2. Tenant-Context-Middleware, die `org_id` nur aus der serverseitigen Session ableitet und transaktionslokal (`SET LOCAL`) setzt; fail-closed (kein Kontext → null Zeilen).
3. Repository-/Query-Guard (expliziter Filter für Performance + sprechende Fehler) plus CI-Test, der RLS-Abdeckung jeder Tenant-Tabelle erzwingt.

## Alternativen

- **A — Shared DB, nur App-Layer-Scoping:** verworfen. Hängt an der Disziplin jeder einzelnen Query; ein vergessenes `WHERE` leckt Fremddaten. Kein Sicherheitsnetz.
- **C — Schema-per-Tenant:** verworfen für das MVP. Starke Isolation, aber multipliziert Migrations-/Provisionierungs-/Routing-Komplexität mit der Tenant-Zahl und erschwert mandatsübergreifendes Reporting der Verwaltung selbst — ohne den proportionalen Mehrwert gegenüber RLS.
- **D — DB-/Cluster-per-Tenant:** verworfen. Für ein MVP unverhältnismäßig teuer/komplex.

## Konsequenzen

**Positiv:** Defense-in-Depth (DB ist letzte, nicht umgehbare Linie); eine Schemaversion/ein Migrationspfad; einfache, RLS-kontrollierte Cross-WEG-Auswertungen für die Verwaltung; objektiv testbar (Isolationstest auf API- und DB-Ebene).

**Negativ / zu beachten:** Sorgfalt nötig bei (a) App-DB-Rolle ohne `BYPASSRLS`, (b) zuverlässigem, transaktionslokalem Setzen des Kontexts bei Connection-Pooling, (c) Migrationen/Hintergrundjobs als getrennte privilegierte Rolle, (d) Indizes auf `organization_id` für Performance. Diese Punkte sind im Scaffolding-Plan (architecture.md Abschnitt 7) als verbindliche Schritte festgehalten.

**Abgrenzung:** Vermögenstrennung (innerhalb einer Org zwischen Mandaten/WEGs) ist eine zweite Achse; in M0 nur im Modell vorbereitet, DB-Härtung frühestens mit M6 (siehe architecture.md 2.6).
