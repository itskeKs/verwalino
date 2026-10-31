# 04a-replacement-outcomes — Receipt

execution: done-local
checked: 2026-10-12

## Workspace und Grenzen
Workspace `~/Developer/hausverwaltung`, HEAD
`aeee229b1bbb7415b35c5c05985a866d25a75210`. Historisch akzeptierte Basis `dee9657`,
Migration 0122; tatsächlicher Test-DB-Head 0123. Keine Migration verändert, kein
Deployment/Commit/Push, keine Produktions-/Parityfreigabe.

Owneddiff: `backend/app/services/exception_service.py`, 48 Testdateien plus conftest und zwei neuen Testhelpern
(Liste in [Setupaudit](../evidence/04a/migration-setup-audit.txt)), Completiondoku.
Vorbestehender Oktoberplan/Parityregister erhalten; `docs/research/` unangetastet.

Vor jüngstem Lauf disposable DB erneut verifiziert: Composeprojekt
`hausverwaltung-declaration-test`, Container `hausverwaltung-declaration-test-postgres-1`,
Konfiguration `infra/docker-compose.declaration-test.yml`, 127.0.0.1:55432,
PostgreSQL 16, `/var/lib/postgresql/data` auf Tmpfs, DB `hausverwaltung`,
Eigentümer `app_migrator`, Head 0123. Kein Zugriff auf regulären DB-Container.

## Diagnose / Lösung / unveränderte Invarianten
`seal_exception_completion` ist ein deferred Constraint-Trigger (0110/0123).
`ExceptionService.complete()` prüft ihn nach dem atomaren DB-Command mit
`SET CONSTRAINTS seal_exception_completion IMMEDIATE` und stellt `DEFERRED` wieder
her. `DBAPIError.orig.sqlstate == '23514'` wird vor Response-Start HTTP 422.
`get_db()` bleibt Besitzer der Requesttransaktion und ihres Rollbacks; RLS/SET LOCAL
bleiben erhalten. Kein Service-Commit, kein schwächerer Seal.

Verworfen, nicht wiederholen: flush-only erzwingt deferred Trigger nicht;
Service-Commit verliert Tenant-/Transaktionskontext; Rollback-only-Dependency
verwirft normale Writes; globale Middleware kann nach Response-Start keine zweite
Antwort senden. `tenant.py` unverändert, experimentelle Middleware entfernt.

Operator autorisiert am 2026-10-11 die Korrektur **aller historischen Test-Setups**,
nicht ihrer Assertions/Erwartungen/Testanzahl/Umfang. Alle 0122-Downgrade-Ketten
bauen nun zuerst 0123 ab und bei vollständigem Wiederaufbau auch wieder auf.
Ursache des FK-Fehlers war der sonst verbleibende `completion_replacement_receipt_fk`.
Zwei Access-Coverage-Setups bauen zuerst neuere Revisionen bis 0088 geordnet zurück:
Der aktive Wrapper um `access_coverage_manifest_pre_intake` enthält nicht die alten
0088-Patchmarker. Akzeptierte Migrationen/Retention-Assertions bleiben unverändert.

AST-Vergleich: nur ausdrücklich autorisierte Compose-Funktion Assertion-Ausnahme;
sonstige Assertions und alle Testnamen/Parametrisierungen unverändert; sämtliche 0122-Downgrade-Sites geprüft.
[Audit](../evidence/04a/migration-setup-audit.txt),
[exakter Testdiff](../evidence/04a/migration-setup-diff.txt). `git diff --check` grün.

