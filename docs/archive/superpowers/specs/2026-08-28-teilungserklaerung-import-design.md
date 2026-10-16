# Import und Gebäudewissen aus Teilungserklärungen

Datum: 2026-08-28
Status: Freigegeben

## 1. Ziel

Die Hausverwaltungs-Webanwendung erhält einen nachvollziehbaren Import für
Teilungserklärungen, Gemeinschaftsordnungen, Änderungsurkunden und zugehörige
Anlagen.

Der Import soll:

- aus einem oder mehreren PDFs einen neuen Gebäudeentwurf anlegen können,
- Dokumente zu einem bestehenden Gebäude ergänzen können,
- Anschrift, Einheiten, Miteigentumsanteile, Flächen, Grundstücksbezüge und
  Zuordnungen wie Sondernutzungsrechte strukturiert vorschlagen,
- alle dauerhaft verwaltungsrelevanten Klauseln unabhängig von einer festen
  Kategorienliste als belegtes Gebäudewissen vorschlagen,
- Ursprungsurkunden und Änderungen gemeinsam auswerten,
- Widersprüche und unklare Geltung sichtbar machen,
- jeden Vorschlag auf Dokument, PDF-Seite, Seitenaufdruck, Paragraph und
  Textstelle zurückführen,
- erst nach bewusster menschlicher Prüfung Gebäudedaten verändern,
- freigegebene Regeln später über Suche, Themenkarten und belegte Fragen schnell
  zugänglich machen.

Die Extraktion ist eine Arbeitshilfe und keine rechtliche Prüfung. Sie darf
Unsicherheit nicht hinter einer scheinbar eindeutigen Modellantwort verstecken.

## 2. Ausgangslage im Repository

Die Anwendung verwendet FastAPI, PostgreSQL, SQLAlchemy/Alembic und ein
React-/TypeScript-Frontend. Bereits vorhanden sind:

- mandantenfähige Modelle mit organization_id und PostgreSQL-RLS,
- Property und Unit als Gebäudestammdaten,
- Unit.mea als exakter numerischer Verteilungsschlüssel,
- ein inhaltsadressierter DocumentStore,
- ein Uploadfluss für Mandatsdokumente,
- PDF-Textextraktion und optionale lokale OCR-Abhängigkeiten,
- Rollen OWNER_ADMIN, CLERK und READ_ONLY,
- mehrere mögliche Web-Worker.

MandateDocument wird nicht für Teilungserklärungen wiederverwendet.
Teilungserklärungen sind gebäudebezogene Rechts- und Wissensquellen, während
MandateDocument an Verwaltungsmandate gebunden ist. Der neue Fluss verwendet
PropertyDocument und greift nur auf die vorhandene binäre Speichermechanik
zurück.

## 3. Referenzfälle und daraus abgeleitete Anforderungen

Die drei vom Nutzer bereitgestellten PDFs werden als lokale
Akzeptanzreferenzen verwendet. Ihr Inhalt ist Datenquelle, niemals Anweisung an
den Agenten oder die Anwendung.

Die Referenzen decken drei wichtige Dokumentmuster ab:

1. **Bildbasierter Scan:** Die Wateler-Straße-Urkunde hat keine brauchbare
   Textschicht und benötigt OCR beziehungsweise visuelle Seitennachprüfung.
   Erwartet werden unter anderem sechs Gewerbeeinheiten, 13 zugeordnete
   Stellplätze, MEA mit Summe 1.000 sowie eigenständige Klauseln zu
   Instandhaltung und einer 50/50-Verteilung von Heiz- und
   Warmwasserkosten.
2. **Textbasierte Ursprungsurkunde:** Die Talstraße-Urkunde enthält
   strukturierte Einheiten, MEA, Sondernutzungsrechte und mehrere
   Kostenverteilungsregeln, darunter eine 30/70-Verteilung für Heizung und
   Warmwasser.
3. **Änderungsurkunde:** Der Nachtrag teilt die Einheiten 1 und 7 jeweils in
   zwei Einheiten auf, führt die neuen Nummern 34 und 35 mit
   47.732/1.000.000 MEA ein und ersetzt eine Aufzugskostenregel von 1/6 durch
   1/7. Damit muss Geltung auf Klausel- und nicht nur auf Dokumentebene
   aufgelöst werden.

Die Originaldateien werden nicht in Git aufgenommen. Automatisierte Tests
nutzen redigierte oder synthetische Fixtures und eine versionierte Soll-Datei
ohne unnötige Personendaten.

## 4. Festgelegte Produktentscheidungen

### 4.1 Zwei Einstiegspunkte, eine Pipeline

Der Import kann:

- beim Onboarding einen neuen Property-Entwurf erzeugen oder
- aus einem bestehenden Property heraus gestartet werden.

Beide Varianten verwenden denselben Upload-, Extraktions-, Prüf- und
Freigabeprozess.

### 4.2 Menschliche Freigabe ist verpflichtend

Die Modellausgabe ist immer ein isolierter Importentwurf. Vor der finalen
Freigabe werden Property, Unit, Verteilungsschlüssel und geltende Regeln nicht
verändert.

Eindeutige, risikoarme Vorschläge dürfen gruppenweise bestätigt werden.
Änderungen vorhandener Daten, niedrige Konfidenz, Widersprüche und
Geltungsentscheidungen benötigen eine Einzelentscheidung. Fehlende Einheiten
werden nie als automatische Löschung interpretiert.

### 4.3 Fester Kern plus offenes Regelregister

Strukturell feste Kerndaten sind:

- Anschrift und Gebäudebezeichnung,
- Verwaltungsart,
- Grundbuch-, Gemarkungs-, Flur- und Flurstücksbezüge,
- Einheiten mit Nummer, Art, Lage und Flächen,
- Miteigentumsanteile,
- Gebäudeteile und Zubehör,
- Sondernutzungsrechte und ihre Zuordnung.

