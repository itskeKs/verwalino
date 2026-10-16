# Reparaturpaket 09 — Endprüfung und Goal-Abschluss

Quelle: `../crossreview-remediation-qwen36-49k.plan.md`, Abschnitt 9.
Dieser Teilplan ist aus dem gespeicherten Original abgeleitet, kein Produktauftrag.

## Aktive Fortsetzungsgrenze

Kontextziel **64k / 65536**; Umsetzungsvorgabe `mlx/qwen3.6-35b-a3b`;
Goal-Checker `openai/gpt-6.1-sol`, `reasoning: medium`. Keine tatsächliche
Backendfenstermessung behaupten, keine Konfiguration ändern. Goal nur als Text,
nicht ausführen oder extern aktualisieren (HTTP-401-Blocker).

Nur Dokument-/Steuerungskorrekturen und deren `evidence/09-*`-Prüfung. Keine
Produktcode-/Teständerungen, keine Portal-/Household-/Former-Resident- oder
Roadmap-Implementierung ab 04b. Die folgenden allgemeinen Code-/DB-Prüfvorgaben
sind keine Erlaubnis, solche Änderungen oder neue DB-Läufe zu beginnen.
Historische 49k-/49152-Evidence, Baselines, Logs und Portwerte bleiben unverändert.
Nach grünem Dokumentaudit und kopierfertigem Goal pausieren. V01 Accounting/Legal,
fehlendes Zwei-Reviewer-Quorum, unvollständiger Backend-Gesamtlauf und P4.3-Lücken
bleiben offen; keine vollständige Produkt-, Fach- oder Releasefreigabe.

## Auftrag, Einstieg und Abnahme
Für Dokumentänderungen Links, relevante P/A/RF-Zuordnungen, Test-Node-IDs, State-/Receiptkonsistenz und `git diff --check` prüfen. Vor jeder Codeänderung Ausgangsregression, danach passende Engine-/Service-/DB-/Securitytests; Frontendchecks nur bei berührtem Frontend. Negative Rechte-/Retry-/Rollbacktests für geänderte Grenzen obligatorisch.

Bei Produktions-/Harnesscodeänderungen einen vollständigen seriellen Backendlauf auf verifizierter Disposable-Umgebung als Abschlussqualifikation einplanen; gemeinsame Fehler nicht durch neue Skips oder schwächere Assertions umgehen. Bei reinen Dokumentänderungen nicht unnötig den Vollrun wiederholen. Fehlende Umgebung/Autorisierung ergibt einen konkreten Testblocker.

Lokale DB nur nach erneutem Disposable-Check; Test-URLs gemäß bestehendem Vertrag `127.0.0.1:55432`, `USER` und `HV_OPENAI_API_KEY` aus Testprozessen entfernen. Keine Produktiv-DB, Credentials, Provider, Versand, Push, SSH oder Deployment. Kein fremder Diff und keine historische Evidenz umschreiben.

Abschlussbericht mit Befund-ID, Status, geänderten Dateien, ausgeführten Prüfungen/Exitcodes, Evidenzlinks, offenen Entscheidungen und nächster Aktion. Notwendige Tests nicht ausgeführt oder Fix fachlich blockiert → Gesamtziel **nicht vollständig erreicht**. Unabhängige erledigte Pakete separat ausweisen; danach pausieren statt Wiederholungsschleifen.

Ein zweites unabhängiges Review bleibt ein externes Gate: Roster nicht eigenmächtig ändern, keine kostenpflichtigen Reviews starten. Qwen als Implementierer oder Goal-Prüfer erfüllt das frühere Zwei-Reviewer-Quorum nicht automatisch.

## Übergabe und Grenzen
- Nachfolger: `null — Abschluss oder ehrliche Pause`. Nur nach lokal erfüllter Abnahme weitergehen; fachlich blockierte Anteile separat festhalten und unabhängige Pakete fortführen.
- Receipt im Reparaturverzeichnis schreiben, State aktualisieren und erneut lesen; anschließend Kontextwechsel.
- Nur diesen Teilplan, Reparatur-State und Vertrag als Auftrag verwenden. Relative Produkt-Receipts/Steps im Originalabschnitt beziehen sich auf `docs/project-completion/`, nicht auf dieses Reparaturverzeichnis.
- Kein Produkt-04b-Auftrag, keine Secrets, Produktionsaktionen, Provideraufrufe, Commits oder Pushes. Keine Finanzsemantik ohne einschlägige Freigabe ändern.
