# Abnahmematrix — crossreview-remediation-qwen36-49k

Stand: 2026-10-15. **Dokumentreparatur, keine Gesamt-/Produkt-/Fach-/Releasefreigabe.**
Umsetzungsvorgabe `mlx/qwen3.6-35b-a3b`, Kontextziel 64k / 65536;
Goal-Checker `openai/gpt-6.1-sol`, `reasoning: medium`. Backendfenster nicht gemessen;
Goal nur als kopierbarer Text, nicht ausgeführt.

| Anforderung | Evidenz | Status / Grenze |
| --- | --- | --- |
| Aktiver Modell-/Kontext-/Scopevertrag | [Plan](../crossreview-remediation-qwen36-49k.plan.md), [Vertrag](../contract.md), [Steuerungsreceipt](../receipts/plan-contract-state-alignment.md), [State](../state.md) | Aktive Steuerung auf 64k / 65536 korrigiert; historischer Identifier/Pfad unverändert. Keine automatische Konfiguration oder Produktfortsetzung. |
| §4 normative P/A/RF und API-Orientierung | [Abgleich](09-normative-pa-ic-audit.md), [Register](../../../acceptance/parity-register.md), [Traceability](../../traceability.md) | RF31→F5/P5.5/A27, nicht leases/meetings/P4.3; unbelegte API→RF-Tabelle ersetzt. P3.3/P4.3 nicht anhand Funktionsnamen vollständig abgenommen. A20→E3/P4.3 bleibt normative Referenz, kein Produktauftrag. |
| §5 Deploymentrunbook | [Runbook](../../../../DEPLOY.md), [lokaler Mocktest](09-deployment-mock.json) | Gate 0122, lokaler Testhead 0123; Web/Worker/Review getrennt; Erstinstallation/Upgrade und öffentliche Vorlage geklärt. Mocktest Exit 0; keine reale Runtime-/Deploymentfreigabe. |
| §1/6 aktueller Dokumentstand und Schutzgrenzen | [Fortsetzungsschutz](09-continuation-protection.json), [historischer Baselinevergleich](09-baseline-comparison.json), [Manifest](09-audit-manifest.json) | Eigene Dokumentkorrekturen getrennt von vorbestehendem Scanner-Testdelta. Historische Logs/Baselines/Patchbytes und 49k-/49152-/Portwerte unverändert; damaliger Testworktree weiterhin nicht vollständig rekonstruierbar. |
| §9 Links, AST-Testreferenzen, Whitespace, aktive Steuerung | [Auditreport](09-document-audit.json), [Helper](09-audit.py) | Direkte Prüfung auch untracked Dateien; Resultat/Umfang/Exitcode im Report. Kein vollständiger semantischer Produkt- oder Fremdcodeaudit. |
| §7 WEG-Rücklage | [Quellenqualifikation](07-weg-source-qualification.md), [historische Diagnose](07-reserve-diagnostic.log) | 1000 Kosten + 200 Rücklage = OWNER_ADVANCE 1200, Spitze −200. Diagnose erhalten; V01 Accounting/Legal offen. Keine Formeländerung, kein erledigter Finanzfix. |
| §8a Doppeladoption / §8b Scanner | [Fallmatrix](../receipts/08a-duplicate-adoption.md), [Assertionmapping](08b-assertion-mapping.md), [historische Läufe](09-audit-manifest.json) | Frühere begrenzte lokale Nachweise erhalten: Baseline 1 passed, Scanner 12 passed, Replacement 3 passed, Identitätsdiagnose 8 Assertions. Nicht neu ausgeführt, kein voller aktueller Backendnachweis. |
| Frontend-/Backend-Gesamtstand | [Finalreceipt](../receipts/final-acceptance.md) | Früherer Frontendlauf bg_706: Lint/318 Tests/Build Exit 0; Chunkwarnung bleibt. Backend bg_705: Timeout/Exit −1 bei ca. 56 %, E-Marker vorhanden; keine Abnahme. Kein identischer Vollrun wiederholt. |
| §9 unabhängiges Zwei-Reviewer-Quorum | [State](../state.md) | Offen: zwei Reviewer gefordert, nur claude/opus konfiguriert. Implementierer und Goal-Checker ersetzen es nicht; kein Rosterwechsel oder bezahlter Review. |
| Externe Goal-Aktualisierung | [Finalreceipt](../receipts/final-acceptance.md) | HTTP/1.1 401 Unauthorized bleibt Blocker; kein Updateversuch, keine Credential-Suche. Goal nur zum Kopieren erstellt. |

## Abschlussgrenze

Die aktuelle Dokumentreparatur ist von den offenen fachlichen und produktbezogenen
Abnahmen getrennt. P4.3 bleibt teilweise; offene Portal-/Household-/Former-Resident-
Nachweise werden hier nicht implementiert. Historische Rohlog-/Quellenlücken und
2679 passed / 2 opt-in Skips werden nicht durch neue Dokumenthashes zu einer
heutigen Gesamtabnahme. Siehe [Lückenliste](09-gap-list.md) und
[Finalreceipt](../receipts/final-acceptance.md). Danach pausieren, kein Nachfolger ab 04b.