Zusätzlich gibt es ein offenes Regelregister. Kategorien wie
Kostenverteilung, Heizung/Wasser, Instandhaltung, Nutzung, Stimmrecht,
Versicherung oder Verwalterbefugnisse helfen beim Suchen. Eine unbekannte
Kategorie darf aber niemals dazu führen, dass eine verwaltungsrelevante
Klausel verworfen wird.

### 4.4 Gebäudewissen ohne automatische Buchungswirkung

Freigegebene Regeln sind zunächst belegtes Gebäudewissen. Eine
Kostenverteilungsklausel verändert nicht automatisch eine laufende
Abrechnung, einen Wirtschaftsplan oder einen Verteilungsschlüssel. Dafür ist
eine separate, explizite Zuordnung und Freigabe in der jeweiligen
Fachfunktion erforderlich.

### 4.5 Begrenzter Agenten-Harness

Der Extraktionsagent ist kein frei operierender Agent. Er ist eine
deterministisch orchestrierte Zustandsmaschine mit:

- festen Verarbeitungspässen,
- versionierten Ein- und Ausgabeschemas,
- begrenzten Wiederholungen,
- hartem Budget,
- keinen Modellwerkzeugen und keinen externen Aktionen,
- deterministischen Prüfungen nach jeder Modellausgabe.

## 5. Zielarchitektur

~~~text
React-Web-App
  |
  | authentifizierter Upload / Review / Suche
  v
FastAPI
  - PropertyDocument API
  - DeclarationImport API
  - Review- und Approval-Service
  - PropertyKnowledge API
  |
  +--------------------------+
  |                          |
  v                          v
Content-addressed         PostgreSQL
DocumentStore            - Importauftrag und Lease
- Original-PDF           - Kandidaten, Belege, Issues
- unverändert            - Property/Unit/Rule
                             |
                             v
                     separater Import-Worker
                     - lokale PDF-Analyse/OCR
                     - OpenAI-Adapter
                     - Schema- und Fachvalidierung
~~~

Der Worker läuft als separater Prozess. Ein FastAPI-BackgroundTask reicht
nicht aus, weil die Anwendung mit mehreren Web-Workern betrieben werden kann
und Extraktionen Prozessneustarts überleben müssen.

Der Worker verarbeitet nicht vertrauenswürdige PDF-Dateien als unprivilegierter
Prozess mit begrenztem temporärem Speicher, Seiten-/Pixelgrenzen und Timeouts.
Sein Runtime-Image enthält die explizit gepinnten PDF-/OCR-Werkzeuge sowie das
deutsche Tesseract-Sprachpaket. Fehlt eine benötigte Fähigkeit, entsteht ein
sichtbarer Preflight-Fehler statt eines stillen Qualitätsverlusts.

Für die erste Version wird PostgreSQL selbst als dauerhafte Queue verwendet.
Der Worker verbindet sich wie die Web-App ausschließlich als app_rw ohne
BYPASSRLS. Seine Dispatch-Schleife liest in einer unscoped Session nur IDs aus
der globalen organization-Tabelle. Für jede Organisation öffnet sie danach
eine get_tenant_session(org_id) und beansprucht genau dort einen fälligen
DeclarationImport mit SELECT FOR UPDATE SKIP LOCKED. Tenant-Tabellen werden
niemals aus einer unscoped Session gelesen.

Der Claim wird in einer kurzen Transaktion gespeichert. Er erhöht eine
monotone lease_generation und erzeugt ein zufälliges lease_token mit
lease_expires_at. OCR und Provideraufrufe laufen außerhalb dieser Transaktion.
Jede nachfolgende Status-, Fortschritts-, Run- oder Kandidatenmutation verwendet
einen Compare-and-Swap auf Import-ID, erwarteten Status, lease_generation und
lease_token. Nach Lease-Ablauf kann ein anderer Worker übernehmen; ein alter
Worker kann danach keine Ergebnisse mehr schreiben. Reclaim, Cancel und
Approve invalidieren den bisherigen Token.

Während OCR und Provideraufrufen erneuert ein separater Heartbeat das Lease mit
demselben Compare-and-Swap. Heartbeat-Intervall und Lease-Dauer sind
konfigurierbar; das Intervall beträgt höchstens ein Drittel der Lease-Dauer.
OCR-Subprozesse und HTTP-Aufrufe besitzen zusätzlich harte Timeouts. Schlägt
eine Erneuerung fehl, beendet der Worker seine Verarbeitung so früh wie möglich
und verwirft jedes danach eintreffende Ergebnis.

Die Produktionsdefinition erhält einen separaten worker-Service aus demselben
Image mit dem Command python -m app.workers.declaration_import, app_rw-
Zugangsdaten, OpenAI-Secret und demselben DocumentStore-Volume wie die App. Er
veröffentlicht keinen Port. Sein Healthcheck prüft Prozess, Datenbankzugang,
Speicherzugang und konfigurierte OCR-Fähigkeiten, ohne Tenant-Tabellen unscoped
zu lesen. Redis, Celery oder ein weiterer Infrastrukturdienst sind für dieses
Volumen nicht erforderlich.

## 6. Import-Zustandsmaschine

Ein DeclarationImport durchläuft folgende Hauptzustände:

~~~text
DRAFT
  -> QUEUED
  -> PREFLIGHT
  -> EXTRACTING
  -> RECONCILING
  -> NEEDS_REVIEW
  -> APPLYING
  -> APPLIED
~~~

Zusätzliche Zustände sind RETRY_WAIT, FAILED und CANCELLED.

Regeln:

- DRAFT enthält hochgeladene Dokumente, Reihenfolge und manuelle
  Dokumentklassifikation.
