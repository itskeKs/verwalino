# Normativer Dokumentabgleich — RF31, P3.3, P4.3

Stand: 2026-10-15. Begrenzte Dokumentreparatur, kein Implementierungs- oder
vollständiger Parity-Nachweis. Umsetzungsvorgabe `mlx/qwen3.6-35b-a3b`,
Kontextziel 64k / 65536; Goal-Checker `openai/gpt-6.1-sol`, `reasoning: medium`.

## Quellen und korrigierte Einordnung

- [Hauptplan §6](../../../2026-10-01-property-management-implementation-plan.md#6-acceptance-and-reference-traceability)
- [Produktdesign: normative P-Zeilen](../../../2026-10-01-product-ux-technical-design.md)
- [Traceability](../../traceability.md), insbesondere A06/A12–A16, A20, A27 und RF-Index
- [Korrigiertes Parity-Register](../../../acceptance/parity-register.md)

| Punkt | Normative Einordnung | Ergebnis / Grenze |
| --- | --- | --- |
| RF31 | A27→F5/P5.5; RF25/RF26/RF31 unverified delivery | Falsche RF31-Zuordnungen zu `leases.py`/`meetings.py` entfernt. Das Fehlen einer bestimmten Route ist weder erforderlicher noch ausreichender semantischer Beweis. Keine RF31-Implementierungsabnahme. |
| P3.2/P3.3 | A06 geteilt; A12→D2/P3.2; A13–A16→D3/P3.3 | Finanzinput-API unterstützt Vollständigkeits-/Berechnungsgrenzen. Funktionsnamen oder API-Existenz beweisen weder ganze P3.3 noch alle Fixtures, PDFs, Stale-/WEG-/SEV-/Historienfälle. |
| P4.3 | A20→E3/P4.3, RF05/RF21/RF24 boundary | Bezeichnung vervollständigt: tenant portal R; owner scope D/V. Grants/Coverage/Source-Security sind Teilbelege; vollständige Portalabnahme bleibt offen. |

## Ausdrücklich zurückgenommen

Die frühere Aussage „P3.3 vollständig implementiert, weil attest/lock/normalize/
source_options vorhanden“ war nicht belegt. Ebenso war „A20 und E3 stehen nicht
wörtlich im Reparaturplan, also sind sie keine normativen P4.3-Referenzen“ falsch:
Der Plan fordert den Abgleich gegen die übergeordneten Quellen. Diese ordnen
A20 eindeutig E3/P4.3 zu. Daraus folgt **keine** Berechtigung, Portal-, Household-
oder Former-Resident-Funktionen im Dokumentreparaturauftrag zu bauen.

Frühere Tabellen nannten konkrete Routen/Funktionsdefinitionen ohne hinreichende
Quellenbindung. Diese Darstellung wird nicht als neue API-Inventur fortgeschrieben.
Die pauschale API→RF-Tabelle im Register wurde durch normative Quellen plus eine
klar nicht-normative API-Orientierung ersetzt.

## Offene Produktnachweise

P4.3 benötigt weiterhin belegte Audience-/Household-Denial-/Former-Resident-Grenzen,
Invite/Grant/Publishing und Dokument-/Message-/Reading-/Resident-Request-Journeys
sowie Portaloberfläche. Vorhandene unterstützende Infrastruktur ist nicht deren
End-to-End-Abnahme. Fehlende Nachweise bleiben offen, ohne sämtliche Mechanismen
als nachweislich abwesend zu behaupten. Keine neue Ausführung von A20/A27 oder
vollständige Fach-/Security-/Produktfreigabe.

V01 Accounting/Legal, Zwei-Reviewer-Quorum, HTTP-401-Blocker und unvollständiger
Backend-Gesamtlauf bleiben offen. Keine Produktcode-/Teständerung in dieser
Fortsetzung; Status und Grenzen stehen im [State](../state.md).
