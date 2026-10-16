# Ausführungsvertrag — 64k / 65536 Kontextziel

Nur diesen Vertrag, `state.md` und **einen** aktuellen Teilplan laden. Nicht den
Gesamtplan, alle Teilpläne, die ganze Parity-Historie oder das ganze Repository lesen.

## Aktiver Dokumentreparatur-Scope

Umsetzungsvorgabe `mlx/qwen3.6-35b-a3b`; Goal-Checker `openai/gpt-6.1-sol`,
`reasoning: medium`. Das Kontextziel 64k / 65536 ist keine gemessene Backendkapazität;
`max_context_tokens` allein belegt weder das wirksame Fenster noch Laufzeitkonformität.
Keine automatische Konfigurationsänderung. Historische 49k-/49152-Nachweise bleiben
unverändert; Identifier `crossreview-remediation-qwen36-49k` bleibt erhalten.

Aktuell gilt ausschließlich der [Reparatur-State](review-remediation/state.md), nicht die Produkt-
Roadmap. 04a lokal abgeschlossen, 04b nicht begonnen, Produktionsgate 0122,
lokaler Testhead 0123. Keine Produktcode-/Teständerungen oder Portal-/Household-/
Former-Resident-Implementierung. Frühere bedingte Codeanweisungen sind hier inaktiv.
V01 Accounting/Legal, HTTP-401-Goal-Updateblocker, Zwei-Reviewer-Quorum und der
unvollständige Backend-Gesamtlauf bleiben offen. Goal nur formulieren, nicht starten.
Keine Secret-/Credential-Suche, Staging, Commits, Pushes oder Deployments.

## Kontextbudget und Arbeitsweise
- Zielmodell: `mlx/qwen3.6-35b-a3b` mit Kontextziel 64k / 65536 Tokens Gesamtkontext, einschließlich System- und
  Toolanweisungen. Arbeitsziel höchstens 28.000 belegte Tokens: Anweisungen +
  State + Übergabe ≤8.000, aktueller Code/Tests ≤16.000, Ergebnisse ≤4.000;
  mindestens 15.000 Reserve, 6.000 Sicherheitsabstand. Das sind Planungsbudgets,
  keine tokenizer-unabhängig gemessenen Größen. Nicht alle Maxima ausschöpfen.
- Nach einem Teilplan **immer** Kontextwechsel. Schon bei etwa 32.000 belegten
  Tokens oder wiederholten verdrängten Reads: Zwischenstand schreiben und wechseln.
- Nur 2–4 relevante Dateien pro Leseschritt. Vor Änderungen vollständige betroffene
  Datei lesen, bei langen Dateien alle notwendigen Seiten. Keine wiederholten Reads
  ohne konkrete Frage; dauerhafte Erkenntnisse in der Receipt speichern.
- Auf höchstens einen kohärenten Implementierungs-/Testschritt arbeiten. Wird ein
  Auftrag größer: vorab weitere kleine Dateien mit gleicher Übergaberegel anlegen,
  aktuellen Plan und `state.md` auf den ersten neuen Schritt umleiten. Verbleibende
  Anforderungen und Anschluss an den bisherigen Nachfolger erhalten.
- Pfade im Originalauftrag sind teilweise Vorschläge, keine existierenden Dateien.
  `api/`, `services/`, `models/`, `core/`, `workers/`, `engine/`, `repositories/`,
  `integrations/`, `exports/` bedeuten `backend/app/…`; unqualifizierte Pages liegen
  unter `frontend/src/pages/`. Erst gezielt suchen; nichts anhand eines Pfades erfinden.
- Design nur für den aktuellen P/A-Abschnitt nachschlagen; relevante Entscheidungen
  in Receipt übernehmen. Register nicht als komplette Zusatzanweisung laden.

## Anti-Schleifen-Protokoll (für jeden Teilplan verbindlich)
1. Am Start `pwd`, `git status --short`, Interpreter/Toolchain und laufende Tasks
   prüfen. `current` ist die einzige Auftragsquelle; bei widersprüchlichen Angaben
   State korrigieren, nicht den gesamten Plan neu durcharbeiten.
