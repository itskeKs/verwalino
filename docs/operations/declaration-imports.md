# Teilungserklärungs-Import und Gebäudewissen betreiben

Der Teilungserklärungs-Import ist asynchron und prüfpflichtig: Die API nimmt Dokumente und Review-Aktionen entgegen, der getrennte `worker` führt Vorprüfung, OCR und Extraktion aus. Erst eine berechtigte Person prüft den Entwurf und übernimmt ihn. Kanonische Gebäude- und Einheitsdaten ändern sich nie durch einen Modellaufruf allein.

Das Gebäudewissen zeigt nur freigegebene Fakten, Regeln, Dokumentbeziehungen und Seitenbelege. Die lexikalische PostgreSQL-Suche funktioniert ohne OpenAI. Die optionale Q&A-Funktion liefert nur eine zitierte Antwort aus begrenzt abgerufenen, freigegebenen Belegen und kann separat abgeschaltet werden.

## Grenzen und lokale Vorprüfung

Pro PDF gelten höchstens **25 MiB** (`HV_DECLARATION_MAX_PDF_BYTES`), pro Import standardmäßig zehn Dokumente und 500 Seiten. Die Vorprüfung weist unlesbare, verschlüsselte oder aktive PDFs sowie eingebettete Dateien ab. Sie nutzt zuerst die Textschicht und nur bei Bedarf OCR.

| Bereich | Standardgrenze |
| --- | ---: |
| OCR-Auflösung / Zeit pro Seite | 180 DPI / 30 Sekunden |
| gerenderte Pixel pro Seite | 20.000.000 |
| temporäre OCR-Daten pro Import | 256 MiB |
| OCR-Seiten / Pixel pro Import | 200 / 400.000.000 |
| Vorprüfungsfrist | 600 Sekunden |
| Provider-Seiten / Zeichen je Batch | 20 / 80.000 |
| Eingabe- / Ausgabe-Tokens je Import | 500.000 / 60.000 |
| Ausgabe-Tokens je Provider-Aufruf | 12.000 |
| Recheck-Seiten / Recheck-Aufrufe | 12 / 2 |
| Kandidaten / geschätzte Importkosten | 5.000 / 1,00 USD |

Der Runtime-Container enthält `qpdf`, Poppler (`pdftoppm`), Tesseract mit Sprachpaket `deu` sowie `pikepdf`, `pdf2image` und `pytesseract`. Häufige stabile Fehlercodes sind `PDF_SIZE_LIMIT_EXCEEDED`, `PDF_PAGE_LIMIT_EXCEEDED`, `PDF_UNREADABLE`, `PDF_ENCRYPTED_FORBIDDEN`, `PDF_ACTIVE_CONTENT_FORBIDDEN`, `PDF_EMBEDDED_FILES_FORBIDDEN`, `OCR_UNAVAILABLE`, `OCR_TIMEOUT`, `OCR_FAILED`, `OCR_RENDERED_PIXELS_LIMIT_EXCEEDED`, `OCR_IMPORT_PAGE_LIMIT_EXCEEDED` und `OCR_IMPORT_PIXEL_LIMIT_EXCEEDED`. Dokumentinhalt oder Provider-Antworten nicht in Tickets oder Logs kopieren.

## Provider, Datenübertragung und Geheimnisse

`HV_OPENAI_API_KEY` ist ein ausschließlich serverseitiges Deployment-Secret. Er bleibt in der Secret-Verwaltung beziehungsweise in der Umgebung von `app` und `worker`, nie im Frontend, Build-Argument, Browser, Projekt-`.env`, Git oder Chat. Die mitgelieferte `.env.example` enthält bewusst einen leeren Wert und dient nur als Vorlage für eine geschützte Datei außerhalb des Checkouts, die mit `docker compose --env-file /absoluter/pfad/...` übergeben wird. Ein Codex-Neustart ist nicht erforderlich: Der Schlüssel wird beim nächsten gezielten Start beziehungsweise Neuerstellen des betroffenen API- oder Worker-Prozesses eingelesen; laufende Codex-Agents bleiben davon unberührt.

Die Extraktion verwendet standardmäßig `gpt-5.6-luna`; eine gezielte, begrenzte Nachprüfung verwendet `gpt-5.4-mini`. Q&A verwendet standardmäßig `gpt-5.6-luna` und ist über `HV_DECLARATION_QA_ENABLED=false` vollständig abschaltbar. Dann bleiben Dokumentliste, Regeln und deterministische Suche verfügbar, während Fragen klar als nicht verfügbar beantwortet werden.