- Ab QUEUED übernimmt ausschließlich der Worker die Extraktionszustände.
- Ein temporärer Providerfehler wechselt nach begrenztem Versuch in
  RETRY_WAIT.
- Ein fachlich unvollständiges Ergebnis wird NEEDS_REVIEW und nicht FAILED.
- APPLYING verwendet eine einzige Datenbanktransaktion.
- Ein fehlgeschlagenes APPLYING lässt den bisherigen Gebäudestand unverändert.
- Der Approve-Endpunkt ist idempotent.
- Eine draft_revision verhindert, dass ein veralteter Browserstand spätere
  Reviewentscheidungen überschreibt.
- Worker-Übergänge und Zwischenstände sind nur mit passender lease_generation
  und passendem lease_token zulässig; null aktualisierte Zeilen bedeuten, dass
  der Worker seine Berechtigung verloren hat und sofort beendet.

## 7. Persistenzmodell

Alle neuen Tabellen tragen organization_id, RLS-Policies, UUID-Primärschlüssel
und Zeitstempel. Jede Migration aktiviert und erzwingt RLS mit ENABLE ROW LEVEL
SECURITY und FORCE ROW LEVEL SECURITY, legt mindestens SELECT- und INSERT-
Policies an und ergänzt die Tabelle in TENANT_TABLES des zentralen
RLS-Abdeckungstests. Die globale organization-Tabelle bleibt die einzige für
den Dispatcher benötigte Ausnahme.

### 7.1 Dokumente und Importworkflow

**property_document**

- optionales property_id; bei einem Neuimport erst nach Freigabe gesetzt,
- Dokumenttyp: DECLARATION, AMENDMENT, ANNEX oder OTHER,
- Titel, Dokumentdatum und optionale Urkundenreferenz,
- Originaldateiname, MIME-Typ, Größe und Seitenzahl,
- SHA-256, storage_uri und storage_backend,
- uploaded_by und Archivstatus.

**property_document_relation**

- source_document_id und target_document_id,
- Relation AMENDS, SUPPLEMENTS oder REFERENCES,
- fachlich freigegebener Status,
- optionale Evidenz für die Beziehung.

**declaration_import**

- Modus NEW_PROPERTY oder EXISTING_PROPERTY,
- optionales target_property_id,
- Status, draft_revision und Fortschritt,
- initiator, approver und Zeitpunkte,
- lease_owner, lease_token, lease_generation, lease_expires_at sowie Retry- und
  Fehlerdaten,
- Tokenverbrauch und geschätzte Providerkosten,
- konfiguriertes Importbudget.

**declaration_import_document**

- n:m-Verknüpfung zwischen Import und Dokument,
- fachliche Rolle und Reihenfolge im Dokumentensatz.

**declaration_extraction_run**

- ein Datensatz je Modellversuch,
- Provider, Modell, Prompt-, Schema- und Harness-Version,
- Status, Laufzeit, Input-/Output-Tokens und Kostenschätzung,
- normalisierter Fehlercode; kein API-Schlüssel und kein kompletter
  Dokumenttext im technischen Log.

### 7.2 Reviewkandidaten

**declaration_candidate**

- Typ PROPERTY_FIELD, LEGAL_REFERENCE, UNIT_CREATE, UNIT_FIELD,
  MEA_RESCALE, ASSIGNMENT, RULE oder DOCUMENT_RELATION,
- stabiler subject_key und optionaler field_key,
- Operation CREATE, UPDATE oder KEEP; keine automatisch erzeugte
  DELETE-Operation,
- previous_value_json und proposed_value_json,
- confidence als Routinghinweis, nicht als Wahrheitsbeweis,
- serverseitig abgeleitete risk_class und requires_individual_review; diese
  Werte stammen nie aus der Modellantwort,
- Reviewstatus PENDING, ACCEPTED, CORRECTED, REJECTED oder
  NEEDS_CLARIFICATION,
- reviewer, reviewed_at und Kandidatenversion.

**declaration_candidate_evidence**

- candidate_id und property_document_id,
- PDF-Seite als einsbasierte technische Seitennummer,
- optionaler aufgedruckter Seitenwert,
- Paragraph/Abschnitt,
- kurze stützende Originaltextstelle,
- optionaler Feldpfad bei Kandidaten mit mehreren Werten.

**declaration_candidate_issue**

- Code, Schweregrad INFO, WARNING oder BLOCKER,
- verständliche Nachricht und strukturierte Details,
- optionaler Kandidatenbezug,
- Resolution und resolve-Audit.

Ein Candidate kann mehrere Quellen besitzen. Ein freigabefähiger kritischer
Candidate benötigt mindestens eine gültige Evidenz. Bei zusammengeführten oder
widersprüchlichen Klauseln werden alle beteiligten Belege gespeichert.

Die CandidatePolicy wird nach jeder Extraktion und vor jedem Review-Write
deterministisch neu berechnet. Immer einzeln zu prüfen sind insbesondere
Property-Identität, Einheitennummer/-typ/MEA, Dokument- und Regelrelationen,
alle fachlichen Regeln, jede Änderung eines vorhandenen Werts sowie jeder
Candidate mit WARNING, BLOCKER, Konflikt oder niedriger Konfidenz. Konfidenz
kann die Risikoklasse nur erhöhen, niemals herabsetzen. Ein Gruppen-Approve-
Endpunkt lehnt die gesamte Anfrage ab, sobald ein enthaltenes Element
requires_individual_review besitzt.

### 7.3 Freigegebene Gebäudedaten

Property und Unit bleiben die führenden Kerntabellen.

Die kanonische MEA-Darstellung ist für ein Property eindeutig:

