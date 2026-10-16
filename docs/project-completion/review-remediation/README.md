# Crossreview-Befunde mit Qwen3.6 und 64k beheben — Reparaturkette

Diese separate Reparaturkette setzt den gespeicherten Plan
`crossreview-remediation-qwen36-49k` um. Sie ist unabhängig von der
Produkt-Roadmap ab Schritt 04b.

## Persistenter Plan und Einstieg

Der im Coddy-Sitzungsbundle gespeicherte Originalplan ist nun als
[Repository-Export](crossreview-remediation-qwen36-49k.plan.md) vorhanden.
Seine neun Pakete stehen unter `steps/`; Paket 08 wird in getrennten
08a-/08b-Kontexten ausgeführt. Die pending-Todos im Originalexport bleiben als
historischer Auftrag erhalten; aktueller Status steht ausschließlich im
[Reparatur-State](state.md).

Wiederaufnahme: `state.md` → `contract.md` → genau den unter `current` genannten
Teilplan lesen. Nicht den Gesamtplan oder alle Teilpläne laden.

Die vorgefundenen `04b-closure.md`, `operator-decision-required.md` und
`receipts/04b-assignment-mappings.md` bleiben erhalten, sind aber keine
Reparatur-Auftrags-/Abnahmequelle. Der darin angenommene fehlende 04b-Reparaturplan
ist kein Operatorblocker: Der Originalauftrag beginnt mit Paket 01 und verbietet
Produkt-04b. Darin behauptete Tests wurden beim Bootstrap nicht verifiziert.

## Aktuelle Begrenzung

Umsetzungsvorgabe `mlx/qwen3.6-35b-a3b`; unabhängiger Goal-Checker
`openai/gpt-6.1-sol`, `reasoning: medium`. Keine automatische Konfigurationsänderung.
Nur bestätigte Dokumentwidersprüche reparieren, **keine Produktcode-/Teständerung**.
P4.3 bleibt teilweise, Portal-/Household-/Former-Resident-Arbeit außerhalb des Scopes.
04a lokal abgeschlossen; 04b nicht begonnen; Produktionsgate 0122, lokaler Testhead 0123.
Historische 49k-/49152-Evidence, Logs, Baselines und Portwerte bleiben unverändert.
Das [Goal](crossreview-remediation-qwen36-49k.plan.md#start-goal--nur-zum-kopieren-nicht-ausführen)
wird nur zum Kopieren erstellt, nicht gestartet oder extern aktualisiert.

## Zweck

Behebung der im Crossreview identifizierten Dokumentationsfehler,
sicherungsrelevanter Evidenzlücken und fachlicher Verdachtsfälle
(WEG-Rücklagen, Doppeladoption, Scanner-Recovery) ohne eigenmächtige
Finanzänderungen.

## Arbeitsprinzipien

- Ausschließlich diesen Plan, `state.md`, `contract.md` und **einen**
  aktuellen Teilplan laden.
- Kontextziel: 64k / 65536 Tokens, Arbeitsziel weiterhin höchstens ~28.000 belegte Tokens; Backendfenster nicht nachgewiesen.
- Pro Sitzung höchstens ein kohärenter Schritt.
- Nach jedem Schritt: Receipt schreiben, `state.md` aktualisieren,
  Kontextwechsel durchführen.
- Keine fremden Änderungen, keine Produktionsaktionen, keine automatischen
  Commits oder Pushes.
- Keine Fortsetzung der Produkt-Roadmap ab 04b.

## Arbeitspakete

| Nr. | Beschreibung | Status |
|-----|-------------|--------|
| 01 | Ausgangszustand sichern und separate Reparaturkette anlegen | done-local |
| 02 | Statusautorität und Modell-/Kontextvertrag klären | 64k-Steuerung korrigiert; aktuelle Prüfung in Paket 09 |
| 03 | 03a-Receipt und B2-Restpunkte reparieren | frühere Nachweise erhalten; B2-Restpunkte keine neue Produktfreigabe |
| 04 | Parity-Register konsolidieren | Dokumentzuordnung korrigiert; P3.3/P4.3 nicht vollständig abgenommen |
| 05 | Deployment-Runbook berichtigen | Dokumentkorrektur; kein Deployment und keine Runtimefreigabe |
| 06 | 04a-Evidenz und Reproduzierbarkeit sichern | historisch qualifiziert; damaliger Worktree nicht vollständig rekonstruierbar |
| 07 | WEG-Rücklagenfehler qualifizieren; Fachgate respektieren | Diagnose erhalten; blocked-fachfreigabe V01 |
| 08 | Doppeladoption und Scanner-Recovery getrennt untersuchen | historische lokale Nachweise erhalten; nicht neu ausgeführt |
| 09 | Endprüfung und ehrlichen Goal-Abschluss erstellen | aktueller Dokumentaudit; Ergebnis siehe State/Receipt |

## Freigabegates

- **V01**: Accounting/Legal — WEG, Rücklagen, gemischte Scopes, Golden Cases.
  Ohne V01-Freigabe keine eigenmächtige Formel- oder Rücklagenänderung.
- **V03**: Accounting/Perioden/Steuer.
- **V05**: Org-Admin, versionierte Teamfreigabe.
- **Externes Zwei-Reviewer-Gate**: `.coddy/crossreview.json` verlangt
  `min_reviewers: 2`, ist aber nur mit `claude:opus` konfiguriert — bleibt offen.

- **HTTP 401**: externe Goal-Updates blockiert; kein neuer Versuch oder Credential-Suche.
- **Backend**: früherer Gesamtlauf Timeout/Exit -1, mit E-Marker; keine Abnahme.
- **Frontend**: früherer Sitzungslauf Lint/318 Tests/Build Exit 0, Chunkwarnung bleibt.

## Ergebnis

Vollständig erledigt nur bei erfüllten Plan-Abnahmen. Fachliche oder Test-
blocker bedeuten `blocked` oder `open`, nicht vollständigen Erfolg.
