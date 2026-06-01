# Software Design Document: Hausverwaltungssoftware MVP

Stand: 2026-06-01 · Version 0.2

> Änderungen ggü. v0.1 (2026-05-10): Auslieferung als reine **Responsive Web-App** festgeschrieben (native Clients gestrichen). Neue Phase-1-Bausteine: **E-Rechnung-Empfang**, **Vermögensbericht (§ 28 IV WEG)**, **GoBD-konforme Belegablage**, **DSGVO-Konzept**, **SEV als Verwaltungsart**, **Beschluss-Sammlung & Eigentümerversammlung**, **Mahnwesen / offene Posten**, **Kaltmiete & Mietkonto**. Zielnutzer auf **Profi-Verwalter mit Mandantentrennung** präzisiert. Abrechnungsengine um Abrechnungsspitze, Leerstand und CO2-Aufteilung ergänzt.

## 1. Zweck

Dieses Dokument beschreibt das MVP einer **responsiven, webbasierten** Hausverwaltungssoftware für Mietverwaltung, WEG-Verwaltung, Sondereigentumsverwaltung und Mischobjekte. Das System soll keine vollständige Finanzbuchhaltung ersetzen. Es dient dazu, Stammdaten, Kosten, Vorauszahlungen, Zahlungseingänge und Abrechnungen strukturiert zu erfassen und daraus nachvollziehbare Betriebskostenabrechnungen, Hausgeldabrechnungen, Wirtschaftspläne und Vermögensberichte zu erzeugen.

Der wichtigste fachliche Anspruch ist **Abrechnungssicherheit**: Automatisierung darf Vorschläge machen, aber rechtlich relevante Buchungen und Abrechnungen werden in Phase 1 erst nach manueller Bestätigung übernommen.

Das System richtet sich an **professionelle Hausverwaltungen, die mehrere Mandate fremder Eigentümer bzw. Eigentümergemeinschaften betreuen**. Mandantentrennung und Vermögenstrennung sind daher von Beginn an verpflichtende Architekturmerkmale.

## 2. MVP-Ziele

- Verwaltung von Objekten, Einheiten, Eigentümerinnen/Eigentümern und Mieterinnen/Mietern.
- Abbildung von reinen Mietobjekten, reinen WEG-Objekten, Sondereigentumsverwaltung (SEV) und Mischobjekten.
- Mandantenfähigkeit mit Vermögenstrennung je Verwaltungseinheit und einfachem Rollenmodell.
- Erstellung von Betriebskostenabrechnungen für Mieter.
- Erstellung von Hausgeld-/Eigentümerabrechnungen für WEG-Objekte (WEMoG-konform als Abrechnungsspitze).
- Erstellung des **Vermögensberichts** nach § 28 Abs. 4 WEG.
- Erstellung eines Wirtschaftsplans mit daraus berechneten Hausgeldvorschlägen.
- Verwaltung von Kaltmiete, Nebenkostenvorauszahlungen und einem einfachen Mietkonto.
- Manuelle Erfassung von Kosten, Vorauszahlungen und Zahlungseingängen.
- **Offene-Posten-Übersicht und einfaches Mahnwesen** (Soll-Ist je Person, Mahnstufen).
- Assistierter Rechnungsimport: **PDF mit Extraktionsvorschlägen** und **strukturierter E-Rechnung-Empfang (XRechnung/ZUGFeRD)**.
- **GoBD-konforme, revisionssichere Belegablage.**
- Führung der **Beschluss-Sammlung** und einfache Verwaltung der **Eigentümerversammlung**.
- Fester Standardkontenplan für Phase 1.
- Feste, saubere PDF-Ausgabevorlagen für Abrechnungen.
- Fristen- und Pflichtenüberwachung (z. B. 12-Monats-Frist BK, Abrechnungsfristen WEG).

## 3. Nicht-Ziele für Phase 1

- **Keine nativen Desktop- oder Mobile-Apps.** Die Auslieferung erfolgt ausschließlich als responsive Web-App. Mobile Nutzung geschieht über den Browser.
- Keine vollständige Finanzbuchhaltung mit DATEV-Logik, Kontenrahmen, Bilanz oder GuV.
- Kein Self-Service-Portal mit Login für Eigentümer oder Mieter (Verwalter erzeugt und versendet Dokumente).
- Kein frei konfigurierbares, feingranulares Rechtesystem auf Einzelobjekt-Ebene (es gibt Mandantentrennung und wenige feste Rollen, siehe Abschnitt 4).
- Kein Bankimport (CAMT/MT940) und kein automatischer Zahlungsabgleich.
- Kein frei konfigurierbarer Kontenplan.
- Kein visueller PDF-Template-Builder.
- Keine automatische Buchung von OCR-/KI-/E-Rechnungs-Ergebnissen ohne Freigabe.
- Keine automatische E-Rechnungs-Ausstellung (B2B-Ausstellungspflicht gilt erst gestaffelt; im MVP nur Empfang).
- Keine Online-Eigentümerversammlung/Abstimmung; nur Erfassung und Dokumentation.