- Property erhält mea_denominator als positiven ganzzahligen Nenner und
  mea_scale_status CONFIRMED oder LEGACY_UNCONFIRMED.
- Unit.mea ist bei CONFIRMED immer der Zähler in genau diesem gemeinsamen
  Property-Nenner. Das Feld wird auf NUMERIC(18,6) erweitert.
- Die deutsche Urkundenschreibweise 47.732/1.000.000 wird damit als
  Zähler 47732 und Nenner 1000000 gespeichert; 186/1.000 als 186 und 1000.
- Die Abrechnungsengine darf Unit.mea weiterhin unmittelbar als relatives
  Gewicht verwenden, weil innerhalb eines Property alle Einheiten dieselbe
  Skala besitzen.

Neue Properties aus einer vollständigen Urkunde starten mit CONFIRMED. Für
bestehende Properties wird kein Nenner aus möglicherweise unvollständigen
Altwerten geraten: Die Migration setzt LEGACY_UNCONFIRMED und lässt die
bisherigen relativen Unit.mea-Werte unverändert. Ein Import, der MEA an einem
solchen Property anlegen oder verändern würde, bleibt blockiert, bis der
Nenner ausdrücklich bestätigt ist.

Weicht die Urkundenskala eines Nachtrags vom bestätigten Property-Nenner ab,
werden Brüche nur bei exakter Umrechnung übernommen. Andernfalls darf der
Server einen property-weiten MEA_RESCALE-Candidate auf einen gemeinsamen
Nenner vorschlagen. Dieser ist immer einzeln zu prüfen und aktualisiert alle
betroffenen Unit.mea-Werte und den Property-Nenner atomar. Gemischte oder nicht
proportionale Altbestandsskalen erzeugen einen BLOCKER und werden niemals
automatisch normalisiert.

API und UI geben Unit.mea zusammen mit Property.mea_denominator aus und zeigen
die quellentreue Bruchdarstellung. Das Originalformat bleibt zusätzlich in der
Evidence erhalten.

Neue kanonische Tabellen:

**property_legal_reference**

- Property-Bezug,
- Art des Registers/Bezuges,
- Gemarkung, Flur, Flurstück, Blatt und freie Zusatzangaben,
- Status und Zeitbezug.

**property_assignment**

- Property und optional zugeordnete Unit,
- Typ wie SONDERNUTZUNGSRECHT, STELLPLATZ, KELLER, GARTEN oder OTHER,
- Kennzeichnung, Lage, Beschreibung und strukturierte Zusatzdaten.

**property_rule**

- Property-Bezug und stabiler fachlicher Schlüssel,
- offener topic-Code, Titel und verständliche Zusammenfassung,
- scope_json für betroffene Einheiten, Bauteile oder Beteiligte,
- effect_json für Pflicht, Berechtigung, Formel und Ausnahmen,
- Status EFFECTIVE, SUPERSEDED, NEEDS_CLARIFICATION oder RETRACTED,
- optionale Gültigkeitsdaten,
- Herkunftsimport und Freigabe-Audit,
- search_text für PostgreSQL-Volltextsuche.

**property_rule_relation**

- n:m-Beziehungen SUPERSEDES, SUPPLEMENTS oder CONFLICTS zwischen Regeln.

**property_rule_source**

- Regel, Dokument, PDF-Seite, Seitenaufdruck, Paragraph und Textstelle.

**approved_fact_source**

- Property und Dokument,
- subject_kind, subject_id und field_key,
- PDF-Seite, Seitenaufdruck, Paragraph und Textstelle,
- Herkunftsimport.

approved_fact_source erhält die Belege der freigegebenen Property-, Unit-,
Legal-Reference- und Assignment-Werte. So bleibt auch ein Kernfeld nach dem
Import auf seine Urkunde zurückführbar.

## 8. Versionierter Extraktionsvertrag

OpenAI liefert ausschließlich JSON nach einem versionierten Structured-Output-
Schema. Die Antwort wird anschließend nochmals mit Pydantic validiert.

Jeder atomare Fakt enthält mindestens:

~~~json
{
  "subject_key": "unit:34",
  "field": "mea",
  "raw_value": "47.732/1.000.000",
  "normalized_value": {
    "numerator": "47732",
    "denominator": "1000000"
  },
  "confidence": 0.98,
  "evidence": [
    {
      "document_key": "amendment-1",
      "pdf_page": 4,
      "printed_page": "4",
      "section": "§ 1",
      "quote": "47,732/1.000.000 Miteigentumsanteil"
    }
  ],
  "issues": []
}
~~~

Eine Regel enthält mindestens:

- title und offenen topic-Code,
- sachliche, kurze summary,
- scope mit betroffenen Einheiten, Gebäudeteilen oder Beteiligten,
- effect mit obligation, entitlement, allocation_formula und exceptions,
- vorgeschlagenen Geltungsstatus,
- optionalen Bezug zu ersetzten oder widersprüchlichen Regeln,
- mindestens eine Evidence,
- confidence und clarification_needed.

allocation_formula ist eine deklarative Beschreibung aus Anteilen,
Bezugsgrößen, Geltungsbereich und Ausnahmen. Sie wird zusammen mit dem
Originaltext gespeichert und niemals als Programmcode ausgeführt.

Zusätzliche fachliche Inhalte dürfen in einem begrenzten additional_facts-
Objekt erhalten bleiben. Das Schema verbietet jedoch freie Datenbankoperationen,
Toolanweisungen und ausführbaren Code.

Modellkonfidenz dient nur der Sortierung im Review. Ein hoher Wert kann niemals
einen Quellenbeleg oder die menschliche Freigabe ersetzen.

## 9. Extraktions-Harness

### Pass 0: Preflight und lokale Seitenanalyse