2. Vor Codeänderung eine überprüfbare Hypothese und den kleinsten unveränderten
   Regressionstest nennen. Fehler bis zur tatsächlich auslösenden Operation lesen;
   Annahmen (z. B. flush = Prüfung deferred Trigger) nicht als Fakten speichern.
3. Pro Fehler höchstens zwei widerlegte Lösungsansätze ohne neue Evidenz. Danach
   gezielt eine neue Diagnose ausführen oder `blocked` mit benötigtem Input setzen.
   Denselben Test nur nach relevanter Änderung oder neuer Diagnose wiederholen.
   Keine globale Middleware/Transaktionsänderung für einen lokalen Fehler ohne Beleg.
4. Fehlversuche im aktuellen Receipt kurz halten: Hypothese → beobachteter Fehler →
   verworfen. Bei Wiederaufnahme diese Liste prüfen, nicht erneut dieselben Varianten
   ausprobieren. Task mit kleinstem sicheren Diff abschließen; kein Nebenrefactoring.
5. Langläufer im Hintergrund, DB-Tests seriell. Task-ID/Logpfad festhalten; niemals
   einen zweiten identischen Lauf starten. Bei fehlendem Tool nur Toolchain beheben,
   nicht Tests überspringen. Shell-Erfolg ist kein pytest-Erfolg (`; echo` verdeckt ihn).
6. Nach spätestens 15 Minuten ohne neuen Befund oder vor Kontextverlust State und
   Receipt speichern: konkrete Fehlersignatur, letzter Test/Exitcode, Dirtypaths,
   laufende Tasks, genau eine nächste Aktion. Bei fehlender Operatorentscheidung
   ausdrücklich fragen und stoppen. Keine fünfminütige stille Denk-/Warteschleife.
7. Abnahme: erforderliche Tests unverändert, keine neuen Skips, lokale/Release-Gates
   getrennt. Logs auf Disk speichern; im Kontext nur Ergebnis und Fehlerausschnitt.
   `current` erst nach erfüllter Abnahme weitersetzen; State nach Schreiben erneut lesen.
   Erst dann Kontextwechsel, niemals sämtliche Nachfolger laden.

## Anforderungen nachschlagen
Bei einer fehlenden A-/RF-Zuordnung nur die relevante Zeile in `traceability.md`
nachschlagen; keine Einschränkung des Designs durch den begrenzten Teilauftrag.
Bestehende ADRs gelten weiter: `docs/adr/0003-gobd-unveraenderbarkeit-mechanik.md`,
`0006-valid-to-konvention.md`, `0007-weg-abrechnungsspitze-und-wirtschaftsplan.md`
und `0010-vermoegenstrennung-zahlungen.md` (alle im selben ADR-Verzeichnis).

## Unverhandelbare Integrität
- Existierende Architektur, PostgreSQL-RLS und reine Decimal-Engines erhalten.
  Mandate/Sphären, Quellenzugriff und finanzielle Effekte nicht vermischen.
- `backend/app/core/tenant.py::get_db()` besitzt die Requesttransaktion; Services
  flushen, keine unabhängigen Commits. Heavy OCR/Rendering außerhalb langer DB-TX.
- Additive Migrationen nach **tatsächlichem** Head; akzeptierte History nie editieren.
  Expand → evidenzbasierter Backfill → Counts/Hashes/Geld reconciliieren → Constraints
  → Writerwechsel. Unknown bleibt Unknown. Alte Finalhashes/Quellen unverändert.
- RLS mit `app_rw`, Composite-Scope-FKs, Businessuniqueness zusätzlich zu Idempotenz.
  Verändertes Payload bei gleicher Key: 409; Rechte auch bei Replay/Jobs rechecken.
  Stale Revision: 409. Quellenlink ist keine Zugriffsgewährung.
- Pro Teilplan: Regression zuerst für eine konkrete Lücke, Happy Path, Failure/Retry,
  negative Org-/Scope-Rechte; A31/A32 gelten quer. Keine Schema-only-Abnahme.
  Integrity-/Security-Grenzen unabhängig reviewen; fehlendes Review offen nennen.
- Rollback stoppt neue Writer/Claims, erhält Evidenz/Reads; Forward-repair statt
  Records löschen. Historische Accepted-Evidenz nicht als neue Testausführung ausgeben.