## 4. Zielnutzer, Mandantenfähigkeit und Rollen

Primärer Nutzer ist eine **professionelle Hausverwaltung**, die viele Objekte fremder Eigentümer und Eigentümergemeinschaften betreut.

**Verwaltungseinheit (Organization):** Oberste Mandanten-/Isolationsgrenze. Alle Stammdaten, Belege, Konten und Abrechnungen sind eindeutig einer Organization zugeordnet. Datenzugriffe sind hart auf die Organization des angemeldeten Nutzers begrenzt.

**Mandat (ManagementMandate):** Geschäftliche Beauftragung innerhalb einer Organization. Ein Mandat verbindet die Verwaltung mit einem verwalteten Kontext und kennt eine Laufzeit:

- WEG-Mandat: Verwaltung einer gesamten Eigentümergemeinschaft.
- Miet-Mandat: Verwaltung im Auftrag eines einzelnen Eigentümers.
- SEV-Mandat: Verwaltung einer einzelnen vermieteten Einheit innerhalb einer fremden WEG.

**Vermögenstrennung:** Gelder und Belege unterschiedlicher Mandate/WEGs werden niemals vermischt. Jede WEG bzw. jeder Auftraggeber hat eigene Konten und eine eigene Abrechnungssphäre (Fremdgeld-/Treuhandtrennung).

**Rollen (fest, nicht frei konfigurierbar):**

| Rolle | Rechte |
| --- | --- |
| Inhaber/Admin | Vollzugriff inkl. Nutzer-, Rollen- und Mandatsverwaltung |
| Sachbearbeiter | Operative Erfassung, Abrechnung, Belegfreigabe; keine Nutzerverwaltung |
| Nur-Lesen | Lesender Zugriff, z. B. für Beirat, Wirtschaftsprüfer, Vertretung |

Feinere, mandatsbezogene Zugriffsbeschränkungen innerhalb einer Organization sind ein bewusster späterer Ausbau.

## 5. Objektarten

Ein Objekt kann in Phase 1 eine der folgenden Verwaltungsarten haben:

| Objektart | Beschreibung | Abrechnungen |
| --- | --- | --- |
| Mietobjekt | Ein Objekt im Eigentum eines Vermieters, bei dem nur Mieterabrechnungen relevant sind. | Betriebskostenabrechnung |
| WEG-Objekt | Ein Objekt mit Eigentümerstruktur, MEA, Hausgeld, Wirtschaftsplan und Jahresabrechnung. | Hausgeldabrechnung, Wirtschaftsplan, Vermögensbericht |
| SEV-Einheit | Eine einzelne vermietete Eigentumswohnung in fremder WEG, verwaltet im Auftrag des Sondereigentümers. | Betriebskostenabrechnung für den Mieter; Weiterreichung der WEG-Hausgeldabrechnung |
| Mischobjekt | WEG-Objekt, in dem einzelne Einheiten vermietet sind. | Hausgeldabrechnung und Betriebskostenabrechnung |

Wichtig: Ein Mischobjekt bleibt fachlich ein Objekt. Es wird nicht künstlich in ein WEG-Objekt und ein Mietobjekt aufgeteilt. Die Einheit verbindet beide Ebenen: Sie hat immer eine Eigentümerzuordnung und optional eine Mietvertragszuordnung.

Die SEV-Einheit ist der Sonderfall, in dem die eigene Organization nicht die WEG verwaltet, sondern nur das Sondereigentum eines einzelnen Eigentümers. Die WEG-Hausgeldabrechnung kommt dann als externes Dokument von der fremden WEG-Verwaltung herein und bildet eine Kostengrundlage für die Mieterabrechnung.

## 6. Kernmodule

### 6.1 Objekt- und Einheitenverwaltung

Das System verwaltet Gebäude und deren Einheiten. Eine Einheit kann Wohnung, Gewerbeeinheit, Stellplatz oder sonstige Einheit sein.

Wichtige Felder:

