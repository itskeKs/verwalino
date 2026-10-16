# Reparaturpaket 05 — Deployment-Runbook berichtigen

Quelle: `../crossreview-remediation-qwen36-49k.plan.md`, Abschnitt 5.
Dieser Teilplan ist aus dem gespeicherten Original abgeleitet, kein Produktauftrag.

## Auftrag, Einstieg und Abnahme
Einstieg: `DEPLOY.md`, `scripts/deploy-production.sh`, `scripts/verify-production.sh`, bestehende Deploymenttests sowie gezielt Compose-/Init-/Konfigurationsvorlagen, niemals echte Secretdateien.

`DEPLOY.md:175` nennt 0070, Skripte verlangen 0122. Ältere Abschnitte nennen `app_rw` für Worker, spätere verlangen `app_worker`; API-/Reviewzugänge ebenfalls eindeutig abgrenzen. Frischinstallation und Upgrade bestehender Volumes getrennt erklären. Bereitstellung von `.env.example` vor Hostvorbereitung trotz `.env.*`-Transferausschluss lösen, **ohne** echte `.env`-Dateien zu kopieren. Neue Worker-/Review-Schlüssel müssen auch für vorhandene Installationen als explizite Voraussetzung dokumentiert sein.

Das akzeptierte Deploymentgate **0122 bleibt unverändert**, solange keine separate Freigabe vorliegt. Keine Rollenprivilegien lockern und kein Produktionsskript ausführen. Geeignete bestehende Mock-/statische Tests erst nach Prüfung ihrer Seiteneffekte verwenden.

**Abnahme:** Anleitung entspricht den tatsächlichen Skript-/Rollenverträgen und enthält sichere Voraussetzungen für Neuinstallation/Upgrade; keinerlei Deploymentaktion.

## Übergabe und Grenzen
- Nachfolger: `steps/06-source-evidence.md`. Nur nach lokal erfüllter Abnahme weitergehen; fachlich blockierte Anteile separat festhalten und unabhängige Pakete fortführen.
- Receipt im Reparaturverzeichnis schreiben, State aktualisieren und erneut lesen; anschließend Kontextwechsel.
- Nur diesen Teilplan, Reparatur-State und Vertrag als Auftrag verwenden. Relative Produkt-Receipts/Steps im Originalabschnitt beziehen sich auf `docs/project-completion/`, nicht auf dieses Reparaturverzeichnis.
- Kein Produkt-04b-Auftrag, keine Secrets, Produktionsaktionen, Provideraufrufe, Commits oder Pushes. Keine Finanzsemantik ohne einschlägige Freigabe ändern.
