# Arbeitsweise für Agenten

Ziel: Die Hausverwaltung wird zum MVP für den produktiven Eigenbetrieb fertiggestellt.
Alle Funktionen aus `docs/mvp.md` sollen in der laufenden App von Anfang bis Ende funktionieren.

## Regeln

- Fortschritt ist Code, Tests und ein Git-Commit. Keine State-, Contract-, Receipt-, Evidence-,
  Handoff-, Hash- oder Audit-Dateien anlegen und keine Reparatur- oder Review-Ketten aufbauen.
- `docs/mvp.md` ist die einzige Aufgabenliste. Eine Aufgabe nehmen, umsetzen, testen, committen,
  in `docs/mvp.md` abhaken. Neue Erkenntnisse als eine Zeile an die passende Aufgabe schreiben,
  nicht als neue Datei.
- Dokumentation heißt: `README.md`, `DEPLOY.md`, `docs/architecture.md` und die Fachseiten
  (`docs/m*.md`, `docs/adr/`, `docs/operations/`) knapp aktualisieren, wenn sich Verhalten ändert.
- `docs/fachreferenz.md` ist die fachliche Referenz (Funktionen und Abläufe der Referenzsoftware) für
  Umfang und Bedienablauf; gezielt mit `rg -n` lesen.
  Keine neuen Plan-, Protokoll- oder Statusdokumente.
- `docs/archive/` ist Altbestand. Nicht lesen und nicht als Regelwerk behandeln, außer der Nutzer
  verlangt es ausdrücklich für eine konkrete Frage.
- Eine Aufgabe pro Session, so geschnitten, dass sie in 64k Kontext passt: nur die Dateien lesen,
  die die Aufgabe nennt, große Dateien mit `rg -n` und Zeilenbereichen.
- Tests gezielt laufen lassen (`uv run pytest tests/test_x.py -k ...`, `npm test -- <datei>`),
  die ganze Suite nur vor einem Merge.
- Die produktive Instanz (`docker-compose.prod.yml`, Volumes `pgdata`, `documents`) enthält echte
  Daten: nie `docker compose down -v`, nie Volumes löschen, nie gegen diese Datenbank testen.
- Commits nicht pushen, außer der Nutzer verlangt es.