Beim Import werden nur nach der lokalen Vorprüfung zugelassene Text- oder OCR-Abschnitte der hochgeladenen PDF-Seiten in begrenzten Batches an den Provider gesendet. Bei Q&A werden nur die normalisierte Frage sowie maximal `HV_DECLARATION_QA_MAX_HITS` freigegebene, zum Gebäude gehörende Fakten und Regeln samt erlaubten Fakt-, Regel- und Quellen-IDs sowie exakten Seitenbelegen gesendet. Es gibt keine Tools und keinen Zugriff auf andere Gebäude, Entwürfe oder Systeme. Der Upload-Dialog weist vor dem Start sichtbar auf diese Übertragung hin.

Beide Adapter setzen `store=false`. Persistiert werden nur technische, sanitisierte Metadaten wie Status, Modell, Token, Kosten, Latenz, Fehlerklasse und Retry-Zähler. Fragen, Zitate, Quelltexte, Antworten, Provider-Payloads, API-Schlüssel und personenbezogene Inhalte gehören nicht in Logs, Budgetabrechnungen oder Support-Tickets.

## Worker, Wiederherstellung und Review

Der Worker beansprucht Aufträge tenantweise mit einem Lease. Jeder Claim hat einen Token und eine Generation; Heartbeats verlängern das Lease nur im gültigen Generation-Kontext. Nach Ablauf kann ein anderer Worker sicher neu beanspruchen, weil verspätete Ergebnisse durch diese Einzäunung nicht mehr publizieren können. `HV_DECLARATION_LEASE_SECONDS`, Polling-Intervall und `HV_DECLARATION_MAX_ATTEMPTS` begrenzen die Wiederherstellung. Bei Unterbruch oder transientem OCR-/Provider-Fehler wartet der Auftrag in `RETRY_WAIT` und ist erst zum hinterlegten Zeitpunkt erneut beanspruchbar.

`POST /api/declaration-imports/{id}/retry` ist nur für retryfähige Fehler (`OCR_TIMEOUT`, `WORKER_INTERRUPTED`, `PROVIDER_TIMEOUT`, `PROVIDER_RATE_LIMIT`, `PROVIDER_SERVER_ERROR`) vorgesehen. `POST /api/declaration-imports/{id}/cancel` sperrt den gerade arbeitenden Lease und verhindert damit eine spätere Veröffentlichung dieses Laufs. Nicht blind wiederholen: Fehlercode, Kosten- und Tokenzähler, Seiten- und Größenlimits zuerst prüfen.

Ein erfolgreicher Lauf endet in `NEEDS_REVIEW`, nicht in einer Anwendung. Prüfende Personen vergleichen jeden vorgeschlagenen Fakt und jede Regel mit den angegebenen PDF-Seiten, bearbeiten oder verwerfen bei Bedarf und tragen die fachliche Verantwortung für die Freigabe. Regeln mit Konflikten bleiben als `NEEDS_CLARIFICATION` sichtbar und sind keine automatische Rechtsauskunft.

MEA-Werte sind exakt als Zähler/Nenner zu behandeln. `CONFIRMED` bedeutet, dass die kanonische Skala für das Gebäude fachlich bestätigt wurde; `LEGACY_UNCONFIRMED` bedeutet, dass ältere Werte vorhanden sind, aber keine Skala stillschweigend ergänzt oder umgerechnet werden darf. Änderungen von MEA oder ihrer Skala erfordern die vorgesehene Bestätigung im Review.

## Lokale API und Worker

Für eine lokale API verwenden Sie die drei Testdatenbank-URLs aus dem nächsten Abschnitt und starten in einer zweiten Shell:

```bash
cd backend
HV_SESSION_COOKIE_SECURE=false ./.venv/bin/uvicorn app.main:app --reload
```

Der produktive Extraktionsprozess läuft getrennt davon:

```bash
cd backend
./.venv/bin/python -m app.workers.declaration_import
```