- Objektname und Adresse
- Verwaltungsart: Mietobjekt, WEG-Objekt, SEV-Einheit, Mischobjekt
- Abrechnungszeitraum
- Einheiten mit Nummer, Lage, Fläche, beheizter Fläche, Nutzungsart und MEA
- optionale Verteilungsbesonderheiten pro Einheit
- optionale Zähler je Einheit/Objekt (siehe 6.2.1)

### 6.2 Personenverwaltung

Das System verwaltet Eigentümer, Mieter und Dienstleister/Lieferanten getrennt, da diese Rollen unterschiedliche Logiken haben.

Eigentümer:

- Kontaktdaten
- Bankdaten optional
- Eigentumshistorie je Einheit mit Start- und Enddatum
- MEA bzw. Anteil je Einheit

Mieter:

- Kontaktdaten
- Mietvertrag je Einheit
- Vertragsbeginn und Vertragsende
- Kaltmiete und Nebenkostenvorauszahlung
- optional Kaution und Notizen

Dienstleister/Lieferanten:

- Stammkontakt (Name, Adresse, Bankdaten, Steuer-/USt-ID optional)
- Verknüpfung mit Kostenpositionen und E-Rechnungen (Lieferantenerkennung)

#### 6.2.1 Zähler und Ablesungen

Für verbrauchsbasierte Verteilung verwaltet das System optional Zähler (z. B. Kaltwasser, Strom Allgemein) mit Ablesungen (Datum, Stand, Periode). Heiz- und Warmwasserabrechnungen werden in Phase 1 weiterhin extern erstellt und als Werte/Datei importiert (siehe 6.9 und 9).

### 6.3 Bankkonten

Das System verwaltet je Mandat bzw. WEG die zugehörigen Konten als Stammdaten, ohne Kontoauszugsimport in Phase 1:

- WEG-/Mietkonto (laufendes Konto)
- separates Rücklagenkonto (Erhaltungsrücklage)
- optional Kautionskonto

Konten sind eindeutig einem Mandat/einer WEG zugeordnet (Vermögenstrennung). Zahlungseingänge und -ausgänge werden in Phase 1 manuell gegen diese Konten erfasst.

### 6.4 Kosten- und Rechnungserfassung

Kosten werden als Kostenpositionen erfasst. Eine Kostenposition kann manuell eingegeben, aus einem hochgeladenen PDF vorgeschlagen oder aus einer strukturierten E-Rechnung übernommen werden.

Wichtige Felder:

- Objekt (und ggf. Mandat)
- Kategorie aus Standardkontenplan
- Lieferant/Rechnungsaussteller (verknüpft mit Dienstleister-Stammdaten)
- Rechnungsdatum
- Leistungszeitraum von/bis
- Bruttobetrag (und USt-Betrag/-Satz, wenn aus E-Rechnung verfügbar)
- optional Belegnummer
- optional PDF-Datei und/oder E-Rechnungs-Datensatz
- Verteilerschlüssel für Eigentümerabrechnung
- Verteilerschlüssel für Mieterabrechnung
- Umlagefähigkeit
- Status der Belegherkunft und Freigabe (siehe 9)

### 6.5 E-Rechnung-Empfang

Seit 01.01.2025 müssen inländische B2B-Empfänger E-Rechnungen empfangen und verarbeiten können. Das System nimmt **XRechnung (XML)** und **ZUGFeRD (PDF mit eingebettetem XML, ab Profil EN-16931)** entgegen.

Vorteil gegenüber dem PDF/OCR-Weg: Felder wie Lieferant, Rechnungsnummer, Datum, Beträge, USt und Leistungszeitraum kommen strukturiert aus dem Datensatz; es ist kein Extraktions-Rateschritt nötig. Das System mappt diese Felder auf eine Kostenposition im Status `draft_extracted` bzw. `needs_review` und schlägt wie beim PDF-Import Kategorie, Umlagefähigkeit und Verteilerschlüssel vor. Auch hier gilt: keine automatische, finale Buchung ohne Freigabe.

### 6.6 Belegarchiv und GoBD-Konformität

Alle Belege (PDF, E-Rechnungs-XML, importierte externe Abrechnungen) werden revisionssicher abgelegt:

- Unveränderbarkeit: Ein freigegebener Beleg wird nicht überschrieben; Korrekturen erzeugen eine neue Version mit Bezug zum Original.
- Integrität: Prüfsumme (z. B. SHA-256) je Beleg.
- Nachvollziehbarkeit: Änderungs-/Freigabe-Historie (wer, wann, was) je Beleg und Kostenposition.
- Aufbewahrung: mindestens **8 Jahre** (Stand BEG IV ab 2025), maschinell lesbar und auffindbar.
- Verfahrensdokumentation: Der Belegfluss (Eingang → Vorschlag → Freigabe → Abrechnung) ist dokumentiert.

