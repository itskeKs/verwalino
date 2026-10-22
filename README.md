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

## Staffelmiete (T43)

In der Miethistorie der Einheit erzeugt „Staffelmiete anlegen“ 1–50 volle
Monatsraten ab dem gewählten ersten Erhöhungsdatum. Ein positiver EUR-Betrag
oder Prozentsatz wird je Stufe auf die vorherige Kaltmiete angewendet und mit
Decimal/ROUND_HALF_UP auf Cent gerundet; die Vorauszahlung bleibt unverändert.
Beispiel: 700 EUR + 30 EUR jährlich ab 01.01.2027 ergibt fünf Stufen
730/760/790/820/850 EUR bis 01.01.2031. Kalendertermine bleiben am Starttag
verankert; kurze Monate werden auf ihren letzten Tag begrenzt, ohne Folgedrift.

`POST /api/leases/{id}/rent-changes/staffel` erwartet `staffel_start_date`,
`staffel_interval_months` (12–120), `staffel_steps` (1–50) und genau eines von
`staffel_increase_amount` (EUR-Dezimalstring) oder `staffel_increase_percent`
(Dezimalbruch, `"0.05"` = 5 %). Grundlage ist die letzte Miethistorienzeile vor
Start, sonst der Originalvertrag. Mindestens ein Jahr Abstand ist erforderlich.
Bereits vorhandene Änderungen ab Start liefern 409; ungültige Werte, Betrags-
oder Datumsüberlauf und Stufen nach Vertragsende liefern 422. Die Transaktion
legt entweder alle Stufen an oder keine. Live-Schreib-/Quellenrechte, gemeinsame
Finanzsperre, Audit und unveränderliche Historie gelten wie bei T23.

