# Hausverwaltung

Webanwendung für die Verwaltung von Immobilien, Einheiten, Vorgängen und
abrechnungsrelevanten Daten.

## Lokales Setup für Entwicklung und Teilungserklärungen

Im Backend-Verzeichnis werden die Entwicklungs-, OCR- und Betriebsabhängigkeiten
gemeinsam installiert:

```bash
cd backend
uv sync --extra dev --extra ocr --extra ops
```

Der Import von Teilungserklärungen ist ein prüfpflichtiger Workflow. Er erzeugt
nie ohne **Human Approval** kanonische Gebäude-, Einheits- oder Regel-Daten;
Dokumentinhalt bleibt untrusted data. Betrieb, Datenschutz, lokale Tests und die
absichtlich gesperrte Live-Evaluation stehen im
[Runbook für Teilungserklärungen](docs/operations/declaration-imports.md).

Weiterführende Dokumentation:

- [Deployment](DEPLOY.md)
- [Architektur](docs/architecture.md)
- [MVP-Plan und Aufgabenliste](docs/mvp.md)
- [Teilungserklärungen und Gebäudewissen](docs/operations/declaration-imports.md) — Upload, prüfbare Extraktion, freigegebene Gebäuderegeln und Betrieb.