- gespeicherten Blob gestreamt gegen content_byte_size und content_sha256 des
  PropertyDocument prüfen; bei Abweichung mit STORAGE_INTEGRITY_MISMATCH
  dauerhaft stoppen und keinen Provider aufrufen,
- PDF-Signatur und parsebare Struktur prüfen,
- Passwortschutz, Größe, Dokumentanzahl und Gesamtseitenlimit prüfen,
- Seitendrehung und vorhandene Textschicht feststellen,
- Text mit pdfplumber extrahieren,
- textarme Seiten lokal mit begrenztem OCR-Aufruf bearbeiten; temporäre
  Renderdateien nach dem Lauf entfernen,
- Renderauflösung, Pixelanzahl, Laufzeit und temporären Speicher begrenzen,
- Qualitätswerte pro Seite speichern,
- keine Modellausführung bei beschädigter oder unzulässiger Datei.

### Pass 1: Dokumentinventar und Beziehungen

Das Primärmodell klassifiziert Titel, Dokumentart, Dokumentdatum,
Urkundenreferenz und ausdrücklich genannte Bezugsurkunden. Es schlägt die
Reihenfolge und AMENDS-/SUPPLEMENTS-Beziehungen vor.

Die Reihenfolge ist noch keine rechtliche Geltungsentscheidung. Eine
Notarisierungs- oder Dokumentzeitangabe wird nicht ohne Textbeleg als
Wirksamkeitsdatum interpretiert.

### Pass 2: Feste Kerndaten

Extraktion von:

- Gebäude und Anschrift,
- Grundbuch-/Flurstücksbezügen,
- Einheiten und Typen,
- Flächen und Gebäudeteilen,
- MEA-Zähler und -Nenner,
- Zubehör und Sondernutzungsrechten.

Tabellen und Aufstellungen werden seitenübergreifend zusammengesetzt. Jeder
Wert bleibt mit seiner Evidence verbunden.

### Pass 3: Offene Klauselgewinnung

Jeder Dokumentabschnitt wird auf eine dauerhafte Relevanz für die Verwaltung
geprüft. Die Taxonomie ist nur ein Such- und Analysehilfsmittel. Der Pass muss
auch unbekannte, aber relevante Pflichten, Rechte, Formeln, Ausnahmen,
Zuständigkeiten und Fristen erfassen. Kein Abschnitt wird allein deshalb
übersprungen, weil eine Kategorieheuristik ihn nicht erkennt.

Nicht als Stammdaten übernommen werden insbesondere Notare, Vertretungen,
frühere Eigentümer und Vollmachtgeber, außer aus dem Dokument ergibt sich eine
fortgeltende verwaltungsrelevante Pflicht oder Berechtigung.

### Pass 4: Reconciliation und Vollständigkeit

- Kandidaten derselben Einheit oder Regel zusammenführen,
- Ursprung und Änderung auf Klausel-Ebene vergleichen,
- Ersetzungen, Ergänzungen und Konflikte vorschlagen,
- Einheiten- und Zuordnungslisten auf Lücken prüfen,
- nicht belegte oder widersprüchliche Aussagen zu Issues machen,
- keine unklare Relation erzwingen.

### Pass 5: Gezielte Nachprüfung

Nur bei fehlendem Beleg, niedriger Seitenqualität, widersprüchlichem Ergebnis
oder kritischer Unsicherheit werden einzelne Seiten höher aufgelöst an das
Modell übergeben. Das stärkere Eskalationsmodell darf nur für diese begrenzten
Fälle und innerhalb des Importbudgets verwendet werden.

### Pass 6: Deterministische Validierung

Mindestens folgende Prüfungen laufen ohne LLM:

- MEA-Nenner sind positiv und Werte exakt parsebar,
- alle bestätigten Unit.mea eines Property verwenden denselben
  Property.mea_denominator,
- eine MEA-Änderung an LEGACY_UNCONFIRMED bleibt bis zur Nennerbestätigung
  blockiert,
- ein Rescale ist nur bei für alle betroffenen Einheiten exakt proportionalen
  Werten zulässig; gemischte Altbestandsskalen sind ein BLOCKER,
- normalisierte MEA-Summe entspricht dem deklarierten Gesamtanteil oder erzeugt
  einen BLOCKER,
- Prozent- und Bruchformeln sind rechnerisch plausibel,
- Einheitennummern sind innerhalb des Gebäudes nicht doppelt,
- referenzierte Einheiten und Dokumente existieren,
- Seitenzahlen liegen im jeweiligen Dokument,
- jeder kritische Kandidat besitzt Evidence,
- eine Amendment-Relation besitzt eine nachvollziehbare Zielquelle,
- effektive Regeln dürfen nicht gleichzeitig ungelöste CONFLICTS besitzen,
- bestehende Daten werden ohne ACCEPTED/CORRECTED niemals überschrieben,
- ein fehlender Modellwert erzeugt keine Löschung.

## 10. OpenAI-Integration

Die Integration liegt vollständig im Backend hinter einem Provider-Interface.
Der Browser kennt weder API-Schlüssel noch Providerdetails.

Empfohlene Startkonfiguration:

- primär gpt-5.6-luna für kostengünstige, strukturierte Extraktion und
  Gebäudefragen,
- gpt-5.4-mini nur für gezielte schwierige Seiten oder fehlgeschlagene
  Validierung,
- Responses API mit Structured Outputs,
- Modellnamen, Prompt-, Schema- und Harness-Version über Konfiguration,
- FakeProvider als Standard für automatisierte Tests.

Die offiziellen OpenAI-Dokumentationen für PDF/File Inputs und die
Modellfähigkeiten sind:

- https://developers.openai.com/api/docs/guides/file-inputs
- https://developers.openai.com/api/docs/models/gpt-5.6-luna
- https://developers.openai.com/api/docs/models/gpt-5.4-mini