## Checks und exakte Umgebung
Aktuelle Backendenv: ab bg_257 IPv4, vorher localhost. Projektvenv Python 3.13.13; alle DB-Läufe seriell:
```bash
cd backend
export HV_DATABASE_URL_MIGRATOR='postgresql+asyncpg://app_migrator:migrator_secret@127.0.0.1:55432/hausverwaltung'
export HV_DATABASE_URL='postgresql+asyncpg://app_rw:app_secret@127.0.0.1:55432/hausverwaltung'
export HV_DATABASE_URL_SYNC='postgresql://app_migrator:migrator_secret@127.0.0.1:55432/hausverwaltung'
env -u USER -u HV_OPENAI_API_KEY .venv/bin/python3 -m pytest -v --tb=short -rs
```
Historische Diagnose bg_189:1551pass/1040fail/146error/2Skips,Exit1,1209.09s;
[Log](../evidence/04a/backend-full-corrected-failed.txt). Auth-Rehearsals nun isoliert
0028,Original-DB im Finally restauriert; bg_193:20pass,41.07s,
[Beleg](../evidence/04a/auth-isolation-regression.txt). Registrierte Graphen retained,
guarded Cleanup ohne CASCADE erhält globale Seeds/Owner. Frühere Teardownlogs
in evidence/04a/legacy-backfills-first-failed.txt,legacy-corrected-first-failed.txt,
legacy-retention-first-failed.txt; keine historische rote Suite zur Abnahme erklärt.
`bg_205`: vollständige Source-Alias-, New-/Existing-Property-, Knowledge-Search-/Query-
und Auth-Dateien, **153 passed**, Exit 0, 89.19s, keine Skips.
[Fixture-Regression](../evidence/04a/fixture-retention-regression.txt). Danach Head 0123,
keine Sicherungsreste; Compose/Tmpfs/Owner vor neuer Gesamtsuite erneut verifiziert.
`bg_206`: **timed_out**, 3600s, Exit -1; kein erfolgreiches Gesamtergebnis.
Teil-Log 915 PASSED/5 FAILED, Hänger vor Publication-Order-Test bei etwa 34%.
[Timeout-Log](../evidence/04a/backend-full-isolated-timeout.txt). Danach Head 0123,
keine aktiven DB-Sessions/Sicherungsreste. `bg_215` gestoppt nach reproduziertem advisory ShareLock-Zyklus zwischen
HTTP-Writer und zusätzlichem app_rw-Leser. [Lockbeweis](../evidence/04a/publication-order-lock-proof.txt).
Observer jetzt unabhängige Migrator-Testsession; HTTP/RLS/Locks unverändert.
`bg_217`:10 passed/4 failed,13.69s; Publication und Allocation-Fall bestanden.
Ledger-Helper auf Produktions-Worker-Session umgestellt; ursprünglicher 0028-
Katalogtest isolierter Rehearsal, aktuelle Worker/RLS-Tests weiter0123, keine
Policies entfernt/gefiltert. `bg_218`: **14 passed**,Exit0,13.20s; beide Publication-Tests, Ledgerdatei und
Allocation-Fall. [Regression](../evidence/04a/timeout-corrected-regression.txt). Danach
Head0123, keine Sicherungsreste, Compose/Tmpfs/Owner bestätigt; Migrationen/tenant.py
unverändert. `bg_219` **Exit1:2367 passed/135 failed/184 errors/2 bestehende Skips**,1087.85s.
[Log](../evidence/04a/backend-full-after-timeout-failed.txt). Danach Head0123, keine
Sicherungsreste, cost_category=0. Eigener dynamischer Cleanup ließ diese globale
0005-Seedtabelle versehentlich im TRUNCATE-Satz; nun Retain-Liste ergänzt, keine
Migration/Assertion geändert. `bg_220` **20 passed**,Exit0,95.26s: Publication-Rehearsal→vollständige Completion/
Replacement-Dateien. [Regression](../evidence/04a/reference-seed-retention-regression.txt).
Danach Head0123,30 cost_category,keine Sicherungsreste; Fix belegt.
Worker-Namespace-Fixture nun frische2Orgs/vierRollen/Mandates pro Fall; reale
unscoped Directory-Query (SpySession) vor Auswahl der Fall-Orgs. Kein Delete
retained Imports/Docs, keine produktive Claim-/RLS-Änderung. `bg_221`:2 passed,Exit0,7.08s (Ledger-Retry-Vorgänger→ersterClaim).
[Regression](../evidence/04a/worker-namespace-first-regression.txt). `bg_222`:**21 passed**,Exit0,9.44s, ganze Worker-Datei inkl.Directory-Spy/Fairness/
Fencing/Heartbeat/Shutdown. [Regression](../evidence/04a/worker-namespace-regression.txt).
Danach Head0123,30cost_category,keine Sicherungsreste.
Früherer Compose-Konflikt nach expliziter Freigabe in bg_223 geprüft. Keine
Credentials/Prodtopologie geändert; übrige Einzelfehler gezielt aus Log lesen. Vorher defekte Disposable-DB nach
Eigentums-/Tmpfscheck neu erstellt. Renderer-Fall im Gesamtlauf fehlgeschlagen,
im gezielten Folgelauf bisher bestanden; Ursache ungeklärt. Zwei bestehende
Skips betreffen Live-PDF-Gates/Runtime-Image, keine neuen Skip-Bedingungen.

Historische Läufe mit denselben Rollen/DB, Host localhost, `-v --tb=short`:
- `tests/test_replacement_exception_outcomes.py::test_replacement_completion_live_audience_and_atomic_seal`:
  **1 passed**, Exit 0, 18.18s; 422, atomarer Rollback, unveränderte Revision,
  Retry 201, Org-/Scope-Denial 404. [Sealbeleg](../evidence/04a/seal-regression.txt).
- `tests/test_exception_completions.py::test_completion_sealed_membership_coverage_and_guarded_roundtrip`:
  **1 passed**, Exit 0, 8.75s. [Roundtrip](../evidence/04a/roundtrip-regression.txt).
