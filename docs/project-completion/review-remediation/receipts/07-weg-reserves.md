# Receipt 07 — WEG-Rücklagenbefund qualifiziert

Stand: 2026-10-14. **Status: blocked-fachfreigabe; lokale Diagnose erledigt, kein Finanzfix.** Teilplan: `steps/07-weg-reserves.md`.

## Korrektur des früheren Receipts

„Kein Fehler gefunden“ war nicht belegt: Der vorhandene Golden-Test prüft die Ist-Reserve-Trennung mit manuell vorgegebenem Soll, **nicht** den vom Originalplan bezeichneten rücklagenhaltigen Vorschuss aus Wirtschaftsplan und Service. Auch die vorgeschlagene ADR-0007 ist keine erteilte V01-Freigabe.

## Evidenz

[Quellenqualifikation und konkrete Entscheidungsvorlage](../evidence/07-weg-source-qualification.md): Plan-/Service-/Engine-Stellen, ADR-0007, M4-Design und amtlicher §28 WEG. [DB-freie Diagnose](../evidence/07-reserve-diagnostic.py) / [stdout](../evidence/07-reserve-diagnostic.log) am 2026-10-14, Exit **0**:

`Kostenplan 1000 + Rücklage 200 → OWNER_ADVANCE 1200 → Ist-Kosten 1000 → Spitze −200`.

Ausgeführt im vorhandenen `backend/.venv` mit `USER`/`HV_OPENAI_API_KEY` entfernt. Keine DB, Provider oder Credentials benötigt. Die Diagnose bildet die gelesene Serviceaggregation nach, ist keine durchgängige DB-/Serviceprüfung. Historische Golden-Einzelläufe bleiben historische Teilnachweise; widersprüchliche Laufzeiten werden nicht zu aktueller Evidenz erklärt.

## Abnahme nach Originalplan §7

- **Erfüllt:** reproduzierbarer synthetischer Befund, Kosten-/Rücklagenanteil und Codeherkunft dokumentiert; konkrete fachliche Entscheidung einschließlich historischer Auswirkungen vorbereitet.
- **Offen/blockiert:** einschlägige V01 Accounting/Legal-Freigabe der Zielsemantik. Soll der Kosten-Sollanteil separat abgezogen werden oder ist der beobachtete −200-Effekt beabsichtigt? Golden Case und Herkunft/Trennung bestätigen lassen.
- **Nicht behauptet:** bestätigter juristischer Fehler, erledigter Fix oder vollständige WEG-Fachabnahme.

Keine Finanzsemantik, Tests, Erwartungen, Migrationen oder historische Final-Snapshots geändert. Nach Freigabe folgt der im Originalplan geforderte rote Regressionstest und kleinste konsistente Fix mit relevanten unveränderten Regressionen. Unabhängige Dokumentpakete laufen weiter.