[§ 557a BGB](https://www.gesetze-im-internet.de/bgb/__557a.html) verlangt
Schriftform und Geldbeträge sowie mindestens ein Jahr unveränderte Miete.
Prozent ist hier nur eine Rechenhilfe; die App ersetzt weder Vereinbarung noch
Prüfung der zulässigen Miethöhe/Mietpreisbremse. Keine neue Datenbankrevision.
`scripts/acceptance/t43_staffelmiete_browser.cjs` prüft den echten UI-Durchstich.

## Indexmiete (T44)

„Indexmiete berechnen“ in der Miethistorie erwartet VPI alt/neu mit gleicher
Destatis-Basis (manuelle Eingabe, kein Abruf) und das Zugangsdatum der Erklärung.
Die schreibfreie Vorschau zeigt `bisherige Kaltmiete × VPI neu / VPI alt`, mit
Decimal/ROUND_HALF_UP auf Cent gerundet; 600 EUR bei 110 → 115 ergeben 627,27 EUR.
Auch Indexsenkungen sind möglich. Die Vorauszahlung wird nicht indexiert.

`POST /api/leases/{id}/rent-changes/index/preview` erwartet `old_index`,
`new_index` (positive endliche Dezimalstrings, höchstens sechs Nachkommastellen)
und `declaration_received_on`. Wirksamkeitsbeginn ist der erste Tag des
übernächsten Monats; mindestens ein Kalenderjahr seit der letzten Änderung der
Kaltmiete ist erforderlich. Eine reine Vorauszahlungsänderung startet diese
Frist nicht neu. Künftige Historienzeilen werden nicht ersetzt (409), Daten
außerhalb des Vertrags und ungültige Berechnungswerte werden abgewiesen (422).

Erst „Als Mietänderung übernehmen“ sendet an `/rent-changes/index` zusätzlich
`expected_base_rent`, `expected_advance_payment` und
`expected_previous_valid_from` aus der Vorschau. Unter der gemeinsamen
Finanz-/Vertragssperre wird neu gerechnet; eine veraltete Vorschau liefert 409.
Die Übernahme nutzt den unveränderlichen T23-Pfad und protokolliert VPI und
angegebenen Zugang im Audit. Live-/Quellenrechte gelten auch für die Vorschau.
Vertragsoriginal, gebuchte Zahlungen und finale Snapshots bleiben unverändert.

[§ 557b BGB](https://www.gesetze-im-internet.de/bgb/__557b.html) verlangt eine
schriftliche Indexvereinbarung und eine Erklärung in Textform. Voraussetzungen
und korrekte VPI-Basis prüft der Betreiber. Das eingegebene Datum belegt keine
Zustellung; die Funktion erstellt/versendet keine Erklärung. Keine Migration.
`scripts/acceptance/t44_indexmiete_browser.cjs` prüft den echten UI-Ablauf.

## HeizkostenV-Rechenengine (T45)

`app.engine.heizkosten.calculate_heating_costs(pools, context=...)` verteilt
getrennt festgelegte Heizungs- und Warmwasserkostenpools. Eingaben/Ergebnisse sind
frozen Dataclasses aus `engine/types.py`; alle Beträge, Anteile und Messmengen
sind Decimal. Verbrauchsquote 50–70 %, Grundquote 30–50 %; bei ausdrücklich
angegebenem §7-Sondergebäudefall sind 70 % Verbrauch Pflicht. Heizungsgrundkosten
nutzen die angegebene Fläche/beheizte Fläche/den Rauminhalt, Warmwassergrundkosten
nur Wohn-/Nutzfläche. Die bestehende Largest-Remainder-Funktion teilt zunächst
Grund-/Verbrauchskosten und danach jede Teilsumme centtreu auf stabile UUIDs auf.

`consumption_recorded=False` kennzeichnet ausdrücklich eine nicht
verbrauchsabhängige Abrechnung: der Pool wird nach der angegebenen Grundbasis
verteilt, im Kontext `TENANT` wird je Nutzer der volle betroffene Kostenanteil
um 15 % gekürzt (Cent-Rundung HALF_UP). Kürzung und Restbetrag werden getrennt
ausgewiesen; kein Kürzungsbetrag wird auf andere Nutzer oder Warmwasserpools
umverteilt. Im expliziten Kontext `WEG_OWNER` erfolgt diese Mieterkürzung nicht.
Fehlende einzelne Werte, NaN/Infinity/Floats, negative Kosten/Mengen, doppelte
Einheiten/Pools oder nicht tragfähige Nullbasen werden laut abgewiesen.

Primärquellen: [§7](https://www.gesetze-im-internet.de/heizkostenv/__7.html),
[§8](https://www.gesetze-im-internet.de/heizkostenv/__8.html),
[§9](https://www.gesetze-im-internet.de/heizkostenv/__9.html) und
[§12 HeizkostenV](https://www.gesetze-im-internet.de/heizkostenv/__12.html).
Die Kostenabgrenzung verbundener Anlagen und Ausnahmevoraussetzungen werden
vorgegeben, nicht aus angenommenen Brennstoffen/Temperaturen hergeleitet;
keine §9a-Schätzung und keine zusätzlichen 3-%-Tatbestände. T45 ist die reine
Engine; T46 verbindet sie nun mit BK, getrennten Zählerquellen und dem PDF.

In der BK-Abrechnung lässt sich je bestätigter Heiz-/Warmwasserposition bewusst
„Intern nach HeizkostenV abrechnen“ aktivieren. Ohne Aktivierung bleibt die externe
Messdienstabrechnung unverändert. Verbrauchsquote, §7-Sonderfall, Grundbasis und
Zählertyp werden ausdrücklich eingegeben; automatische und manuelle Mengen bleiben
von Kaltwasser/anderen Verbrauchsschlüsseln getrennt. Alle teilnehmenden Einheiten,
Mieterwechsel und Leerstandsabschnitte sind sichtbar. Grundkosten werden mit
Fläche/Volumen × Tagen verteilt; Verbrauchskosten nutzen exakte Grenz-/Zwischenablesungen
([§9b](https://www.gesetze-im-internet.de/heizkostenv/__9b.html)) oder ausgewiesene
Handwerte (maximal sechs Nachkommastellen), niemals eine stille Tagesschätzung.
Der Zeitraumanteil einer übergreifenden Kostenquelle wird centtreu vorab abgegrenzt;
bei Teilumlage bleibt der bestätigte Mieterbetrag maßgeblich. Leerstand trägt den
Brutto-Eigentümeranteil ohne Mieterkürzung. Fehlende/mehrdeutige Werte liefern 422.

Berechnen speichert die Eingabeabsicht im vorhandenen Entwurfssnapshot; Reload
stellt sie wieder her. Ohne Requestbody werden gespeicherte Eingaben wiederverwendet,
ein expliziter leerer Request löscht sie. Vorschau speichert nichts. Automatische
Ablesungen werden bis zur Finalisierung frisch aufgelöst; danach stehen Konfiguration,
Messbelege und getrennte Grund-/Verbrauchs-/Kürzungsbeträge unveränderlich im Snapshot.
Das UI-geöffnete PDF liest nur diesen eingefrorenen Rechenweg. Keine neue Migration,
keine behauptete §9-Kostentrennung, §9a-Schätzung oder rechtliche Zertifizierung.
`uv run pytest tests/test_m3_settlement.py -k heiz` prüft den Backendpfad;
`scripts/acceptance/t46_heizkosten_browser.cjs` prüft die reale UI und PDF-Inhalte.

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

## Allgemeine Dokumentenablage (Phase 2)

`/api/attachments` verwaltet Originaldateien zu einem Objekt, optional zusätzlich
zu Einheit, Mietvertrag oder Person. Upload und Archivierung benötigen Prepare-,
Metadaten Read- und Downloads Exportrechte an allen verknüpften Quellen.
Listen sind paginiert und blenden archivierte Einträge standardmäßig aus.
Archivierung mit Begründung ist endgültig und löscht weder Original noch Metadaten;
Ersetzen, Umhängen und Löschen sind nicht vorgesehen.

Dateien bis 25 MiB werden im vorhandenen mandantengetrennten, inhaltsadressierten
DocumentStore aufbewahrt. Downloads prüfen Schlüssel, SHA-256 und Länge und liefern
exakt die geprüften Bytes mit sicheren Dateinamen und `nosniff`. Inline ist nur für
anhand der Signatur erkannte PDF-, PNG- und JPEG-Dateien möglich; andere Inhalte
werden als Download angeboten. Ein fehlgeschlagener Datenbankvorgang rollt
Metadaten und Audit zurück, löscht aber keine möglicherweise gemeinsam referenzierten
Originalbytes. Migration 0130 verweigert einen verlustbehafteten Downgrade bei
vorhandenen Dokumenten.

Der Bereich „Dokumente“ steht in Objekt, Einheit und Person zur Verfügung.
Bei Personen wird für einen Upload ein zugängliches Objekt bewusst ausgewählt;
die Personenliste bleibt objektübergreifend. „Öffnen“ lädt das autorisierte Original,
„Archivieren“ verlangt einen Grund. „Archivierte anzeigen“ macht aufbewahrte Dateien
weiterhin sichtbar und lesbar. Read-only-Benutzer erhalten keine Schreibaktionen.
Der echte UI-/PDF-Prüffall ist `scripts/acceptance/t37_attachments_browser.cjs`.

## Objekt-Jahresübersicht (Phase 2)

`GET /api/reports/property-year?property_id=…&year=2026&accounting_scope_id=…`
liefert Einnahmen je Zahlungstyp, Ausgaben je Kategorie/Umlagekennzeichen und
Saldo als exakte zweistellige Geldstrings. Grundlage sind aktive gebuchte
Zahlungen nach Wertstellung (`IN` positiv, `OUT` negativ im selben Typ) sowie
aktive bestätigte Bruttokosten nach Rechnungsdatum. Entwürfe, Stornoketten,
überholte Kosten und bloß importierte Bankumsätze zählen nicht.

Datumsbasis und fachlicher Hinweis stehen in der Antwort. Kaution und andere
Zahlungstypen bleiben getrennt sichtbar; eine steuerliche Behandlung wird nicht
unterstellt. `TEILWEISE_PRUEFEN` bleibt ein eigenes Kennzeichen. Dies ist eine
nachprüfbare Grundlage, keine vollständige Anlage V oder Steuererklärung.
Der Bericht benötigt vollständige Read-/Source-Rechte und gegebenenfalls einen
expliziten, das ganze Objekt und Jahr abdeckenden WEG-/MIET-Kreis. Fehlende
Klassifikation oder Teilzugriff wird nicht als vollständiger Nullbericht ausgegeben.

Unter **Berichte** (`/berichte`) werden Objekt, Kalenderjahr und gegebenenfalls
Kreis bewusst gewählt. „Übersicht anzeigen“ zeigt die Originalgruppen und
Server-Summen; Auswahlwechsel verwirft alte Ergebnisse. „CSV herunterladen“
verwendet dieselben Filter, benötigt zusätzlich Exportrechte an Kreis und Quellen
und liefert UTF-8 mit BOM, Semikolon, CRLF und Dezimalkomma. Formelverdächtige
Texte werden entschärft, negative Geldwerte bleiben numerisch. Fehler lassen
Eingaben bzw. den bestehenden Bericht erhalten. Historische unbekannte
Zahlungstypen bleiben mit ihrem ursprünglichen Code unterscheidbar.
`scripts/acceptance/t39_property_year_report_browser.cjs` prüft echte UI-Buchungen,
bestätigte Belege und den tatsächlichen Download: 800 − 125 = 675 EUR.

## Aufgaben-API (Phase 2)

`/api/tasks` bietet organisationsgebundenes Anlegen, Lesen, partielles Ändern und
Löschen operativer Aufgaben. Titel ist erforderlich (max. 255 Zeichen), Notiz
maximal 10.000 Zeichen; Fälligkeit ist optional, Erledigen und Wiederöffnen sind
zulässig. Ohne `assigned_user_id` wird die Aufgabe dem aktuellen Benutzer
zugeordnet; neue Zuweisungen benötigen ein aktives Mitglied derselben Organisation.
Später inaktive Zuständige lassen bestehende Aufgaben nicht verschwinden.

Die paginierte Liste liefert `{items,next_offset}` (Standard 50, maximal 100) und
kombiniert Objekt-, Einheits-, Vertrags-, Benutzer-, Erledigt- und `due_until`-Filter.
Die Datumsgrenze ist einschließlich und schließt undatierte Aufgaben aus. Einheit
bzw. Vertrag leiten ihre Eltern serverseitig ab. Zum vollständigen Lösen werden
alle drei Parent-IDs ausdrücklich auf `null` gesetzt. Änderungen benötigen
Prepare-Rechte an alten und neuen Quellen; globale Aufgaben benötigen keinen
Finanzkreis. OwnerAdmin/Clerk schreiben, Readonly liest, unbekannte Live-Rollen
werden abgewiesen. Erfolgsantworten folgen erst nach dem Commit. Diese Aufgaben
sind löschbare operative Daten, keine unveränderlichen Finanzoriginale.

Unter **Aufgaben** (`/aufgaben`) lassen sich Aufgaben filtern, bearbeiten,
erledigen, wieder öffnen und löschen. **Aufgabe anlegen** in Objekt und Einheit
übernimmt den geladenen Kontext, speichert aber erst nach bewusster Bestätigung.
Die Zuständigkeit wird aus tatsächlichen Organisationsmitgliedern ausgewählt;
Readonly-Mitglieder sehen keine Schreibaktionen.

Das Dashboard zeigt offene, datierte Aufgaben bis einschließlich sieben lokaler
Kalendertage in die Zukunft, unabhängig von ihrer Zuständigkeit. Undatierte und
später fällige Aufgaben bleiben in der Aufgabenliste verfügbar. **Erledigen**
entfernt die Aufgabe erst nach erfolgreicher API-Antwort aus dem Widget.
Aufgaben und Stammdaten bleiben unabhängig von finanziellen Fristen sichtbar.
`GET /api/fristen/context` prüft nur, ob eine bewusste Abrechnungskreiswahl nötig
ist; gebundene Finanzdaten werden davor weder als organisationsweite Fristen
abgerufen noch als leere Übersicht dargestellt. Zugriffsfehler bleiben sichtbar.
`scripts/acceptance/t42_tasks_browser.cjs` prüft echte Objekt-/Einheitsaufgaben,
die morgen fällige Aufgabe samt Erledigen und Reload sowie die ausdrückliche
Kreiswahl nach einer echten Kontoanlage.

## Zahlungs- und Kostenlisten als CSV (Phase 2)

Beide Listen bieten **CSV herunterladen** mit denselben aktiven Filtern wie die
Anzeige. Zahlungen unterstützen serverseitig Konto, Vertrag, Mieter, Eigentümer,
Zahlungsart, Periodenjahr und Status; Kosten kombinieren Status **und** Objekt.
`/api/payments/export.csv` und `/api/cost-entries/export.csv` prüfen zusätzlich
Exportrechte an allen ausgewählten Quellen und Kreisen, auch nochmals vor der
Ausgabe. Eine nicht exportierbare Zeile verhindert die gesamte Datei; erlaubte
Teilkreise benötigen keinen künstlichen Ganzobjektzugriff.

UTF-8-BOM, Semikolon, CRLF, Dezimalkomma und Text-Formelschutz gelten wie beim
Jahresbericht. Zahlungsdateien enthalten Originalbetrag und einen getrennten
vorzeichenbehafteten Saldo-Betrag. **Listensaldo** bzw. **Listensumme Brutto**
beziehen sich ausschließlich auf die angezeigten Zeilen; je Filter können das
auch Entwürfe, Stornozeilen oder ersetzte Kosten sein, kein gebuchter Kontostand.
Die Summierung bleibt centgenau ohne JavaScript-Floatarithmetik. Laden,
Filterwechsel und verspätete Antworten können keine veraltete Datei auslösen.
`scripts/acceptance/t40_csv_exports_browser.cjs` prüft echte Downloads mit
650 EUR Zahlungs-Listensaldo und 125 EUR Kosten-Bruttosumme samt Ausschlussfällen.

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

## Künftige NK-Vorauszahlung vorschlagen (Phase 2)

Eine aktive finalisierte BK-Abrechnung zeigt den Vorschlag aus ihrem eingefrorenen
Mieter-Kostenanteil je abgerechneten Monat. Ganze Kalendermonate zählen als eins,
Teilmonate nach belegten Tagen; die Kalenderquote bleibt bis zur abschließenden
kaufmännischen Cent-Rundung exakt. Aktuelle Kosten, Guthaben/Rückstand und gezahlte
Vorauszahlungen ersetzen diese Basis nicht. Fehlende historische Eingaben führen
zu einem Fehler statt einer erfundenen Rate.
`GET /api/settlements/{id}/advance-proposal` liefert Kosten, Monatsbasis und Betrag
als Dezimalstrings. „Übernehmen ab“ und „Vorauszahlung übernehmen“ senden per POST
an dieselbe URL ausschließlich `valid_from`. Die Übernahme verwendet den bestehenden
T23-Pfad: volle Miethistorienzeile mit am Stichtag gültiger Kaltmiete und neuer
Vorauszahlung, ohne Vertragsüberschreibung. Doppelte Stichtage liefern 409;
Schreib-, Quellen- und Kreisrechte gelten, die Historie ist vor HTTP 201 gespeichert.
Finalisierte Snapshots, gebuchte Zahlungen und tatsächliches Ist bleiben unverändert.
`scripts/acceptance/t28_advance_proposal_browser.cjs` prüft 1.200 EUR / 12 = 100 EUR
und das Mietkonto 2027 nach ausdrücklicher Übernahme: 700/100 statt 700/150.

## Sonderumlagen-Backend (Phase 2)

`POST /api/special-levies` legt eine einmalige Sonderumlage mit `property_id`,
explizitem `accounting_scope_id`, positivem centgenauem `amount`, `due_date` und
optionalem `resolution_id` an. Unterstützt werden vollständige WEG-Objektkreise;
unvollständige MEA-/Eigentumsgrundlagen und Teilkreise werden ausdrücklich abgewiesen.
Die Verteilung verwendet die Eigentümer am Fälligkeitstag, bei Miteigentum deren
positive Anteile innerhalb der Einheit, und die bestehende centgenaue Restverteilung.
10.000 EUR auf MEA 300/300/400 ergeben 3.000/3.000/4.000 EUR.

Kopf und Anteile bleiben nach Anlage unveränderlich. Spätere MEA-/Eigentumsänderungen
berechnen alte Umlagen nicht neu. `GET /api/special-levies` liest mit optionalem
Objekt-/Kreisfilter unter tatsächlichen Kreis- und Quellenrechten; unvollständiger
Zugriff wird nicht als vollständige leere Liste ausgegeben. Das bestehende
Eigentümerkonto berücksichtigt den Anteil einmalig als `SONDERZAHLUNG` im
Fälligkeitsmonat, auch ohne Wirtschaftsplan; regulär gebuchte Sonderzahlungen
reduzieren den offenen Betrag. Unter `/sonderumlagen` werden Objekt und Lesekreis
gewählt; Schreibrollen wählen zum Anlegen ausdrücklich den Anlagekreis sowie Betrag
und Fälligkeit. Die Seite zeigt die festgeschriebenen Anteile mit Eigentümer- und
Einheitsnamen und verwendet die vorhandenen Konto-/Mahnungsseiten. Ohne
Wirtschaftsplan kann dort ein WEG-Objekt und dessen Kreis ausdrücklich gewählt werden.
Sonderumlagen werden erst nach ihrem wirklichen Fälligkeitstag (zuzüglich Karenz)
überfällig; Zahlungen desselben Monats werden einmalig auf die älteste Fälligkeit
angerechnet. `scripts/acceptance/t33_special_levies_browser.cjs` prüft die UI-Anlage,
3.000 EUR offen und eine echte planlose Eigentümermahnung samt Reload.

### Einladung zur Eigentümerversammlung (Phase 2)

„Einladung als PDF“ im Versammlungsdetail öffnet
`GET /api/meetings/{id}/invitation.pdf` im Browser. Jede zum Versammlungstermin
berechtigte Eigentümerprofil-Identität erhält eine eigene Seite mit Namen,
Postanschrift, Datum/Ort, vorhandenem Einladungstext und sortierter Tagesordnung.
Mehrere Einheiten desselben Profils erzeugen keine doppelten Briefe; Miteigentümer
mit getrennten Profilen erhalten getrennte Seiten. Objekt- und Kontakt-Exportrechte
werden vor und nach dem Rendern geprüft; ein gesperrter Kontakt erzeugt keine
scheinbar vollständige Teilausgabe. Fehlende Anschriften, leere Empfängerlisten
oder Seitenüberlauf werden mit 422 abgewiesen, nicht erfunden oder abgeschnitten.

Bei weniger als 21 Kalendertagen ab Export-/Lesedatum erscheint der gemeinsame
Backend-Hinweis in UI und PDF (§ 24 Abs. 4 WEG). Er bestätigt weder Versand noch
rechtzeitigen Zugang. Der Export verändert keine Versammlung, Tagesordnung,
Eigentumshistorie oder bestehende Protokolle. Der echte Chromeablauf ist mit
`scripts/acceptance/t34_meeting_invitation_browser.cjs` wiederholbar.

## Zähler-API (Phase 2)

`GET/POST /api/meters` verwaltet Objektzähler und optional einer Einheit
zugeordnete Zähler. Listen können nach `property_id` und `unit_id` gefiltert
werden; `GET/PATCH /api/meters/{id}` liest beziehungsweise korrigiert nur
Nummer, Art und Standort. Objekt und Einheit werden nicht nachträglich umgehängt.
`GET/POST /api/meters/{id}/readings` liefert den chronologischen Verlauf und
fügt einen Stand aus `reading_date`, `value` und optional `period` hinzu.
Messwerte sind nichtnegative, exakte Dezimalstrings mit höchstens zehn Ganzzahl-
und vier Nachkommastellen; Antworten enthalten immer vier Nachkommastellen.
Bestehende Ablesungen werden nicht überschrieben, gleiche Ablesetage sind erlaubt.
Org-Trennung und Objekt-Masterrechte gelten für Listen, Einzelabruf und Schreiben;
Leserecht allein erlaubt keine Änderung, PAUSED erhält nur die zulässigen Lesewege.
Schreibwege verwenden Live-Rollenprüfung, Finanzsperre und Commit vor HTTP-Erfolg.
Das Zählerpanel in Objekt und Einheit (T30) erlaubt Anlegen, Korrigieren von
Beschreibungsfeldern, Erfassen eines Standes und chronologischen Verlauf.
Einheitszähler bleiben fest zugeordnet; Objektlisten umfassen auch deren Zähler.
Deutsche Dezimaleingaben werden exakt normalisiert. „Verbrauch“ zeigt ausschließlich
die Differenz der letzten zwei eindeutigen Ablesetage mit beiden Daten; bei Rückgang
oder mehrdeutigen Ablesetagen ist eine Prüfung statt eines erfundenen Verbrauchs nötig.
READ_ONLY sieht nur den Verlauf. Fehlversuche behalten Eingaben, paralleles Speichern
ist gesperrt und verspätete Antworten wechseln nicht den aktuellen Kontext.
`scripts/acceptance/t30_meters_browser.cjs` prüft UI-Anlage und Reload mit
100,0000 → 250,1250 = 150,1250 sowie einem separaten Einheitszähler.
Die BK-Abrechnung (T31) übernimmt ohne Handwert den exakten Verbrauch aus dem
jeweils eindeutig nächsten Stand innerhalb ±31 Tagen um Anfang und Ende der Periode.
Voraussetzung ist genau ein Einheitszähler mit zur Kostenkategorie passender Art,
z. B. `KALTWASSER`. Fehlende/mehrdeutige Stände, Rückgänge, mehrere Zähler oder
verschiedene Verbrauchsarten verlangen Handeingaben statt Schätzung; Gemeinschafts-
zähler werden nicht als Einheitszähler verwendet. Bewusste Handwerte einschließlich
0 haben Vorrang. Alle teilnehmenden Einheiten brauchen eine vollständige Basis.
`GET /api/settlements/{id}/consumption-suggestions` liefert für Entwürfe autorisierte
Vorschläge mit Stand-IDs, Datum und Wert; die Oberfläche zeigt diese Basis, ohne sie
als Handwert zurückzusenden. Berechnung und Finalisierung lesen unter Finanzsperre
neu; verwendete Messdaten werden im unveränderlichen Snapshot eingefroren.
`scripts/acceptance/t31_meter_consumption_browser.cjs` prüft ausschließlich über die
Oberfläche zwei Einheiten mit 30/70 Verbrauch und 100 EUR Wasserkosten: zwei reale
Abrechnungen ergeben 30/70 EUR einschließlich Reload und Finalisierung.

### Verzugszinsen auf Mahnungen (Phase 2)

Beide Mahnungsformulare verlangen einen ausdrücklich geprüften „Basiszinssatz (%)“.
`HV_DUNNING_BASE_INTEREST_RATE` liefert optional die Vorgabe; ohne Konfiguration
bleibt das Feld leer. Die App behauptet keinen amtlichen oder automatisch aktuellen
Zinssatz. `GET /api/dunning/settings` liefert die Vorgabe für angemeldete Mitglieder.
Eine explizite dezimale Zeichenkette `base_interest_rate` überschreibt sie; alte
API-Aufrufe ohne Angabe und ohne Konfiguration bleiben zinsfrei, explizites `null`
deaktiviert die Berechnung. Bestehende Briefe/PDFs werden dadurch nicht umgerechnet.

Berechnet wird je offenem Posten `Betrag × (Basis + 5) / 100 × Kalendertage / 365`,
ab tatsächlicher Fälligkeit, mit einmaliger `ROUND_HALF_UP`-Cent-Rundung. Gebühren
und Zinsen werden nicht verzinst. Beispiel: 700 EUR, Basis 2,50 %, 60 Tage →
8,63 EUR Zinsen und 708,63 EUR Gesamtbetrag bei Gebühr 0. Fälligkeit, Stichtag,
Tage, Basis-/Jahreszins und Beträge werden gespeichert und in Detail/PDF angezeigt;
spätere Konfigurations- oder Zahlungsänderungen berechnen die ausgestellte Mahnung
nicht neu. Migration 0129 schützt diese Provenienz und verweigert deren Verlust
beim Downgrade. Ausstellung allein bestätigt keinen Zugang. Der echte UI-/PDFablauf
ist mit `scripts/acceptance/t35_dunning_interest_browser.cjs` wiederholbar.

### Mahnlauf mit ausgewählten Entwürfen (T53)

Unter **Offene Posten → Mahnlauf öffnen** Zeitraum, Stichtag, Mindestrückstand
und gegebenenfalls den MIET-Vorbereitungskreis ausdrücklich wählen. Die Vorschau
schreibt nichts und enthält Verträge mit Hauptforderung **größer** als die Schwelle.
Miethistorie, gebuchte Zahlungen und Mahnstufen kommen aus demselben Mietkonto-/
Mahnpfad wie bei Einzelentwürfen. Auswahl und angezeigte Gesamtsumme müssen bestätigt
werden; es entstehen nur Entwürfe, keine Ausstellung, PDFs oder E-Mails.

Zwischenzeitlich geänderte Quellen verlangen eine neue Vorschau (409). Bei unklarer
Netzwerkantwort bleibt derselbe Auftrag für eine manuelle Wiederholung erhalten;
dessen Original-IDs werden unter erneuter vollständiger Prepare-/Quellenprüfung
zurückgegeben, nicht neu berechnet oder dupliziert. Alte ausgestellte Briefe bleiben
unverändert. `scripts/acceptance/t53_dunning_batch_browser.cjs` prüft tatsächlich drei
Rückstände à 850 EUR und genau drei Entwürfe über zusammen 2.550 EUR einschließlich
Mobil-Abbruch, Reload und unveränderter Quelldaten.

### E-Mail-Versand (T47)

Ausgestellte Mahnungen, finalisierte BK-Abrechnungen und personengenau adressierte
Versammlungseinladungen bieten einen ausdrücklichen E-Mail-Versand. Die Adresse
kommt ausschließlich vom berechtigten Kontakt; keine frei eingegebenen Empfänger.
Zusätzlich zu Live-Schreib-, Quellen- und Kontaktexportrechten ist bei gebundenen
Finanzdokumenten die eigene Kreisfähigkeit `send` erforderlich (PDF-Export reicht
nicht). Lesende Rollen können keine Sendung auslösen.

SMTP ist ohne `HV_SMTP_HOST` und `HV_SMTP_FROM` deaktiviert. Weitere Optionen:
`HV_SMTP_PORT` (587), `HV_SMTP_SECURITY` (`starttls`, alternativ `tls`), gemeinsam
`HV_SMTP_USERNAME`/`HV_SMTP_PASSWORD` sowie `HV_SMTP_TIMEOUT_SECONDS` (15).
`plain` ist ausschließlich ohne Anmeldung auf Loopback für lokale Tests zulässig.
Es werden keine Zugangsdaten oder echten Empfänger vorausgesetzt.

Vor SMTP wird ein Versandversuch dauerhaft im bestehenden append-only Audit am
Dokument gespeichert, einschließlich Empfänger, Message-ID und Referenz auf das
exakte PDF im Dokumentenspeicher. Ein Wiederholungsschlüssel verhindert doppelte
Übermittlung derselben Anfrage. Die Historie unterscheidet SMTP-Annahme, Fehler
und unklaren Ausgang; auch ein nach Absturz offen gebliebener Versuch ist **kein**
Nachweis unterbliebenen Versands. Keine automatische Wiederholung bei unklarem
Ausgang. SMTP-Annahme ist **kein Zustell-/Zugangsnachweis**; Fristen und Empfang
bleiben gesondert zu prüfen. „Versand-PDF öffnen“ liest die gespeicherten Bytes mit
Hashprüfung und aktuellen Quellen-/ursprünglichen Kontaktexportrechten, ohne
finalisierte Dokumente neu zu berechnen. Keine Schemaänderung.

`uv run pytest tests/test_mail_service.py` prüft Fake-SMTP und Fach-/Rechtepfade.
`scripts/acceptance/t47_mail_browser.cjs` betreibt nur einen lokalen SMTP-Empfänger
auf `127.0.0.1:11025` ohne Weiterleitung und prüft den tatsächlichen UI-/PDF-Ablauf.

### Briefvorlagen und Rundschreiben (T48)

Unter **Briefe** lassen sich Klartextvorlagen mit `{{mieter.name}}`,
`{{mieter.adresse}}`, `{{objekt.name}}`, `{{objekt.adresse}}` und `{{datum}}`
speichern und bearbeiten. Objekt, Vorlage und Stichtag ausdrücklich wählen und
„Jetzt erstellen“ bestätigen: Alle an diesem Tag aktuellen Mieter erhalten je
Person ein eigenes PDF, auch bei mehreren Verträgen nur einmal. Unbekannte
Platzhalter werden abgelehnt; HTML und Skripte bleiben gedruckter Klartext.

Vollständige Objekt-/Historien- und Kontaktexportrechte sind erforderlich;
ein verdeckter Empfänger blockiert die gesamte Ausstellung. Leser können keine
Vorlage ändern oder Briefe erstellen. Wiederholung derselben Anfrage erzeugt
keine Duplikate. Die Historie öffnet gespeicherte, hashgeprüfte Originalbytes;
spätere Vorlagen- oder Namensänderungen verändern diese nicht. Kein automatischer
Versand und kein Zustellnachweis. Migration **0132** verweigert einen Downgrade
mit gespeicherten Vorlagen oder Briefen. Tests: `uv run pytest tests/test_letters.py`;
echte UI-/PDF-Abnahme: `scripts/acceptance/t48_letters_browser.cjs`.

### DATEV-Buchungsstapel (T49)

Unter **Berichte → DATEV-Export öffnen** Objekt, Zeitraum und gegebenenfalls
Ganzobjekt-Finanzkreis auswählen. SKR03 oder SKR04, Berater-/Mandantennummer,
Wirtschaftsjahresbeginn, Sachkontenlänge und alle Kontenzuordnungen ausdrücklich
angeben; es gibt keine vorbelegten realen Konten. Kosten werden je Kategorie
gegen ein eigenes Verrechnungskonto gebucht, Bankbewegungen gegen den gewählten
Zahlungstyp-Gegenposten. So darf die Bezahlung einer Rechnung nicht nochmals
unmittelbar als Aufwand zugeordnet werden. Die fachlich geprüften Konten müssen
**ohne Steuerautomatik** arbeiten; keine Umsatzsteueraufteilung oder BU-Schlüssel.

Die Vorschau zeigt Einzelposten und exakte Summen. Der echte EXTF-Download
(Header 700, Buchungsstapel 13, UTF-8-BOM, Semikolon/CRLF) prüft Quellen und
Exportrechte erneut. Konten- oder Quellenwechsel verlangt eine neue Vorschau.
Gebuchte aktive Zahlungen zählen nach Wertstellung, bestätigte aktuelle Kosten
nach Rechnungsdatum; Stornopaare, ersetzte Kosten und Rohbankumsätze werden nicht
zusätzlich exportiert. Nullkosten erzeugen keine verbotene Nullbuchung.

**Vollständige aktuelle Bestandsausgabe, kein Differenzexport:** Dateien nicht
wiederholt oder überlappend importieren; nach Korrekturen ist die Abstimmung im
Zielsystem erforderlich. Eine heruntergeladene Datei ist kein erfolgreicher
DATEV-Import. Kein Upload, keine Änderung von Originalbuchungen und keine neue
Migration. Kontenkonfiguration wird pro Ausgabe ausdrücklich eingegeben, nicht
als globale Kategoriezuordnung gespeichert. Tests:
`uv run pytest tests/test_datev_export.py`, reale Q1-Abnahme:
`scripts/acceptance/t49_datev_browser.cjs`.

### SEPA-Monatsmiete (T50)

Unter **Zahlungen → SEPA-Lastschrift öffnen** ein laufendes Objektkonto und den
berechtigten MIET-Finanzkreis ausdrücklich auswählen. Gläubiger-ID, Name, Ort/Land
und die bestehende Bankvereinbarung erfassen; dann je Mieter ein tatsächlich
unterschriebenes, extern aufbewahrtes CORE-Mandat registrieren. Kontakt-IBAN,
Unterschriftsdatum, eindeutige Referenz, FRST/RCUR und gegebenenfalls letzte
**bei der Bank vorgelegte** Nutzung ausdrücklich bestätigen. Ein Download zählt
nicht als Nutzung; auch zurückgewiesene/zurückgegebene Einzüge gehören zur Historie.
Namens-/IBAN-Wechsel und Verarbeitungssperren blockieren die Verwendung.

Monat, Einzugsdatum, eindeutige Nachrichten-ID und tatsächlich erfolgte
Vorabankündigung angeben (14 Tage oder ausdrücklich vereinbarte kürzere Frist).
Die Vorschau verwendet das vorhandene Monats-Soll einschließlich Miethistorie,
NK-Vorauszahlung und tagegenauer Teilmonate. Ohne gültiges Mandat wird kein Mieter
stillschweigend ausgelassen. Der Download prüft Quellen/Exportrechte erneut und
validiert **pain.008.001.08** gegen das unveränderte EPC-CORE-XSD. Nicht-EWR-Banken
benötigen ausdrücklich eingegebene BICs; Bankprofil und Annahmefähigkeit extern prüfen.

**Nur Datei, keine Übertragung:** keine Zahlung/Buchung, kein automatischer Wechsel
FRST→RCUR und kein Bankannahme-Nachweis. Wiederholte Downloads/Uploads können
Doppeleinzüge auslösen; IDs und Einzugsverarbeitung außerhalb der App abstimmen.
Keine vorgegebenen echten Bankdaten oder Mandate. Migration **0133** schützt
Mandatsidentitäten und verweigert einen Downgrade mit gespeicherten SEPA-Daten.
Tests: `uv run pytest tests/test_sepa_export.py`; UI-only-Abnahme mit isoliertem
Launcher: `HV_PYTHON="$PWD/backend/.venv/bin/python" node scripts/acceptance/t50_sepa_browser.cjs`.

### CSV-Stammdatenimport (T51)

Unter **Objekte → CSV-Stammdaten importieren** die CSV-Vorlage tatsächlich herunterladen
und als UTF-8-Datei (BOM erlaubt), mit Semikolon und unveränderten Spalten befüllen.
`property`, `unit` und `person` erzeugen neue Objekte, Einheiten und Kontakte;
`property_key` verweist auf einen lokalen Objektschlüssel oder eine berechtigte
bestehende Objekt-UUID. Flächen bleiben exakte Dezimalwerte, auch `60,25`.
Maximal 500 Zeilen bzw. 256 KiB; unbekannte Spalten, ungültige Werte und Dubletten
werden mit Zeile/Feld gemeldet. Keine automatisch angelegten Profile oder Verträge.

Die Vorschau schreibt nichts. Erst nach Prüfung, ausdrücklicher Freigabe des
Datenbereichs (falls erforderlich) und Bestätigung wird der gesamte Import atomar
angelegt. Es gibt keine stillen Selbstfreigaben. Änderungen verlangen eine neue
Vorschau; Wiederholung derselben Anfrage liefert dieselben IDs, nach Rechteentzug
keine Teilantwort. Unklare Netzwerkfehler mit **Import erneut versuchen** fortsetzen,
nicht durch neue Anfragen duplizieren. Revision 0134 hält nur unveränderliche,
benutzergebundene Ergebnis-IDs, nicht die CSV oder Kontaktdaten im Batch.
Tests: `uv run pytest tests/test_master_import.py`; echter UI-/Downloadablauf:
`node scripts/acceptance/t51_master_import_browser.cjs`.

### Monatliche Mietkennzahlen (T52)

Im Dashboard unter **Mietkennzahlen** Objekt, Mietmonat/Jahr und gegebenenfalls den
vollständigen MIET-Abrechnungskreis auswählen. Soll umfasst Kaltmiete und
Nebenkostenvorauszahlung einschließlich Miethistorie und Teilmonaten. Ist zählt
aktive gebuchte KALTMIETE-/NK-Zahlungen nach zugeordnetem Mietmonat, nicht nach
Wertstellung; Auszahlungen werden abgezogen. Nicht zugeordnete Zahlungen werden
separat gemeldet, nicht still einem Monat zugeschlagen.

Rückstände sind positive Nettosalden je Vertrag seit Vertragsbeginn bis zum
gewählten Monatsende; Guthaben eines anderen Vertrags gleichen sie nicht aus.
Leerstand bewertet den heutigen Einheitsbestand zum gewählten Monatsende, nicht
einen versionierten historischen Objektbestand. Fehlende Rechte, unvollständige
Historienabdeckung oder inkohärente Buchungen ergeben Fehler statt falscher Nullen.
Die Übersicht verändert keine Buchungen oder Verträge. Pflichtprüfung:
`uv run pytest tests/test_dashboard_kpis.py`; echte UI-Abnahme:
`node scripts/acceptance/t52_dashboard_kpis_browser.cjs`.

Weiterführende Dokumentation:

- [Deployment](DEPLOY.md)
- [Architektur](docs/architecture.md)
- [MVP-Plan und Aufgabenliste](docs/mvp.md)
- [Teilungserklärungen und Gebäudewissen](docs/operations/declaration-imports.md) — Upload, prüfbare Extraktion, freigegebene Gebäuderegeln und Betrieb.