- `tests/test_access_coverage.py::test_capture_waits_for_live_admin_and_migration_retains_snapshots`
  und `tests/test_projected_access_coverage.py::test_projection_migration_retains_v2_and_preserves_v1`:
  **2 passed**, Exit 0, 9.42s. [Accessroundtrips](../evidence/04a/access-roundtrip-regression.txt).
- Vollständige relevante Matrix: `tests/test_replacement_exception_outcomes.py
  tests/test_exception_completions.py tests/test_allocation_source_replacements.py
  tests/test_allocation_corrections.py tests/test_allocation_adoptions.py
  tests/test_intake_batches.py tests/test_intake_extraction_worker.py
  tests/test_intake_inspection.py tests/test_workflow_runner.py`:
  **130 passed**, Exit 0, 188.60s, keine Skips; Happy Path, Failure/Retry,
  Rechte, historische Ketten und Worker-Unterbrechung geprüft.
  [Matrixlog](../evidence/04a/relevant-tests-corrected.txt).
- Unverändertes Frontend, `PATH=$HOME/.local/share/mise/installs/node/22.23.1/bin:$PATH`:
  `npm test` **318/318**, `npm run lint` und `npm run build` Exit 0;
  Bundlegrößenwarnung. [Tests](../evidence/04a/frontend-tests.txt),
  [Lint](../evidence/04a/frontend-lint.txt), [Build](../evidence/04a/frontend-build.txt).
  Kein UI-Code verändert, keine echte Browserjourney behauptet. Erster Versuch mit
  fehlendem npm (Exit 127) wurde durch den erfolgreichen Toolchainlauf ersetzt.

Historische Diagnose, nicht aktuelle Abnahme: frühere Matrix 125 passed/5 failed;
unterbrochene Gesamtsuite 226 passed/3 failed, Exit 2. Die FK-/0088-Setupfehler sind
in den oben genannten Regressionen korrigiert. SSL-Ursache später alsIPv6selfconnectbelegt.

## Qwen / nächste Aktion / Release
Qwen3.8/49k: State → Vertrag → ausschließlich current, Receipt gezielt; kurze
Budgets, Anti-Schleifen-Regeln und echter Kontextwechsel. Keine pauschale
semantische/fachliche Qualifikation aller Repositorydokumente.

