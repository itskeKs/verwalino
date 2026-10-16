# Receipt 08b — Scanner-Recovery, korrigiert und quellengebunden

Stand: 2026-10-14. Teilplan `steps/08b-scanner-recovery.md`, Originalplan §8b/§9.
**Lokale Nachweislücke geschlossen; kein Recovery-Policyfix nach endgültiger Budgetausschöpfung.**

## Befund / kleinster Delta

- Vorhandener Retry ist autorisiert (Access-Recheck, Fence, expliziter Grund, Idempotenz) und kann **innerhalb** des Budgets blocked→clean herstellen.
- Versuch≥3 wird dauerhaft abgelehnt. Das folgt dem bestehenden SQL-Vertrag, nicht einer Scanner-Verfügbarkeitserkennung.
- Der einzige eigene Backend-Delta ergänzt `test_blocked_inspection_retries_same_original_with_bounded_replay`: Scanner nach Ausschöpfung verfügbar, frischer Retry409, Clean-Stub nicht ausgeführt, weiterhin3 Attempts/0Refs/1received/3inspected. [Originalbytes](../evidence/08b-test-intake-batches.before.txt), [eigener Patch](../evidence/08b-owned-test.delta.patch).
- Kein Production-/Harness-/Finanzcode geändert, keine Assertions geschwächt, keine neuen Skips/Suppressions.

## Tatsächlich ausgeführte Prüfungen

| Prüfung | Ergebnis / Exit | Evidenz |
|---|---|---|
| unveränderte Ausgangsregression `bg_614` | 1 passed in7.39s, Exit0 | [Log](../evidence/08b-baseline-20261014.log) |
| ergänzte Sequenz + Recovery/Rechte/Fencing/Outbox-Rollback/alte Events/Scannerparameter `bg_616` | **12 passed in10.20s, Exit0** | [Log](../evidence/08b-regression-20261014.log) |

[Mapping jeder Anforderung auf echte Assertion/Node/Lauf](../evidence/08b-assertion-mapping.md). Vor Start Disposable erneut bestätigt: richtige Compose-Datei, postgres16, `127.0.0.1:55432`, tmpfs, healthy. Vorhandene `backend/.venv`, Prozesse ohne USER/HV_OPENAI_API_KEY, DB-Läufe seriell. Kommandos und Quellenhashes im Auditmanifest.

## Korrigierte Fehlaussagen / Grenzen

Die erfundene Pflicht „unavailable→erschöpft→409→neuer202→clean“ wird zurückgenommen. Die echte negative Sequenz nach Limit und die erfolgreiche Recovery **vor** dem Limit sind zwei verschiedene belegte Tests. Recovery-Replay liefert dasselbe Receipt, nicht pauschal einen zweiten409. Rechte-/Fencing-/Rollbacktests sind vorhanden und grün; „alle Negativtests fehlen“ war falsch.

Historische 27+17+9=53-Fälle-/Laufzeitclaims ohne Rohlogs werden nicht als aktueller Lauf oder vollständig bewiesene Gesamtfreigabe behandelt. Elf malformed-Parameter, nicht zwölf. Kein rückwirkendes Umschreiben des gefilterten „7 passed,20 deselected“-Laufs zu27.

## Konkrete offene Entscheidung

**Retrypolicy:** Soll ein zusätzlicher autorisierter Pfad nach endgültigem Versuchslimit eingeführt werden? Aktuell existiert über den geprüften Endpoint keiner. Falls gewünscht, genaue Berechtigung, Budget, Attempt-/Fencing-/Idempotenz-/Effektregeln vorher festlegen; kein Behelf durch Reset oder Live-Scannerinstallation. Die bestehende Sperre ist belegt, keine bestätigte Sicherheitslücke. Ohne weiteren Auftrag keine Semantikänderung.

Gesamtplan bleibt wegen WEG-V01 und unabhängigen Reviewgates nicht vollständig erledigt. Diese Gates blockieren die lokale Scannerprüfung nicht; es sind keine zusätzlichen Scanner-Fachgates.
