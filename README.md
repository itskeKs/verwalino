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
wiederholbar. Die Umsatzseite bietet für Schreibrollen „Vorschlag übernehmen“,
„Aufteilen“ und „Ignorieren“. Prüfpflichtige Vorschläge öffnen den Splitdialog;
unklare Beträge müssen dort ausdrücklich vollständig aufgeteilt werden.
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
Status standardmäßig `NEU`, ausdrücklich `ALL` für alle Zustände,
optional `bank_account_id`, `offset`, `limit`
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
zusätzliche `approve`-Berechtigung. Der Splitdialog wählt Mietvertrag oder
Eigentümer des Bankobjekts, Zahlungsart und Periode; Summen werden centgenau
ohne Floatarithmetik geprüft. API-Fehler bleiben sichtbar, doppelte Klicks
erzeugen keine zweite Buchung. „Alle“ zeigt auch zugeordnete/ignorierte Umsätze.
Der echte Browserablauf `scripts/acceptance/t22_bank_assignment_browser.cjs`
prüft Warmmiete, Teilzahlung, Ignorieren, Zweittab-Konflikt und die nach Reload
persistierten Mietkontowerte gegen `scripts/dev-local.sh`.

## Mietänderungen mit Stichtag (Phase 2)

`GET /api/leases/{id}/rent-changes` liefert die chronologische Historie;
`POST` ergänzt eine vollständige Rate aus `valid_from`, `base_rent` und
`advance_payment` (endliche, nichtnegative, centgenaue Dezimalwerte).
Stichtage müssen im Vertragszeitraum liegen und je Vertrag eindeutig sein;
Konflikte liefern 409. Bestehende Vertragsbeträge werden nicht überschrieben,
Historienzeilen sind unveränderlich. Org-/Vertrags-FK, Parent-RLS und vorhandene
Prepare-Rechte gelten; Leser dürfen nur abrufen. Die gemeinsame Finanzsperre
koordiniert Änderungen mit Berechnungen, vor dem Vertrag-Zeilenlock.
Mietkonto und Bankvorschläge laden auch Änderungen vor dem Abfragebeginn.
Bei 700 EUR Kaltmiete und 750 EUR ab 15.03.2026 ergibt März taggewichtet
727,42 EUR, April 750 EUR; NK wird unabhängig ebenso berechnet. Je Komponente
und Monat wird erst die gesamte Tagessumme gerundet. Gebuchte Zahlungen und
finalisierte Snapshots bleiben unverändert. Die Bedienoberfläche folgt in T24.

Weiterführende Dokumentation:

- [Deployment](DEPLOY.md)
- [Architektur](docs/architecture.md)
- [MVP-Plan und Aufgabenliste](docs/mvp.md)
- [Teilungserklärungen und Gebäudewissen](docs/operations/declaration-imports.md) — Upload, prüfbare Extraktion, freigegebene Gebäuderegeln und Betrieb.