### 6.7 Miet- und Zahlungsverwaltung

Das System erfasst je Mietverhältnis Kaltmiete und Nebenkostenvorauszahlung und führt ein einfaches Mietkonto (Soll-Stellung je Periode, erfasste Zahlungen, Saldo).

Zahlungseingänge werden in Phase 1 manuell erfasst und können zugeordnet werden zu:

- Mieter: Kaltmiete
- Mieter: Nebenkostenvorauszahlung
- Mieter: Kaution
- Eigentümer: Hausgeldvorauszahlung
- Eigentümer: Rücklagenanteil
- Eigentümer: Sonderumlage / Sonderzahlung / Nachzahlung

Das System berechnet für Abrechnungen die Differenz zwischen Soll-Anteil und bereits erfassten Zahlungen und führt eine **Offene-Posten-Liste** je Person.

### 6.8 Mahnwesen

Auf Basis der offenen Posten unterstützt das System ein einfaches, mehrstufiges Mahnwesen:

- Erkennung überfälliger Soll-Stellungen (Miete, Hausgeld, Nachzahlung)
- Mahnstufen (z. B. Zahlungserinnerung, 1./2. Mahnung)
- Erzeugung von Mahnschreiben als PDF
- Vermerk von Mahnhistorie und Status je offenem Posten

Kein automatischer Bankabgleich; Zahlungen werden manuell verbucht und gleichen offene Posten aus.

### 6.9 Abrechnungsengine

Die Abrechnungsengine verarbeitet Kosten, Zeiträume, Personenhistorien, Verteilerschlüssel und Zahlungen.

Sie erzeugt:

- Betriebskostenabrechnung für Mieter
- Hausgeld-/Eigentümerabrechnung für WEG-Objekte
- Wirtschaftsplan mit Hausgeldvorschlägen
- Datengrundlage für den Vermögensbericht (siehe 11)

Verbindliche Regeln:

- **Chronologische Integrität:** Alte Eigentümer- und Mietverhältnisse dürfen nicht überschrieben werden. Bei Eigentümer- oder Mieterwechsel innerhalb eines Abrechnungszeitraums müssen anteilige Zeiträume berechnet werden.
- **Abrechnungsspitze (WEMoG):** Die WEG-Jahresabrechnung weist die Differenz zwischen den im Wirtschaftsplan beschlossenen Soll-Vorschüssen und den tatsächlichen Kosten als Abrechnungsspitze aus. Beschlussgegenstand ist die Anpassung (Nachschuss/Guthaben), nicht die gesamte Abrechnung.
- **Leerstand:** Anteile leerstehender Einheiten trägt der Eigentümer; die Verteilung muss Leerstand sauber abbilden.
- **CO2-Kostenaufteilung:** Für die Aufteilung nach CO2KostAufG (10-Stufen-Modell, abhängig von kg CO₂/m²·a) wird der Mieter-/Vermieteranteil entweder aus der externen Heizkostenabrechnung übernommen oder anhand eingegebener Kennwerte berechnet. Nur der Mieteranteil ist umlagefähig.
- **Heizkosten:** Heiz-/Warmwasserkosten werden in Phase 1 als extern erstellte Werte je Einheit übernommen (HeizkostenV-konform durch den Messdienst); das System weist diese als externe Abrechnung aus.

### 6.10 Eigentümerversammlung und Beschluss-Sammlung

Für WEG-Mandate führt das System:

- **Eigentümerversammlung:** Einladung mit Tagesordnung, Teilnehmer/Stimmrechte (nach MEA oder Kopfprinzip), Protokoll, gefasste Beschlüsse mit Abstimmungsergebnis.
- **Beschluss-Sammlung (§ 24 Abs. 7 WEG):** fortlaufend nummerierte, datierte Sammlung der Beschlüsse mit Ort/Datum der Versammlung und Vermerk bei Anfechtung/Aufhebung. Pflicht für jede WEG.

Online-Abstimmung oder virtuelle Versammlung ist nicht Teil von Phase 1.

## 7. Standardkontenplan Phase 1

Der MVP nutzt einen festen Standardkontenplan. Eigene Kategorien werden in Phase 1 nicht angelegt. Jede Kategorie bringt Defaults für Umlagefähigkeit und Verteilerschlüssel mit. Diese Defaults können pro Kostenposition manuell angepasst werden, wenn ein Sonderfall vorliegt.

