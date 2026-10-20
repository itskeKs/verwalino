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
verknüpfte Umsätze sind nicht löschbar. Import-API und Oberfläche folgen in den
nächsten Aufgaben.

Der reine CSV-Parser unterstützt Semikolon-Dateien in UTF-8 (auch BOM) und CP1252
mit deutschen Datums-/Dezimalformaten. Pflichtspalten sind
`Buchungstag`/`Buchungsdatum`, `Wertstellung`/`Valutadatum` und
`Betrag`/`Betrag (EUR)`/`Betrag (€)`; optional Name (`Name`, `Gegenkonto Name`
oder `Auftraggeber / Begünstigter`), IBAN (`IBAN`/`Gegenkonto IBAN`),
`Verwendungszweck` und `Währung` (nur EUR). Keine freie Mapping-Oberfläche.
Zusätzlich geprüft sind DKB-Giro-Altexporte und das Semikolon-Schema seit 2023:
[veröffentlichte Formatdefinition](https://github.com/siddhantgoel/beancount-dkb/blob/6b9266e54bdbf566dfba63c44aa9cc7657144b8b/beancount_dkb/extractors/ec.py).
Das ist ein nachvollziehbares Open-Source-Formatbeispiel, keine Zusicherung für
jede heutige DKB-Exportvariante oder eine Festlegung der tatsächlich genutzten Bank.
Bei DKB 2023 wird die Gegenpartei nach Eingang/Ausgang gewählt; vorgemerkte oder
widersprüchliche Umsätze werden abgewiesen, alte `Tagessaldo`-Zeilen nicht gebucht.
Unbekannte/mehrdeutige Formate und ungültige Zeilen brechen den gesamten Import
mit verständlicher Meldung ab; keine Rundung oder stille Feldkürzung.

Importidentitäten sind unabhängig von Dateiname, Encoding und Spaltenreihenfolge.
Mehrfach vorhandene identische Buchungen bleiben erhalten. Ohne bankseitige
Transaktions-ID sind überlappende Auszüge mit unterschiedlicher Anzahl ansonsten
identischer Buchungen nicht eindeutig unterscheidbar; diese Grenze darf nicht
als sichere bankübergreifende Dublettenerkennung ausgegeben werden.

CAMT.053 wird für `camt.053.001.02` und `.08` als XML oder ZIP unterstützt,
mit derselben Ausgabe wie CSV. Nur gebuchte EUR-Umsätze; `CRDT` ist positiv,
`DBIT` negativ. Bei Sammelbuchungen müssen die Einzelbeträge exakt die
Gesamtbuchung ergeben, sonst erfolgt keine Übernahme. Datum und Zeitstempel
werden als bankseitiges Buchungs-/Valutadatum übernommen, Gegenparteien und
mehrere unstrukturierte Verwendungszweckzeilen bleiben erhalten. XML-DTDs und
Entitäten, unbekannte Versionen sowie ZIP-Pfadtraversal, Symlinks, verschlüsselte
oder verschachtelte Archive werden abgewiesen. ZIPs werden nur im Speicher
verarbeitet: höchstens 100 Einträge, zusammen höchstens 25 MiB entpackt und
100.000 Umsätze; Auszüge verschiedener Konten dürfen nicht zusammengeführt
werden. Ein Fehler in einer Datei verwirft den gesamten Parserlauf.

Weiterführende Dokumentation:

- [Deployment](DEPLOY.md)
- [Architektur](docs/architecture.md)
- [MVP-Plan und Aufgabenliste](docs/mvp.md)
- [Teilungserklärungen und Gebäudewissen](docs/operations/declaration-imports.md) — Upload, prüfbare Extraktion, freigegebene Gebäuderegeln und Betrieb.