`FakeDeclarationProvider` und der Fake-Q&A-Provider sind ausschließlich Test-Injektionen, keine Produktionsschalter. Status- und Kandidaten-Endpunkte benötigen eine berechtigte, angemeldete Sitzung; bei der Diagnose nur die Metadaten aus dem Abschnitt „Status sicher prüfen“ verwenden.

## Lokale Tests und Evaluationen

Alle normalen Tests verwenden synthetische, redigierte Daten und machen keine Netzwerk-Anfrage. Die flüchtige Testdatenbank läuft ausschließlich auf Port 55432:

```bash
docker compose -p hv-declaration-tests -f infra/docker-compose.declaration-test.yml up -d --wait
unset hv_database_url hv_database_url_migrator hv_database_url_sync database_url database_url_migrator database_url_sync
export HV_DATABASE_URL='postgresql+asyncpg://app_rw:app_secret@localhost:55432/hausverwaltung'
export HV_DATABASE_URL_MIGRATOR='postgresql+asyncpg://app_migrator:migrator_secret@localhost:55432/hausverwaltung'
export HV_DATABASE_URL_SYNC='postgresql://app_migrator:migrator_secret@localhost:55432/hausverwaltung'
export HV_ENVIRONMENT=test
cd backend
./.venv/bin/python -m app.evals.declaration_knowledge --fixtures tests/fixtures/declaration_eval_cases.json --provider fake
```

Der Fake-Report enthält nur Kennzahlen und schlägt bei festgelegten Qualitäts- oder Sicherheitsgrenzen fehl. Für einen realen, lokalen Smoke-Test wird eine **externe**, nicht versionierte Manifestdatei mit neutralen Case-IDs auf absolute lokale Pfade und deren SHA-256-Hash angelegt. Manifestdatei, PDFs und erzeugte Reports dürfen nie ins Repository gelangen. Der Lauf benötigt gleichzeitig (1) `--provider live --live`, (2) `HV_DECLARATION_LIVE_EVAL=1` sowie einen nichtleeren serverseitigen Schlüssel und (3) `HV_DECLARATION_REAL_PDF_MANIFEST`, das exakt zum übergebenen externen Manifest passt. Der Zielordner für den bereinigten Bericht wird explizit außerhalb des Repositories gewählt. Fehlt eine dieser Sperren, wird kein Dokument geöffnet oder an einen Provider übertragen.

```bash
cd backend
./.venv/bin/python -m app.evals.declaration_knowledge \
  --provider live --live --manifest /absoluter/pfad/manifest.json \
  --report-dir /absoluter/pfad/berichte
```

## Betrieb und Wiederherstellung

Die Compose-Installation führt API und Worker getrennt aus:

```bash
docker compose --env-file /absoluter/pfad/hausverwaltung.env \
  -f docker-compose.prod.yml up -d app worker
```

Der Worker besitzt weder einen Port noch Migrations- oder Session-Secrets; beide Dienste können auf die gemeinsame Dokumentablage zugreifen. Vor einem Update werden die Datenbank **und** das `documents`-Volume als konsistentes Paar gesichert. Ein Wiederherstellungsplan sichert zuerst Schreibzugriffe, restauriert Datenbank und Dokumentvolume aus derselben Sicherungsgeneration, prüft die PostgreSQL-Integrität und die dokumentierten SHA-256-Werte und startet erst danach API/Worker. Ein Restore nur einer Seite kann Datenbank-Referenzen und gespeicherte PDFs auseinanderziehen und ist daher kein gültiger Restore.

## Status sicher prüfen und sauber beenden

Die Statusantwort enthält nur Metadaten. Relevante Felder sind `status`, `error_code`, `current_stage`, `input_tokens`, `output_tokens`, `estimated_cost_usd`, `attempt_count` und `max_cost_usd`. Bei `FAILED` oder `RETRY_WAIT` wird mit diesen Metadaten gearbeitet, nicht mit Dokumenttexten.

Lokal gestartete Uvicorn- und Worker-Prozesse werden im jeweiligen Vordergrundterminal mit `Ctrl-C` beendet. Die isolierte Testdatenbank nur über ihren exakten Projektnamen stoppen:

```bash
docker compose -p hv-declaration-tests -f infra/docker-compose.declaration-test.yml down
```

Die bereitgestellten Referenz-PDFs bleiben lokal; sie werden weder nach Git noch in Test-Fixtures kopiert und nicht ohne den expliziten Live-Eval-Lauf an einen Provider übertragen.