### 7.1 Umlagefähige Betriebskosten

Diese Kosten sind grundsätzlich für Betriebskostenabrechnungen vorgesehen, sofern sie mietvertraglich vereinbart und im konkreten Fall laufende Betriebskosten sind.

| Kategorie | Mieter-BK Default | Eigentümerabrechnung | Standard-Verteilung |
| --- | --- | --- | --- |
| Grundsteuer / öffentliche Lasten | umlagefähig | ja | Fläche oder MEA |
| Kaltwasser | umlagefähig | ja | Verbrauch oder Fläche |
| Entwässerung / Abwasser | umlagefähig | ja | Verbrauch oder Fläche |
| Heizung zentral | umlagefähig extern/prüfen | ja | externe Abrechnung oder Heizkostenlogik |
| Warmwasser zentral | umlagefähig extern/prüfen | ja | externe Abrechnung oder Heizkostenlogik |
| Müllbeseitigung | umlagefähig | ja | Einheit, Personen oder Fläche |
| Straßenreinigung | umlagefähig | ja | Fläche oder MEA |
| Winterdienst | umlagefähig | ja | Fläche oder MEA |
| Gebäudereinigung | umlagefähig | ja | Fläche |
| Gartenpflege | umlagefähig | ja | Fläche oder MEA |
| Allgemeinstrom / Beleuchtung | umlagefähig | ja | Fläche oder MEA |
| Schornsteinreinigung | umlagefähig | ja | Einheit, Fläche oder externe Abrechnung |
| Gebäudeversicherung | umlagefähig | ja | Fläche oder MEA |
| Haftpflichtversicherung | umlagefähig | ja | Fläche oder MEA |
| Aufzug Betrieb/Wartung | umlagefähig | ja | Einheit oder Fläche |
| Waschraum / Gemeinschaftswascheinrichtung | umlagefähig | ja | Verbrauch, Einheit oder Fläche |

### 7.2 Gemischte oder prüfpflichtige Kosten

Diese Kategorien dürfen nicht blind vollständig auf Mieter umgelegt werden.

| Kategorie | Mieter-BK Default | Eigentümerabrechnung | Hinweis |
| --- | --- | --- | --- |
| Hausmeister / Hauswart | gemischt_prüfen | ja | Verwaltungs-, Reparatur- und Instandhaltungsanteile herausrechnen |
| Sonstige Betriebskosten | manuell_prüfen | ja | nur bei konkreter mietvertraglicher Grundlage und laufenden Kosten |
| Kabel / Breitband / Gemeinschaftsantenne | manuell_prüfen | ja | seit 2024 eingeschränkte Umlagefähigkeit |
| Messdienst / Abrechnungsdienst | umlagefähig_extern/prüfen | ja | z. B. Heizkosten/Wasser, keine Doppelabrechnung |
| CO2-Kosten | teilweise_prüfen | ja | nur Mieteranteil umlagefähig (10-Stufen-Modell) |

### 7.3 Nicht umlagefähige Eigentümer-/WEG-Kosten

Diese Kosten erscheinen in der Eigentümer-/Hausgeldabrechnung, aber nicht in der Betriebskostenabrechnung für Mieter.

| Kategorie | Mieter-BK Default | Eigentümerabrechnung | Hinweis |
| --- | --- | --- | --- |
| WEG-Verwalterkosten | nicht_umlagefähig | ja | Verwaltungskosten |
| Buchhaltung / Jahresabrechnung | nicht_umlagefähig | ja | Verwaltungskosten |
| Eigentümerversammlung | nicht_umlagefähig | ja | Verwaltungskosten |
| Bankgebühren WEG-Konto | nicht_umlagefähig | ja | Verwaltungskosten/Admin-Kosten |
| Rechtsberatung / Prozesskosten | nicht_umlagefähig | ja | regelmäßig Eigentümer-/WEG-Kosten |
| Instandhaltung | nicht_umlagefähig | ja | keine Betriebskosten |
| Instandsetzung / Reparaturen | nicht_umlagefähig | ja | keine Betriebskosten |
| Erhaltungsrücklage / Instandhaltungsrücklage | nicht_umlagefähig | ja | getrennt ausweisen |
| Sonderumlage | nicht_umlagefähig | ja | Zweck prüfen, Default owner-only |

### 7.4 Reine Mietobjekte

