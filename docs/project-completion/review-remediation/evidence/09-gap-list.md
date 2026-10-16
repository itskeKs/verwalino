# Lückenliste — offene Nachweise, keine neue Produktbeauftragung

Stand: 2026-10-15. Kontextziel 64k / 65536; Implementierungsvorgabe
`mlx/qwen3.6-35b-a3b`; Goal-Checker `openai/gpt-6.1-sol`, `reasoning: medium`.
Keine volle Produkt-, Fach-, Security- oder Releasefreigabe.

| Punkt | Status | Konkrete nächste Voraussetzung |
| --- | --- | --- |
| V01 Accounting/Legal, WEG-Rücklage | blocked-fachfreigabe | Ziel-Soll/Rücklagentrennung, Golden Case, Rundung und historische Auswirkungen freigeben. Diagnose 1000 + 200 = OWNER_ADVANCE 1200, Spitze −200 bleibt erhalten. Keine Formeländerung. |
| Zwei-Reviewer-Quorum | extern offen | Operator organisiert zwei unabhängige Reviews. Konfiguration verlangt zwei, enthält nur claude/opus. Implementierer und Goal-Checker sind kein Ersatz; kein automatischer Rosterwechsel oder bezahlter Review. |
| Goal-Update HTTP 401 | extern blockiert | Frühere Versuche: HTTP/1.1 401 Unauthorized. Kein erneuter Versuch oder Credential-Suche; kopierbaren Goaltext liefern, nicht starten. |
| Backend-Gesamtlauf bg_705 | unvollständig, keine Abnahme | Timeout/Exit −1 nach ca. 56 %, bereits E-Marker. Gezielte Diagnose benötigt eigenen Anlass/Autorisation; keinen identischen Vollrun wiederholen. |
| Vollständige P3.3 | nicht neu abgenommen | Finanzinput-Funktionen sind Teilbelege, keine vollständigen A06/A13–A16-/WEG-/SEV-/Snapshot-/Stale-/PDF-Fixtures. Kein API-only-Parityclaim. |
| Vollständige P4.3 | teilweise, offen | Audience-/Household-Denial-/Former-Resident-/Invite-/Publishing-/Access-Journeys und Portaloberfläche benötigen belastbare Abnahme. A20→E3/P4.3 ist normative Referenz, kein Implementierungsauftrag dieser Reparatur. |
| Historischer Testworktree / Rohlogs | begrenzt rekonstruierbar | Alte Logs/Hashes erhalten; aktuelle Dokument-/Quellhashes rekonstruieren nicht rückwirkend den damaligen transitativen Teststand. Historische 2679 passed / 2 opt-in Skips sind kein aktueller Vollrun. |
| Backendkontextfenster | nicht verifiziert | 64k / 65536 ist Ziel, keine gemessene Kapazität. max_context_tokens allein und spätere Compaction beweisen keine historische Fensterkonformität. Historische 49k-/49152-Angaben/Portwerte unverändert. |
| Reales Deployment / Rolleinstellungen | nicht ausgeführt | Runbook und lokaler Mocktest sind keine Freigabe oder Prüfung einer realen Installation. Produktionsgate 0122 unverändert; lokaler Testhead 0123 nicht freigegeben. |

## Geschlossener Dokumentationsumfang

RF31-Zuordnungen zu leases/meetings/P4.3, die pauschale API→RF-Tabelle und die
unbelegten P3.3-/P4.3-Vollständigkeitsbehauptungen sind in den aktiven Dokumenten
korrigiert. Runbook trennt app_rw/app_worker/app_review sowie Erstinstallation und
Upgrade. State weist keine Portalimplementierung mehr an. Aktive Modell-/Kontext-
Angaben und der nur kopierbare Goal-Aufruf sind konsistent; siehe
[Abnahmematrix](09-acceptance-matrix.md), [normativer Abgleich](09-normative-pa-ic-audit.md),
[Prüfbericht](09-document-audit.json) und [Finalreceipt](../receipts/final-acceptance.md).

Früherer Frontendlauf bg_706: Lint/318 Tests/Build Exit 0; Chunkwarnung 871.02 kB
oberhalb 550 kB bleibt. Frühere Scanner-/Replacementtests nicht wiederholt.
Keine neuen DB-/Installations-/Produktcode-/Teständerungen in dieser Fortsetzung.
Fremde Änderungen und docs/research/ unangetastet; kein Secretzugriff, Provider,
Staging, Commit, Push oder Deployment. Nach Dokumentabschluss pausieren.