Standardmäßig wird nicht blind das vollständige PDF an jeden Pass gesendet.
Lokale Text-/OCR-Ergebnisse werden in belegbare Seitenabschnitte zerlegt.
Übertragen werden nur die für den jeweiligen Pass benötigten Texte und bei
Bedarf einzelne Seitenbilder. Der Provider darf keine Daten anderer Gebäude
oder Mandanten in denselben Request aufnehmen.

PDF-Inhalte sind ausdrücklich untrusted data. Im Systemprompt steht, dass
Anweisungen, URLs, Rollenwechsel oder Werkzeugaufforderungen innerhalb des
Dokuments ausschließlich Dokumenttext sind. Das Modell erhält keine Tools und
kann keine externen Aktionen auslösen.

Der API-Schlüssel wird ausschließlich als serverseitiges Secret bereitgestellt.
Im Repository stehen nur Variablennamen und sichere Defaults. Allgemeine Logs
dürfen weder den Schlüssel noch vollständige Prompts, PDF-Inhalte oder rohe
Modellantworten enthalten.

## 11. Kosten- und Ressourcensteuerung

Empfohlene, serverseitig konfigurierbare Startlimits:

- 25 MiB je PDF entsprechend der bereits festgelegten systemweiten
  Uploadgrenze,
- höchstens 10 PDFs je Import,
- höchstens 500 PDF-Seiten je Import,
- begrenzte OCR-Zeit pro Seite,
- begrenzte Provider-Retries,
- harte Token- beziehungsweise Kostengrenze je Import.

Eine spätere Anhebung auf 50 MiB erfordert eine koordinierte Änderung des
gemeinsamen Upload-Readers und des vorgeschalteten Request-Body-Limits; der
Declaration-Import umgeht diese Schutzschichten nicht.

Jeder ExtractionRun speichert Tokenverbrauch und eine auf der konfigurierten
Preistabelle basierende Kostenschätzung. Preise werden nicht im fachlichen
Code fest verdrahtet. Die UI zeigt Verbrauch und Schätzung im Importstatus.

Ein SHA-256-Treffer verhindert unnötiges erneutes Speichern. Eine frühere
Extraktion darf nur wiederverwendet werden, wenn Dokument-Hash, Schema-,
Prompt-, Harness- und Modellversion kompatibel sind; andernfalls entsteht ein
neuer ExtractionRun.

## 12. Review- und Freigabefluss

### Upload

- Mehrere PDFs können gemeinsam ausgewählt werden.
- Die UI zeigt vermutete Dokumentart und Reihenfolge.
- Der Nutzer kann beides vor dem Start korrigieren.
- Vor dem Start ist sichtbar, dass benötigte Dokumentabschnitte über die
  OpenAI API verarbeitet werden.
- Nach dem Start darf die Seite geschlossen werden.

### Review

Die Reviewseite gruppiert:

- Stammdaten,
- Einheiten,
- Zuordnungen,
- Regeln,
- Klärungsbedarf.

Jeder Vorschlag zeigt:

- bisherigen und vorgeschlagenen Wert,
- Erstellen/Ändern/Beibehalten,
- Konfidenz und Issues,
- ACCEPT, CORRECT, REJECT oder NEEDS_CLARIFICATION,
- Original-PDF direkt auf der stützenden Seite.

Eindeutige neue Werte dürfen gruppenweise bestätigt werden. Ein
Bestandsupdate zeigt immer Vorher/Nachher. BLOCKER, niedrige Konfidenz,
Widersprüche und Regelvorrang sind nicht sammelbestätigbar.

### Freigabe

Vor dem finalen Approve zeigt die UI eine Änderungssumme. Freigabe ist
gesperrt, solange:

- bei einem Neuimport name, street, zip_code, city oder management_type fehlen,
- ein kritischer Kandidat PENDING ist,
- ein BLOCKER ungelöst ist,
- eine zu übernehmende Regel keinen Beleg besitzt,
- ein Dokumentbezug oder eine Einheit unaufgelöst ist.

Der Approval-Service:

1. prüft Rolle, organization_id und draft_revision,
2. sperrt den Importdatensatz,
3. validiert alle Entscheidungen erneut,
4. legt bei NEW_PROPERTY das Property an,
5. schreibt akzeptierte Property-/Unit-/Assignment-Werte,
6. schreibt Regeln, Quellen und Relationen,
7. bindet Dokumente an das Property,
8. markiert den Import APPLIED,
9. committet alles gemeinsam.

OWNER_ADMIN und CLERK dürfen importieren, reviewen und freigeben. READ_ONLY darf
Dokumente, freigegebene Regeln und Quellen lesen, aber keine Kandidaten
verändern oder einen Import starten. Nicht freigegebene Importentwürfe und
technische ExtractionRun-Details sind nur für Write-Rollen sichtbar.

## 13. Gebäudewissen und Fragenfunktion

Im Property-Detail entsteht ein Bereich Dokumente & Wissen mit:

- Dokumentliste und Versionsbeziehungen,
- Themenkarten für geltende Regeln,
- Volltextsuche,
- Fragefeld,
- Kennzeichnung EFFECTIVE, SUPERSEDED und NEEDS_CLARIFICATION,
- Sprung zur Originalseite.

Die erste Version verwendet PostgreSQL-Volltextsuche und Trigram-Suche über
freigegebene Kernfakten sowie Titel, Topic, Zusammenfassung, Wirkung und
Belegtext der Regeln. Eine zusätzliche Vektordatenbank ist nicht erforderlich.

Für eine natürlichsprachliche Frage:

1. filtert der Server ausschließlich freigegebene Kernfakten und Regeln
   desselben Property und Mandanten,