Bei reinen Mietobjekten werden in Phase 1 nur Kosten erfasst, die für die Betriebskostenabrechnung relevant sind. Nicht umlagefähige interne Vermieterkosten wie Reparaturen, Verwaltung oder Instandhaltung werden für reine Mietobjekte im MVP nicht erfasst.

## 8. Verteilerschlüssel

Das System unterstützt in Phase 1 folgende Verteilerschlüssel:

- MEA
- Wohn-/Nutzfläche
- beheizte Fläche
- Einheit
- Personenanzahl
- Verbrauch
- externe Abrechnung pro Einheit
- manueller Betrag pro Einheit

Eigentümerabrechnung und Mieterabrechnung können für dieselbe Kostenposition unterschiedliche Verteilerschlüssel haben.

Beispiel: Eine WEG-Rechnung wird in der Eigentümerabrechnung nach MEA verteilt, für Mieter aber nach Wohnfläche oder nach externer Abrechnung.

## 9. Assistierter Rechnungsimport

Der Rechnungsimport ist in Phase 1 ein Vorschlagswerkzeug. Er darf keine Kostenposition automatisch final buchen. Es gibt zwei Eingangskanäle:

- **PDF-Rechnung:** Extraktion per OCR/Parsing (unsicherer).
- **E-Rechnung (XRechnung/ZUGFeRD):** strukturierte Felder direkt aus dem Datensatz (sicherer, siehe 6.5).

Ablauf:

1. Nutzer lädt eine PDF- oder E-Rechnung hoch (oder leitet sie an einen Eingangskanal weiter).
2. Das System extrahiert bzw. übernimmt Lieferant, Rechnungsdatum, Belegnummer, Betrag, USt, Leistungszeitraum und Textmerkmale.
3. Das System schlägt eine Kategorie aus dem Standardkontenplan vor.
4. Das System schlägt anhand der Kategorie Umlagefähigkeit und Verteilerschlüssel vor.
5. Der Nutzer prüft und bestätigt oder korrigiert die Werte.
6. Erst nach Bestätigung wird die Kostenposition für Abrechnungen aktiv und der Beleg revisionssicher festgeschrieben (siehe 6.6).

Jeder importierte Vorschlag muss einen Status haben:

- `draft_extracted`: aus PDF/E-Rechnung erkannt, noch nicht bestätigt
- `needs_review`: Erkennung unsicher oder Kategorie kritisch
- `confirmed`: vom Nutzer bestätigt und abrechnungsrelevant
- `rejected`: Vorschlag verworfen

## 10. Wirtschaftsplan

Der Wirtschaftsplan dient zur Berechnung von Hausgeldvorschüssen für Eigentümer.

Eingaben:

- Vorjahreskosten oder manuelle Planwerte pro Kategorie
- erwartete Rücklagenzuführung
- Verteilerschlüssel je Kostenart
- optional Rundungsregeln und monatliche Zahlweise

Ausgaben:

- Planbetrag je Kategorie
- Anteil je Einheit/Eigentümer
- monatlicher Hausgeldvorschlag
- Anteil umlagefähiger und nicht umlagefähiger Kosten
- Rücklagenanteil separat

Der Wirtschaftsplan ist kein Ersatz für einen formalen WEG-Beschluss. Das System erzeugt Vorschläge und Unterlagen für die Beschlussfassung; der gefasste Beschluss wird in der Beschluss-Sammlung dokumentiert (siehe 6.10).

## 11. Dokumenten- und PDF-Ausgabe

Phase 1 nutzt feste PDF-Vorlagen. Erzeugt werden:

- Betriebskostenabrechnung für Mieter
- Hausgeld-/Eigentümerabrechnung
- Wirtschaftsplan
- **Vermögensbericht (§ 28 Abs. 4 WEG)**
- Mahnschreiben
- Versammlungseinladung und Protokoll

Die Abrechnungs-PDFs sollen enthalten (formelle Mindestanforderungen nach BGH-Rechtsprechung für Mieterabrechnungen):

- Objekt und Abrechnungszeitraum
- Adressat und Einheitenbezug
- Kostenübersicht nach Kategorien (Gesamtkosten je Kostenart)
- angewandter Verteilerschlüssel je Position
- Berechnung des Anteils des Adressaten
- Abzug der Vorauszahlungen
- Nachzahlung oder Guthaben
- bei Eigentümerabrechnungen Trennung umlagefähig/nicht umlagefähig/Rücklage sowie Ausweis der Abrechnungsspitze
- Hinweise auf manuelle externe Abrechnungswerte, falls verwendet