- Keine echten Provideraktionen, Versand, Kauf, Push, SSH oder Deployment ohne
  ausdrückliche passende Autorisierung. Fake/Synthetic lokal; keine Modell-/Prompt-/
  Declaration-Providerpolicy-Änderung. Test-DB vor Reset explizit als disposable prüfen.
- Shell/Interpreter/Env zuerst prüfen (`python3`/Projektvenv, `command -v rg`).
  Fehlende Tools nicht voraussetzen; für Node Projektinstallation/PATH prüfen.
  Keine Credentials erfinden; USER/HV_OPENAI_API_KEY aus Testprozessen entfernen.
  DB-Tests seriell. Vorhandene Frontendskripte: test, lint, build; kein behauptetes E2E.
- Nicht-owned Änderungen/untracked `docs/research/` unangetastet; ein Writer.
  Kein automatisches Staging, Commit oder Push in diesem Auftrag.

## Freigaben (nie selbst erfinden)
V01 Accounting/Legal: eligibility/VAT/WEG/reserves/mixed scope, Golden Cases.
V02 Heating/CO2: applicability, exceptions, specialist formulas/fixtures.
V03 Accounting: periods/coverage/basis/tax/schema/target importer.
V04 Integration/Product: contracts/institutions/channels/secrets/sandbox/idempotency,
separate live authorization.
V05 Org admin: versioned team approval/separation, deny self-approval.
V06 Privacy/Security: audience/former residents/processor/holds/retention/artifacts.
V07 Product: parity depths, companion architecture, signature/settings matrix.
V08 Operations/Product: measured hardware/load/SLO/RPO/RTO and org budgets.
V09 Specialist/Product: voting/proxy/notices/rent/templates/legal methods.
Offene Gates blockieren betroffene Release-/Methodenaktivierung, nicht unabhängige
Fake-/lokale Arbeiten. Nur gated Umfang: `blocked`; nie als erledigt umetikettieren.

## Abschluss und echter Kontextwechsel
1. Receipt in `receipts/<plan-id>.md`: Taskstatus, HEAD/Worktree/owned diff,
   tatsächlicher Migrationshead vs akzeptierter Head, relevante Interfaces/Invarianten,
   neue oder erhaltene Nachweise, exakte Checks mit Ergebnis/Umgebung, Fehler,
   Blocker/V-Gates, nächste konkrete Aktion; max. ca. 1.000 Wörter. Keine Chatkopie.
2. `state.md` aktualisieren, max. ca. 1.200 Wörter: `current`, `next`, letzter Receipt,
   lokale/Release-Status getrennt, Gate-Tabelle, Dirtypaths und laufende Prozesse.
   Abgeschlossene Historie nur als Receiptlinks, nicht kumulativ hineinkopieren.
3. `done-local` nur mit bestandenen erforderlichen lokalen Prüfungen;
   `blocked`/`in-progress` mit konkreter fehlender Voraussetzung. Nächsten Plan nur
   beginnen, wenn seine Dependencies lokal belegt oder ausdrücklich unabhängig sind.
4. Coddy mit verfügbarem `compact_context`: kompaktieren, mit Anleitung nur Einstieg,
   state/current/next, letzte Receipt, Dirtypaths, Checks/Blocker zu erhalten und alte
   Dateiinhalte/Pläne/Logs zu verwerfen. Danach **nur** state, Vertrag und next lesen.
   Compaction spart Kontext, ist aber kein garantierter komplett leerer Kontext.
5. Ohne wirksame Compaction oder wenn danach Budget weiter überschritten: anhalten
   und folgenden Auftrag für eine **frische Sitzung** ausgeben (Dateien persistent):
   `Lies docs/project-completion/README.md, state.md, contract.md und ausschließlich
   den in state.md unter current genannten Teilplan. Verifiziere den letzten Receipt
   gezielt. Implementiere diesen Teilplan und führe seine Übergabe aus.`
   Nicht den nächsten Plan im alten vollen Kontext öffnen. Ein Markdownlink startet
   kein Modell; Übergabe ist eine Agentenanweisung, kein Hintergrundprozess.