2. bevorzugt bei Regeln EFFECTIVE und berücksichtigt sichtbare
   NEEDS_CLARIFICATION-Konflikte,
3. übergibt nur die gefundenen Fakten, Regelobjekte und Quellen an das Modell,
4. verlangt eine strukturierte Antwort mit verwendeten fact_ids, rule_ids und
   source_ids,
5. verwirft Quellenkennungen, die nicht im Retrievalsatz enthalten waren,
6. gibt bei fehlender Deckung „nicht dokumentiert“ oder „Klärungsbedarf“
   zurück.

Auch die Nutzerfrage und die abgerufenen Originaltextstellen sind für das
Antwortmodell untrusted data. Sie können die Quellenpflicht, Mandantengrenze
oder Werkzeuglosigkeit nicht aufheben.

Fällt OpenAI aus, bleiben Suche, Themenkarten und Originalquellen nutzbar.
Die Antwortfunktion hat keine Tools und führt keine Fachaktion aus.

## 14. API-Oberfläche

Vorgesehene Ressourcen:

- POST /api/declaration-imports
- POST /api/declaration-imports/{id}/documents
- PATCH /api/declaration-imports/{id}/documents/{document_id}
- POST /api/declaration-imports/{id}/start
- GET /api/declaration-imports/{id}
- GET /api/declaration-imports/{id}/candidates
- PATCH /api/declaration-imports/{id}/candidates/{candidate_id}
- POST /api/declaration-imports/{id}/retry
- POST /api/declaration-imports/{id}/approve
- POST /api/declaration-imports/{id}/cancel
- GET /api/properties/{property_id}/documents
- GET /api/properties/{property_id}/documents/{document_id}/content
- GET /api/properties/{property_id}/rules
- POST /api/properties/{property_id}/knowledge/query

Dateiinhalte werden über einen autorisierten Streaming-Endpunkt ausgeliefert.
Seitenparameter dienen dem Viewer, sind aber niemals Bestandteil des
Speicherpfads. Fremde Mandanten-IDs liefern wie in der übrigen Anwendung 404,
nicht 403.

Mutierende Requests verwenden die vorhandene Write-Role-Prüfung. Start, Retry
und Approve sind gegen Doppelaufrufe idempotent.

## 15. Fehlerbehandlung

| Fehler | Systemreaktion | Nutzerwirkung |
|---|---|---|
| Beschädigtes oder verschlüsseltes PDF | vor Provideraufruf stoppen | konkrete Datei und behebbarer Grund |
| Schlechter Scan | gezieltes Re-Rendern/OCR/Seitenbild | Seite als unsicher, manuelle Korrektur möglich |
| Provider-Timeout oder Rate-Limit | begrenzter Backoff, Lease bleibt wiederaufnehmbar | Fortschrittsstatus und Retry |
| Ungültige Structured Output | Schema ablehnen, einmal gezielt nachfordern | kein halber Entwurf |
| Importbudget erreicht | Verarbeitung geordnet stoppen | Kosten-/Limit-Hinweis und bewusste Fortsetzung |
| MEA-/Formelabweichung | deterministischer BLOCKER | Soll/Ist mit Quellen |
| widersprüchliche Klauseln | keine automatische Auflösung | beide Quellen und Klärungsbedarf |
| Fehler beim Approval | vollständiger Rollback | bestehender Gebäudestand bleibt erhalten |

Technische Fehlercodes sind stabil und maschinenlesbar. Nutzertexte bleiben
verständlich und enthalten keine Secrets oder fremden Daten.

## 16. Datenschutz und Audit

- Alle Import-, Kandidaten-, Dokument- und Regeldaten unterliegen
  organization_id und RLS.
- Der Zugriff auf PDF-Inhalte benötigt zusätzlich eine Property-/Import-
  Berechtigungsprüfung.
- Personen werden nur übernommen, wenn sie Träger einer fortgeltenden,
  verwaltungsrelevanten Pflicht oder Berechtigung sind.
- Originaldokument, SHA-256, Modell-/Prompt-/Schema-Version, Extraktionslauf,
  Reviewentscheidungen und Approver bleiben nachvollziehbar.
- Strukturierte Entwürfe werden mandantengeschützt gespeichert, aber nicht in
  allgemeinen Logs dupliziert.
- Dokumente werden nicht überschrieben. Korrekturen oder Nachträge sind neue
  PropertyDocuments.
- Historische Regeln bleiben als SUPERSEDED nachvollziehbar und werden in
  normalen Antworten nicht als aktuell ausgegeben.
- Aufbewahrung und Löschung folgen der bestehenden DSGVO-/Dokumentlogik; ein
  noch von einem freigegebenen Fakt oder einer Regel referenziertes Dokument
  kann nicht stillschweigend gelöscht werden.
- Vor produktiver Nutzung werden die eingesetzten OpenAI-API-Datenkontrollen,
  Aufbewahrungseinstellungen, Vertragsgrundlagen und der Datenschutzhinweis der
  Hausverwaltung dokumentiert und fachlich geprüft.

## 17. Tests und Evaluation

### 17.1 Deterministische Tests

Unit- und Vertragstests decken ab:

- Pydantic-/JSON-Schema für alle Kandidatentypen,
- MEA-Parsing, Nennernormalisierung und Summen,
- LEGACY_UNCONFIRMED, exakte Property-Skala und MEA_RESCALE-Policy,
- Prozent-/Bruchformeln,
- Dokument- und Regelrelationen,
- Zustandsmaschine, Lease und Retry,
- lease_generation-/lease_token-Compare-and-Swap,
- erfolgreiche CAS-Heartbeat-Erneuerung und Abbruch nach verlorenem Heartbeat,
- serverseitige CandidatePolicy und Verbot der Gruppenfreigabe kritischer
  Kandidaten,