Der Vermögensbericht enthält mindestens den Stand der Erhaltungsrücklage sowie eine Aufstellung des wesentlichen Gemeinschaftsvermögens (Forderungen/Verbindlichkeiten der Gemeinschaft).

## 12. Fristen- und Pflichtenüberwachung

Das System überwacht und erinnert an wesentliche Fristen:

- 12-Monats-Frist der Betriebskostenabrechnung (§ 556 Abs. 3 BGB).
- WEG-Jahresabrechnung/Vermögensbericht nach Kalenderjahresende.
- Anstehende Eigentümerversammlung / fällige Wirtschaftspläne.
- Überfällige offene Posten (Anstoß Mahnwesen).

## 13. Datenmodell auf hoher Ebene

Zentrale Entitäten:

- `Organization` (Mandanten-/Isolationsgrenze)
- `User`, `Role`
- `ManagementMandate`
- `Property`
- `Unit`
- `Meter`, `MeterReading`
- `Person`
- `OwnerProfile`
- `TenantProfile`
- `Vendor` (Dienstleister/Lieferant)
- `OwnershipHistory`
- `LeaseAgreement`
- `BankAccount`
- `CostCategory`
- `CostEntry`
- `InvoiceDocument` (PDF und/oder E-Rechnungs-Datensatz, mit Prüfsumme/Version)
- `Payment`
- `OpenItem` (offener Posten) und `DunningNotice` (Mahnung)
- `SettlementRun`
- `SettlementLine`
- `EconomicPlan`
- `EconomicPlanLine`
- `AssetReport` (Vermögensbericht)
- `OwnersMeeting`, `Resolution` (Beschluss), `ResolutionRegister` (Beschluss-Sammlung)

Beziehungen (Auszug):

- `Organization` umschließt alle anderen Entitäten (harte Mandantentrennung).
- `Property` hat viele `Unit`.
- `Unit` hat viele `OwnershipHistory` und viele `LeaseAgreement`.
- `Property`/`Mandate` hat ein oder mehrere `BankAccount`.
- `Property` hat viele `CostEntry`; `CostEntry` kann ein `InvoiceDocument` haben.
- `Payment` wird Mieter, Eigentümer, Einheit, Konto und Zeitraum zugeordnet und gleicht `OpenItem` aus.
- `SettlementRun` erzeugt `SettlementLine` pro Adressat und Kategorie.
- `OwnersMeeting` erzeugt `Resolution`; bestätigte `Resolution` werden in der `ResolutionRegister` fortgeschrieben.

## 14. Chronologische Regeln

- Eigentumswechsel und Mieterwechsel werden als Historie gespeichert.
- Historische Daten werden nicht überschrieben.
- Abrechnungen greifen auf den Zustand im jeweiligen Abrechnungszeitraum zu.
- Kostenpositionen haben Leistungszeiträume, nicht nur Rechnungsdaten.
- Bei unterjährigem Wechsel muss anteilig berechnet werden können.
- Freigegebene Belege und finalisierte Abrechnungen sind unveränderbar; Korrekturen erfolgen über Neuversionen mit Bezug zum Original.

## 15. Datenschutz (DSGVO)

Das System verarbeitet personenbezogene Daten von Mietern, Eigentümern und Dienstleistern. Phase 1 berücksichtigt:

- Rechtsgrundlage und Zweckbindung der gespeicherten Daten.
- Aufbewahrungs- und Löschkonzept (Verzahnung mit den steuerlichen 8-Jahres-Fristen für Belege).
- Datenzugriff nur innerhalb der eigenen Organization und gemäß Rolle.
- Datenhaltung in der EU; bei SaaS-Betrieb Auftragsverarbeitung (Art. 28 DSGVO) vorgesehen.
- Auskunfts- und Löschanfragen müssen technisch bedienbar sein (Export/Anonymisierung), ohne die revisionssichere Belegaufbewahrung zu verletzen.

## 16. Technische Architektur

Empfohlener MVP-Stack:

- Frontend: **Responsive Web-App** mit React und TypeScript (Desktop-first, voll bedienbar auf Tablet und Smartphone). Keine native App; mobile Nutzung über den Browser. Datei-Upload/Belegfoto über die Browser-Datei-/Kamera-API.
- Backend: Python FastAPI oder Node.js/NestJS.
- Datenbank: PostgreSQL mit mandantenfähigem Schema (Organization-Scoping auf jeder relevanten Tabelle).
- E-Rechnung: Parser/Validator für XRechnung und ZUGFeRD (EN-16931) als modularer Service.
- PDF-Erzeugung: HTML-zu-PDF Rendering, z. B. Playwright/Puppeteer, oder serverseitige PDF-Bibliothek.
- Dateispeicher: revisionssicher mit Prüfsummen; lokal im MVP, später S3-kompatibel mit Objektsperre/Versionierung.
- OCR/PDF-Extraktion: modularer Service, damit später ein anderer Anbieter oder ein lokales Modell angebunden werden kann.
- AuthN/AuthZ: Login mit Organization-Bindung und festen Rollen.

Architekturprinzipien:

- **API-first**, sauber getrennt von der Web-UI, damit Integrationen (z. B. späterer Bankimport, DATEV-Export) möglich bleiben. Die API dient Integrationen, nicht nativen Clients.
- Mandantentrennung als invariante Eigenschaft jeder Datenzugriffsschicht (kein Cross-Org-Zugriff).
- Abrechnungslogik getrennt von UI und PDF-Layout.
- Kostenkategorien als versionierte Systemdaten.
- Alle finalen Abrechnungsergebnisse und freigegebenen Belege werden als unveränderbare Snapshots gespeichert, damit spätere Stammdatenänderungen alte Dokumente nicht verändern.

## 17. Rechtliche Grundlagen für Defaults

Die Software bildet fachliche Defaults anhand folgender Grundlagen ab:

- BGB § 556: Vereinbarungen über Betriebskosten
- BGB § 556 Abs. 3: 12-Monats-Abrechnungsfrist und Einwendungsfrist
- BGB § 556a: Abrechnungsmaßstab für Betriebskosten
- BetrKV § 1: Definition und Ausschluss von Verwaltungs-/Instandhaltungskosten
- BetrKV § 2: Betriebskostenkatalog
- WEG § 16: Nutzungen und Kosten
- WEG § 24 Abs. 7: Beschluss-Sammlung
- WEG § 28: Wirtschaftsplan, Jahresabrechnung, Vermögensbericht (inkl. Abs. 4 Vermögensbericht; Abrechnungsspitze nach WEMoG)
- HeizkostenV, insbesondere Verteilung von Heiz- und Warmwasserkosten
- CO2KostAufG für Aufteilung von CO2-Kosten (10-Stufen-Modell)
- TKG-/Nebenkostenprivileg-Änderungen für Kabel/Breitband seit 2024
- UStG § 14 / E-Rechnungs-Pflicht (Empfang seit 01.01.2025; XRechnung/ZUGFeRD)
- GoBD: Unveränderbarkeit, Nachvollziehbarkeit, maschinelle Auswertbarkeit
- Aufbewahrungsfristen (8 Jahre für Buchungsbelege ab 2025, BEG IV)
- DSGVO: Verarbeitung personenbezogener Daten, Aufbewahrung/Löschung, Auftragsverarbeitung

Hinweis: Das System kann rechtliche Regeln technisch abbilden und Plausibilitätsprüfungen liefern. Es ersetzt keine juristische oder steuerliche Prüfung bei Sonderfällen.

## 18. Offene Punkte für die nächste Spezifikationsrunde

- Exakte PDF-Struktur für Betriebskostenabrechnung, Hausgeldabrechnung und Vermögensbericht.
- Welche Rundungsregeln gelten pro Abrechnung.
- Genaue Mahnstufen, Fristen und Gebührenlogik im Mahnwesen.
- Wie externe Heiz-/Wasserabrechnungen genau eingegeben werden: Betrag pro Einheit, PDF-Anhang, Gesamtsumme und Plausibilitätscheck; Übernahme des CO2-Aufteilungswerts.
- Detailtiefe des Mietkontos (nur Soll-Ist vs. einzelne Sollstellungen je Monat) und Behandlung von Kaution/Kautionskonto.
- Ob und wie Gewerbeeinheiten einen Vorwegabzug benötigen (andere Umlagelogik, USt-Option).
- Ob Eigentümerabrechnungen steuerliche Ausweise (z. B. § 35a EStG haushaltsnahe Dienstleistungen/Handwerkerleistungen) enthalten sollen.
- Eingangskanäle für E-Rechnungen (Upload, E-Mail-Postfach je Mandat) und Validierungsstrenge.
- Genauer Umfang der Versammlungsverwaltung (Stimmrechtsmodelle, Vollmachten, Umlaufbeschluss).
- Detailtiefe des Lösch-/Anonymisierungskonzepts im Spannungsfeld zur revisionssicheren Aufbewahrung.