Operator „ja go“ autorisiert ausschließlich aktuelle Compose-Erwartung und RLS-
Inventar/Coverage. `bg_223`:**40 passed,1alter Runtime-Image-Skip**,Exit0,6.35s.
[Regression](../evidence/04a/current-contract-regression.txt).25Tabellen org_id+
ENABLE/FORCE RLS;14deny-all PrivatePolicies, übrige Tenant-Predicate-Schleifen.
[Katalog](../evidence/04a/rls-inventory-catalog-audit.json). Compose geparste Worker-
Rolle app_worker; API-/Migrator-URLs verboten. Prodconfig/Policies unverändert.
`bg_224`:**2 passed**,Exit0,9.51s, Budget-/Projection-Roundtrips.
[Regression](../evidence/04a/historical-catalog-regression.txt). Nur guarded Cleanup/
neuerer Rückbau im rückgerollten Savepoint, Retention-Assertions unverändert;
danach Head0123,30Categories,keine Sicherungsreste.
`bg_225`:83passed/1SetupError,Exit1,51.51s; PAUSED-Projection erster Migrator-
Connect rejected SSL upgrade, keine Fachassertion. [Fehllog](../evidence/04a/post-seed-cascade-first.txt).
Server ssl=off,Head0123,30Categories,keine Sessions/FATAL-Logs. Ressourcenhypothese
nicht belegt. `bg_226`:84passed,53.56s,503 originale SSL-Callbacks ohne Repro;
[Diagnose](../evidence/04a/post-seed-wire-diagnostic.txt), keine Reparatur behauptet.
`bg_227`:**Exit1,15failed/2664passed/7errors/2bestehendeSkips**,1211.35s pytest/1217s.
[Vollrun](../evidence/04a/backend-full-current-contracts-failed.txt). Head0123,
30Categories,Owner/Tmpfs/keine Sicherungsreste bestätigt.11historische Rehearsal-
Fehler,4funktionale Assertions(privacy,bindingAudit,scopedJob,valid_toCoverage),
7property_document_content FK-Teardown-Errors; kein SSL-Error aufgeführt.
`bg_228`:**24passed**,Exit0,76.48s/81sTask: ganze Visual-Allocation-Datei→2Rehearsals.
[Regression](../evidence/04a/visual-rehearsal-adjacency.txt),
[Retentionproof](../evidence/04a/visual-rehearsal-retention-proof.txt). Nur guarded
positiver Savepoint-Cleanup/Finanzgraphbereinigung vor0121; negative Visual-Evidenz
bleibt. Head0123/30Categories/keine Sicherungsreste,32Visual-Sets/10Corrections
weiter vorhanden. AST40Dateien Assertions unverändert außer freigegebener Compose-
Funktion, sämtliche Namen/Parametrisierungen unverändert. Keine Prod-Migrationen.
bg_229:11passed,12.26s,[Content/Registry](../evidence/04a/content-registry-regression.txt);
registrierte Graphen retained. bg_231:5pass/4fail,21.28s,
[Historische Diagnose](../evidence/04a/historical-remaining-first.txt).5Roundtrips
geordnet im Savepoint grün; Link-Restoration jetzt konkrete Graph-/START-Request-
Zeilen und aktive DRAFT→QUEUED-Guards/FKs, keine Abschaltung. bg_233:23passed,
15.41s,[Funktional](../evidence/04a/functional-remaining-regression.txt); Actor-GUC,
Coverage und validierter versionierter Outcome nur Setup, Assertion-AST unverändert.
Privacy jetzt identische frische M3-Personen pro Fall statt geteilten retained Outputs.
bg_235:**114passed**,Exit0,64.22s/69sTask, zehn komplette betroffene Dateien;
[Adjacency](../evidence/04a/remaining-file-adjacency.txt), alle Link-/Privacy-Cases.
Danach Head0123/30Categories/Owner/Tmpfs/keine Sicherungsreste bestätigt; AST49Dateien
unverändert außer erlaubter Compose-Funktion, sämtliche Namen/Parametrisierungen.
bg_236 **Exit1:2678passed,2alteSkips,1SetupError**,1233.47s/1240sTask:
[Vollrun](../evidence/04a/backend-full-final-failed.txt). approved_knowledge_graph
Foreign-Migrator-Connect bei query_endpoint_no_hit_short_circuits_disabled_provider:
asyncpg rejectedSSLupgrade. Keine Fachassertion rot. DB0123/30Categories/Owner/Tmpfs/
keineSicherungsreste bestätigt; keineSSL-/Resource-Logs,Admin-FATALfrüheresRehearsal.
bg_238 **Exit0:600Connects+36QueryTests**,12.33s/32sTask;
[Diagnose](../evidence/04a/query-wire-diagnostic.txt),
[Runner](../evidence/04a/query-wire-diagnostic-runner.txt).
679nativeCallbacks alleN/advisory/CERT_NONE,keineunerwartetenBytes/keineRepro.
KeinTLS-Override/Retry,keinebehaupteteReparatur/Abnahme.
Operator „Capture läuft“:PID93438. bg_256 **Exit1**,3000SQLAlchemy+308asyncpg;
[Diagnose](../evidence/04a/captured-transport-diagnostic-failed.txt),
[Runner](../evidence/04a/captured-transport-diagnostic-runner.txt).
[Selfconnectbeweis](../evidence/04a/ipv6-self-connect-proof.json):
peer==local `[::1]:55432`,eigenerSSLRequest `0000000804d2162f` stattN.
NurIPv4Listener;localhostIPv6zuerst,55432imEphemeralbereich49152–65535.
IPv4-PCAP erfasstnichtIPv6fall;Ursachennachweis ausnativemSocketcallback.
FixnurTestenv127.0.0.1,Guarderlaubt;TLS/Assertionenunverändert.
bg_257 **Exit0:6000Connects+78passed**,84.34spytest/261sTask;
6079nativeN-Antworten,allePeers127.0.0.1,TLSunverändert.
[Verifikation](../evidence/04a/ipv4-transport-regression.txt),
[Summary](../evidence/04a/ipv4-transport-summary.json).
CapturePID93438gestoppt;DB0123/30Categories/Owner/keineSicherungsreste/keineanderenSessions.
bg_261 **Exit0:2679passed,2bestehendeopt-inSkips**,1225.29spytest/1231sTask.
[Vollnachweis](../evidence/04a/backend-full-ipv4.txt),
[Summary/Skipnodes/DB-Endcheck](../evidence/04a/backend-full-ipv4-summary.json),
[Runner](../evidence/04a/backend-full-ipv4-runner.txt).
10929nativeN-Antworten,allePeers127.0.0.1,keineAbweichung.
DB-Endcheck0123/30Categories/Owner/Tmpfs/keineSicherungsreste/keineanderenSessions.
[FinalerAST-Audit](../evidence/04a/final-integrity-audit.json) bestätigt49Dateien.
**Handoff:state.current=04b;frischerKontext,04bhiervorbereitet,nichtgeladen/implementiert.**
Prod-Migrationen/tenant.py unverändert, keine neue TLS-/Skip-Umgehung.

V01–V09, Integrity/Security-Review und echte Runtime-/Ops-/Capacity-Gates bleiben
offen. Lokale Tests ersetzen keine Produktions-, Scanner- oder Parityfreigabe.
Keine laufenden Tasks;Capturegestoppt;keineServer/Watcher.
