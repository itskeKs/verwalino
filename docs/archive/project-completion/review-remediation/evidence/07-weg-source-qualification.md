# WEG-Rücklagen — Quellenqualifikation und Entscheidungsvorlage

Stand: 2026-10-14. Status: **reproduzierter Befund; blocked-fachfreigabe**, kein erledigter Finanzfix.

## Planfall und aktueller Nachweis

Originalplan §7 verlangt die Kette Wirtschaftsplan → Service → WEG-Engine, nicht nur die Reserve-Trennung innerhalb der Engine. [DB-freie Diagnose](07-reserve-diagnostic.py) mit dem vorhandenen `backend/.venv/bin/python`, `USER`/`HV_OPENAI_API_KEY` entfernt, `PYTHONPATH=backend`: [stdout](07-reserve-diagnostic.log), tatsächlicher Exit **0** am 2026-10-14.

Ein Eigentümer, eine Einheit, 1.000 € geplante und tatsächliche Kosten, 200 € geplante Rücklage:

| Größe | Beobachtung |
|---|---:|
| geplante Kosten | 1.000,00 € |
| geplante Rücklage | 200,00 € |
| OWNER_ADVANCE / übernommenes Soll | 1.200,00 € |
| OWNER_COST_TOTAL | 1.000,00 € |
| Abrechnungsspitze | −200,00 € |

Die Diagnose aggregiert die Engine-Planresultate wie der gelesene Service; sie führt **keine** DB-/Serviceintegration aus. Assertions sichern das beobachtete Ist-Verhalten, keine genehmigte Zielsemantik.

## Entscheidende Code- und Projektstellen

- `backend/app/engine/wirtschaftsplan.py:110–113`: Gesamtvorschuss = Kosten + Rücklagenzuführung; `:167–180` verteilt dies auf `annual_advance`.
- `backend/app/services/weg_settlement_service.py:478–500`: `_plan_advances_per_owner` summiert `OWNER_ADVANCE.planned_amount` ohne Trennung und übergibt `plan_advance_total`.
- `backend/app/engine/weg.py:408–413,437–443`: Rücklagen separat, nicht im Ist-Kostenvolumen; `:490–492`: `owner_cost_total = uml + nicht`, Spitze = Kosten − Gesamt-Soll.
- `backend/app/engine/types.py:528–533`: `annual_advance` ist ausdrücklich Kosten **plus** Rücklage.
- [ADR-0007](../../../adr/0007-weg-abrechnungsspitze-und-wirtschaftsplan.md), Status **Vorgeschlagen**, §§Entscheidung/Konsequenzen: dieselbe Kombination; keine dokumentierte einschlägige V01-Freigabe.
- [M4-Design](../../../m4-weg-abrechnung.md) §§2.1–2.4,3.1,5: Soll statt Ist-Zahlungen; Rücklage als Vermögensbildung separat; fachliche Abnahme ausdrücklich offen.
- `backend/tests/engine/test_golden_weg.py::TestGolden3ThreeSpheres::test_reserve_not_in_owner_cost_total_or_spitze` prüft 1.500 Kosten − **manuell vorgegebenes** 1.400 Soll = 100. Er prüft nicht die Plan-/Serviceherkunft eines rücklagenhaltigen Solls und widerlegt den Planfall daher nicht.

## Fachquelle und Qualifizierung

[§28 WEG, amtlicher Gesetzestext](https://www.gesetze-im-internet.de/woeigg/__28.html), abgerufen 2026-10-14:
- Abs.1 unterscheidet Vorschüsse zur **Kostentragung** und zu **Rücklagen**.
- Abs.2 regelt Nachschüsse/Anpassung der beschlossenen Vorschüsse auf Grundlage der Jahresabrechnung.
- Abs.4 verlangt gesonderten Vermögensbericht einschließlich Rücklagenstand.

Der Gesetzestext gibt keine implementierbare Formel für diesen konkreten Grenzfall frei. **Bestätigt** ist der technische Rücklageneffekt auf die Spitze. **Korrekt gemäß der bestehenden Projektbeschreibung** sind die getrennte Ist-Rücklagenausweisung und nachrichtliche Behandlung tatsächlicher Zahlungen. **Offene Fachfrage** ist die Zielabgrenzung des rücklagenhaltigen Plan-Solls; weder „kein Fehler“ noch ein abschließend bestätigter Rechts-/Finanzfehler ist belegt.

## Konkrete V01-Entscheidung (Originalplan §7)

Accounting/Legal (Steuerberater/Fachanwalt WEG) soll für diesen Golden Case festlegen:
1. Welche Kosten-/Rücklagen-Sollanteile gehen in die Spitze ein? Bleibt −200 zulässig oder ist bei identischen Plankosten/Ist-Kosten ein anderer Betrag erforderlich?
2. Wie sind Soll/Ist-Rücklagen und Entnahmen nachzuweisen, ohne Vermögensbildung als verbrauchte Kosten zu behandeln?
3. Herkunft/Trennung je Eigentümer/Einheit, Rundung und historische Auswirkungen bestätigen.

Historisch könnten vorhandene Final-Snapshots den Effekt enthalten. Kein Bestands-/Produktionsaudit durchgeführt; **keine** alten Snapshots, Hashes, Migrationen oder Finanzformeln geändert. Erst nach Freigabe: roter Regressionstest, kleinster konsistenter Fix, Null-/positive Rücklage, mehrere Eigentümer/Einheiten, Rundung und nachrichtliche Zahlungen; alte Finals unverändert.

Historische Einzellaufzeiten 3.22/6.06/6.32s sind keine aktuelle Quellenbindung und werden nicht addiert oder durch einen neuen Lauf umgeschrieben. Die Behauptung einer fehlenden Virtualenv war falsch: die vorhandene isolierte Umgebung ist nutzbar. Es wurde kein unveränderter Golden-Test wiederholt.