- Reviewstatus und Freigabeblocker,
- DocumentStore-Größe-/SHA-256-Integritätsprüfung vor dem Parser,
- idempotentes Approval und Rollback,
- Quellenseitenvalidierung,
- Prompt-Injection-Text als reine Daten.

### 17.2 Integrations- und API-Tests

Mit PostgreSQL und FakeProvider:

- NEW_PROPERTY und EXISTING_PROPERTY vollständig,
- Multi-PDF-Upload und Dokumentreihenfolge,
- textbasierte und OCR-markierte Seiten,
- parallele Worker und Lease-Recovery,
- organisationsweiser Dispatch über get_tenant_session ohne unscoped Zugriff
  auf Tenant-Tabellen,
- Zombie-Worker-Test: Worker A verliert sein Lease, Worker B beendet den Job,
  danach darf A weder Kandidaten noch Status verändern,
- ungültige Providerantwort und Retry,
- Storage-Hash-Mismatch ohne Provideraufruf,
- Bestandsdiff ohne automatische Löschung,
- gemischte oder unbestätigte MEA-Altbestandsskala als BLOCKER,
- RLS und Foreign-ID-404,
- Approval-Transaktion,
- Dokumentstreaming und Seitenquelle,
- Q&A-Quellenvalidierung und sichere Enthaltung.

### 17.3 Frontendtests

- Einstieg beim neuen und bestehenden Gebäude,
- Upload, Reihenfolge und Fortschritt,
- Reviewgruppen und Vorher/Nachher,
- gruppenweise Bestätigung nur für risikoarme Kandidaten,
- BLOCKER und Pflichtentscheidungen,
- PDF-Sprung,
- Freigabezusammenfassung,
- Gebäudewissen, Suche, belegte Antwort und Klärungsstatus.

### 17.4 Adversarial-Tests

- PDF mit Prompt-Injection,
- falscher MIME-Typ und Polyglot-Datei,
- passwortgeschütztes, beschädigtes und übergroßes PDF,
- path-traversal-artiger Dateiname,
- API-Timeout, Rate-Limit und Schemafehler,
- Doppelstart, Doppel-Approve und konkurrierende Worker,
- mandantenfremder Zugriff,
- freigegebene Quelltextstelle mit Prompt-Injection in der Fragenfunktion,
- Fehler mitten in der Approval-Transaktion.

### 17.5 Modell-Evaluation

Das redigierte Referenzkorpus besitzt einen manuell geprüften Goldstandard:

- Kerndaten und normalisierte Werte,
- relevante Regeln,
- Geltungs- und Änderungsrelationen,
- erlaubte Klärungsfälle,
- stützende Dokumentseiten.

Qualitäts-Gates:

- 100 Prozent der kritischen Kerndaten sind korrekt oder ausdrücklich als
  unsicher markiert; kein erfundener Wert,
- 100 Prozent der freigabefähigen Fakten und Regeln haben eine tatsächlich
  stützende Quelle,
- mindestens 95 Prozent Precision und Recall für relevante Regeln,
- keine verfehlte kritische Regel im Abnahmekorpus,
- 100 Prozent der bekannten Änderungsrelationen korrekt,
- 100 Prozent der fachlichen Q&A-Antworten belegt; sonst Enthaltung.

Jeder Eval-Bericht enthält Modell-, Prompt-, Schema- und Harness-Version,
Qualität, Laufzeit, Tokens und Kosten. Ein Wechsel darf keinen kritischen
Sollfall verschlechtern.

## 18. Einführung

Die Implementierung wird in vertikale, testbare Schnitte zerlegt:

1. Persistenz, RLS und dokumentbezogener Upload,
2. Importzustandsmaschine und FakeProvider,
3. lokaler Preflight/OCR und OpenAI-Adapter,
4. Kandidaten, Evidenz, Validierungen und Review-API,
5. Review-UI und transaktionale Freigabe,
6. Regelregister, Suche und Quellenviewer,
7. belegte Fragenfunktion,
8. lokale Referenzevaluation und Betriebsdokumentation.

Ein Feature-Flag hält OpenAI-Extraktion und Q&A deaktiviert, bis API-Schlüssel,
Providerkonfiguration und Referenzevaluation erfolgreich sind. Der
deterministische FakeProvider bleibt in Tests verfügbar.

## 19. Nicht Bestandteil dieser ersten Version

- automatische rechtliche Auslegung ohne menschliche Freigabe,
- automatische Änderung laufender Abrechnungen oder Wirtschaftspläne,
- allgemeines Dokumentenmanagement außerhalb des Property-Kontexts,
- Personen-/Eigentümerimport aus historischen Urkundenteilen,
- autonome Modellwerkzeuge oder externe Aktionen,
- Vektordatenbank und semantische Embeddings,
- Committen der bereitgestellten Originalurkunden,
- produktiver API-Schlüssel oder Deployment im Rahmen dieses Features.

## 20. Definition of Done

Die erste produktive Version gilt als fertig, wenn:

- beide Einstiegswege und mehrteilige Urkundensätze funktionieren,
- Originale unverändert und mandantengeschützt gespeichert werden,
- kein Gebäudewert vor Endfreigabe verändert wird,
- kritische Werte und Regeln einen gültigen Seitenbeleg besitzen,
- Änderungen klauselweise nachvollziehbar sind,
- Widersprüche als Klärungsbedarf sichtbar bleiben,
- die lokale Referenzevaluation alle Qualitäts-Gates erfüllt,
- Kosten und Tokenverbrauch je Lauf sichtbar sind,
- RLS-, Prompt-Injection-, Retry-, Idempotenz- und Rollbacktests bestehen,
- Suche und Q&A nur freigegebene Regeln verwenden,
- jede Sachantwort belegt ist oder sich ausdrücklich enthält.
