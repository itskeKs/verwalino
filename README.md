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

## Eigenes Passwort ändern

Nach der Anmeldung öffnet „Passwort ändern“ in der oberen Benutzerleiste den
Dialog für aktuelles Passwort, neues Passwort und Bestätigung. Falsches
Altpasswort und unzulässiges neues Passwort werden im Dialog angezeigt. Nach
erfolgreichem Wechsel erfolgt die Abmeldung; anschließend mit dem neuen Passwort
anmelden. Der lokale Browserablauf ist mit
`scripts/acceptance/t14_password_browser.cjs` gegen `scripts/dev-local.sh`
wiederholbar (vorhandenes Playwright über `HV_PLAYWRIGHT_PACKAGE`, Chrome).

## Bankumsätze (Phase 2, Datenmodell)

Bankumsätze werden getrennt von gebuchten Zahlungen geführt: Beträge sind
signierte `Decimal`-Werte (Gutschrift positiv, Lastschrift negativ), Importzeilen
sind je Konto über ihren Importhash eindeutig. RLS und Composite-Fremdschlüssel
binden Umsatz, Konto und optionale Zahlungs-Verknüpfung an dieselbe Organisation
und dasselbe Konto. Nach dem Buchen ist auch diese Verknüpfung unveränderlich;
verknüpfte Umsätze sind nicht löschbar. Parser, Import-API und Oberfläche folgen
in den nächsten Aufgaben; das konkrete Bankprofil aus F2 ist noch offen.

Weiterführende Dokumentation:

- [Deployment](DEPLOY.md)
- [Architektur](docs/architecture.md)
- [MVP-Plan und Aufgabenliste](docs/mvp.md)
- [Teilungserklärungen und Gebäudewissen](docs/operations/declaration-imports.md) — Upload, prüfbare Extraktion, freigegebene Gebäuderegeln und Betrieb.
