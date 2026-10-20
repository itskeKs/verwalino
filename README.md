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

## Bankumsätze und Dateiimport (Phase 2)

Bankumsätze werden getrennt von gebuchten Zahlungen geführt: Beträge sind
signierte `Decimal`-Werte (Gutschrift positiv, Lastschrift negativ), Importzeilen
sind je Konto über ihren Importhash eindeutig. RLS und Composite-Fremdschlüssel
binden Umsatz, Konto und optionale Zahlungs-Verknüpfung an dieselbe Organisation
und dasselbe Konto. Nach dem Buchen ist auch diese Verknüpfung unveränderlich;
verknüpfte Umsätze sind nicht löschbar.

Unter „Bankkonten“ öffnet „Umsätze“ die kontobezogene Liste mit Statusfilter
und Pagination. „Kontoauszug importieren“ führt für Schreibrollen über Datei,
Vorschau und ausdrückliches „Übernehmen“; nach Dateiwechsel ist eine neue Vorschau
nötig. CSV, CAMT-XML und ZIP sind auswählbar, Fehler und die tatsächliche Anzahl
neuer Umsätze werden angezeigt. READ_ONLY kann lesen, aber nicht importieren.
Der lokale Browserablauf einschließlich zweitem Import mit null neuen Umsätzen
ist mit `scripts/acceptance/t19_bank_import_browser.cjs` gegen `scripts/dev-local.sh`
wiederholbar. Vorschlagsengine und API-Zuordnung sind vorhanden;
die Buchungsoberfläche folgt in T22.
`GET /api/bank-accounts/{id}/transactions` liefert die Liste mit optionalem
`status`, `offset`, `limit` (Vorgabe 50, höchstens 100) und `next_offset`.

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

`POST /api/bank-accounts/{id}/imports` akzeptiert das Multipart-Feld `file`.
Mit `dry_run=true` (Vorgabe) wird nur geprüft: `count`, `new_count`,
`duplicate_count`, `total_amount`, `new_total_amount`, `date_from`/`date_to`
und höchstens 100 `transactions` (`preview_truncated` kennzeichnet den Rest).
Geldwerte werden als exakte Dezimalstrings ausgegeben. `dry_run=false` übernimmt
atomar und nennt die tatsächliche `imported_count`; Wiederholung und parallele
Übernahme erzeugen keine Doppelzeilen. Nur Schreibrollen, nur Konten derselben
Organisation; bekannte Quell-IBANs müssen zur hinterlegten Ziel-IBAN passen.
Ohne Quell-IBAN ist kein automatischer Kontonachweis möglich; das Zielkonto wird
bewusst gewählt. Fehlerhafte Dateien werden vollständig abgewiesen (422),
Datei-/Requestgrößen begrenzt (413). Der Import erzeugt noch keine gebuchten
Zahlungen und verändert deshalb weder Mietsaldo noch finalisierte Dokumente.

Die reine Zuordnungsvorschlagsengine verwendet die bestehenden offenen
Miet-/Eigentümerkonto-Posten. Warmmiete wird in Kaltmiete/NK aufgeteilt, bekannte
fremde IBANs verhindern einen Treffer. Konfidenz und Prüfhinweise bleiben sichtbar
im Ergebnis; mehrdeutige Teilzahlungen erhalten keine erfundene Tilgungsfolge,
sondern unaufgeteilte Beträge zur manuellen Prüfung. Die Engine selbst bucht nicht.

`GET /api/bank-transactions` liefert kontoberechtigte Umsätze mit Vorschlägen,
Status standardmäßig `NEU`, optional `bank_account_id`, `offset`, `limit`
(50/maximal 100), als `{items,next_offset}`. Vorschläge nutzen tatsächliches
Soll/Ist; Hausgeld nur aus einem passenden finalisierten Wirtschaftsplan,
Eigentümerzahlungen anderer Objekte werden nicht eingerechnet.
`POST /api/bank-transactions/{id}/assign` nimmt `{lines:[...]}` mit Zahlungsart,
positivem centgenauem Betrag, Jahr/Monat und Mietvertrag oder Eigentümer.
Die Summe muss den gesamten Umsatz decken. Konto, Objekt, Richtung und
Wertstellung stammen ausschließlich aus dem gesperrten Bankumsatz. Zahlungen
werden atomar gebucht und verknüpft; Fehler rollen auch Audit und Status zurück.
`POST /api/bank-transactions/{id}/ignore` ignoriert ohne Zahlung. Beide Übergänge
gelten nur für `NEU`, Wiederholung/Konkurrenz liefert 409. Schreibrolle sowie bei
gebundenen Konten `prepare` und zum Buchen `confirm` sind erforderlich; keine
zusätzliche `approve`-Berechtigung. Die Bedienoberfläche dafür folgt in T22.

Weiterführende Dokumentation:

- [Deployment](DEPLOY.md)
- [Architektur](docs/architecture.md)
- [MVP-Plan und Aufgabenliste](docs/mvp.md)
- [Teilungserklärungen und Gebäudewissen](docs/operations/declaration-imports.md) — Upload, prüfbare Extraktion, freigegebene Gebäuderegeln und Betrieb.
