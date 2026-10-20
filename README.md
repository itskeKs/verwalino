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
finalisierte Snapshots bleiben unverändert.
In der Einheit zeigt jeder Vertrag Originalbeträge und Miethistorie. „Miete ändern
ab …“ öffnet den Dialog mit Datum, Kaltmiete und Vorauszahlung; die am gewählten
Datum gültige volle Rate wird vorbelegt. Leser sehen nur den Verlauf. Ungültige
Werte werden vor dem POST abgewiesen, Serverfehler behalten die Eingaben;
während des Speicherns sind weitere Änderungen und Schließen gesperrt.
`scripts/acceptance/t24_rent_change_browser.cjs` prüft den UI-Ablauf und alle
24 Monats-/Typzeilen: 700/150 bis März, 750/150 ab April, Jahres-Soll 10.650 EUR.

## Kautionen (Phase 2)

`/kautionen` zeigt je Vertrag das vereinbarte Soll aus `lease.deposit`, gebuchte
Einzahlungen, echte Rückzahlungen und den gehaltenen Bestand. Dafür liefert
`GET /api/payments/deposits` eine vollständige berechtigte Übersicht; bei
klassifizierten Finanzquellen ist `accounting_scope_id` ausdrücklich auszuwählen.
Der MIET-Kreis begrenzt die Quellen, nicht Jahr oder Kontotyp. Fehlende Rechte,
unklassifizierte oder inkohärente Quellen ergeben einen Fehler statt Teilzahlen.
Unter „Zahlungen“ werden Raten als Typ `KAUTION`, Richtung `IN`, positiv erfasst
und anschließend gebucht; eine echte Rückzahlung ist eine eigene `OUT`-Buchung.
Entwürfe, stornierte Originale und Stornozeilen zählen nicht. 1.800 EUR Soll,
zweimal 900 EUR Eingang und 300 EUR Rückzahlung ergeben 1.500 EUR Bestand.
Kaution bleibt aus dem Mietsaldo und den NK-Ableitungen heraus. Verzinsung oder
eine automatische Rückzahlungs-/Tilgungsregel wird nicht unterstellt; negative
Bestände bei unvollständiger Althistorie bleiben sichtbar. Historische aktive
Kautionen ohne vollständige Vertragszuordnung müssen korrigiert werden (409),
der bestehende Stornopfad bleibt dafür erhalten. Neue/geänderte/zu buchende
Kautionen müssen zu Kontoobjekt, Vertrag und Mieter passen. Buchungsbelege und
finalisierte Dokumente bleiben unverändert. Der echte UI-Prüffall ist
`scripts/acceptance/t25_deposits_browser.cjs`.

## Eigentümerwechsel (Phase 2)

In der Einheit beendet „Eigentum beenden“ eine offene Zuordnung mit dem
inklusiven letzten Eigentumstag (`PATCH /api/ownership/{id}`, nur `valid_to`).
Die Folgeanlage wird ab dem nächsten Tag vorbereitet; der neue Eigentümer muss
bewusst ausgewählt und gespeichert werden. Eigentümer, Einheit, Beginn und MEA
der alten Zeile bleiben erhalten. Schreibrolle und bestehende Prepare-Rechte
sowie die gemeinsame Finanzsperre gelten; Miteigentum bleibt zulässig.
Die Jahresabrechnung lädt ihre Zahlungseingaben über den autorisierten
`GET /api/weg-settlements/{id}/input-owners` für alle Eigentümer im Zeitraum,
nicht nur aus dem Wirtschaftsplan. Unbekannte Zahlungen sind keine Nullwerte:
auch bestätigte Nullen müssen ausdrücklich eingegeben und geprüft werden.
Finalisierte Ergebnisse werden nicht aus heutigen Eigentumsdaten neu berechnet.
`scripts/acceptance/t26_ownership_change_browser.cjs` prüft den Verkauf zum
30.06.2026, echte beleggebundene Jahreskosten von 365 EUR, die Anteile
181/184 EUR, Reload/Finalisierung und den geöffneten, inhaltlich validierten PDF.

## BK-Sammellauf (Phase 2)

Unter „Abrechnungen“ legt „BK-Sammellauf“ nach bewusster Objekt-, Zeitraum- und
Kreiswahl die fehlenden Entwürfe für die überlappenden Mietverträge an.
`POST /api/settlements/batch` nimmt `property_id`, `period_start`, `period_end`
und optional `accounting_scope_id`; die Antwort enthält `created` und `skipped`
(mit Vertrags-/Lauf-ID). Bestehende aktive BK-Läufe desselben Vertrags, Zeitraums
und Kreises werden unverändert übersprungen, auch wenn bereits finalisiert.
Die gemeinsame Finanzsperre verhindert doppelte parallele Sammelläufe; Fehler
rollen sämtliche neuen Entwürfe und Auditzeilen zurück. Org-, Quellen- und
Kreisrechte gelten auch bei leerem Ergebnis; Teilkreise begrenzen die Einheiten.
Es wird weder gerechnet noch finalisiert: tatsächliche Vorauszahlungen bleiben
unbekannt (`null`), bis sie über den bestehenden Ablauf bestimmt werden.
`scripts/acceptance/t27_settlement_batch_browser.cjs` prüft drei UI-Verträge,
drei Entwürfe und die identische Wiederholung ohne neue Läufe.

Weiterführende Dokumentation:

- [Deployment](DEPLOY.md)
- [Architektur](docs/architecture.md)
- [MVP-Plan und Aufgabenliste](docs/mvp.md)
- [Teilungserklärungen und Gebäudewissen](docs/operations/declaration-imports.md) — Upload, prüfbare Extraktion, freigegebene Gebäuderegeln und Betrieb.
