# Teilungserklärungs-Import und Gebäudewissen betreiben

Der Teilungserklärungs-Import ist ein asynchroner, prüfpflichtiger Workflow.
`DeclarationPipeline` ist der feste, deterministische Manager: Sie führt eine
begrenzte lokale Vorprüfung, feste Extraktionspässe, lokale Validierungen und
höchstens einen begrenzten Vision-Recheck aus. Sie ist kein frei autonomer
Agent. Dokumentseiten, OCR-Text und Modellantworten sind **untrusted data**;
sie erhalten keine Tools, keine Aktionssenke und keine Berechtigung, Daten zu
ändern. Erst **Human Approval** übernimmt einen geprüften Entwurf in
kanonische Gebäude-, Einheits- oder Regel-Daten.

Das Gebäudewissen zeigt nur freigegebene Fakten, Regeln, Dokumentbeziehungen
und Seitenbelege. Die lexikalische PostgreSQL-Suche funktioniert ohne einen
Modellaufruf. Die optionale Q&A-Funktion liefert ausschließlich eine zitierte
Antwort aus begrenzt abgerufenen, freigegebenen Belegen und kann separat
abgeschaltet werden.

## Betriebsfluss und Modellmatrix

```text
PDF -> lokale Vorprüfung/OCR -> textuelle Luna-Pässe -> lokale Evidenz-/Domain-Validierung
       -> RouterDecision(NONE | HUMAN_REVIEW | VISION_RECHECK)
       -> nur ausgewählte, lokal gerenderte PNG-Seiten an Mini
       -> erneute Validierung -> NEEDS_REVIEW -> Human Approval
```

| Zweck | Modell / Reasoning | Zulässige Eingabe | Grenze |
| --- | --- | --- | --- |
| reguläre Extraktion (`inventory`, `extract_core`, `extract_rules`, `reconcile`) | `gpt-5.6-luna`, `medium` | begrenzte Text-/OCR-Batches bzw. strukturierte Zwischenergebnisse | keine Bilder, keine Tools, kein `previous_response_id` |
| Vision-Recheck | `gpt-5.4-mini`, `none` | nur von `RouterDecision` ausgewählte, lokal gerenderte PNG-Seiten plus begrenzter Kontext | keine regulären Luna-Pässe mit Bildern; Renderfehler ist fail closed |
| Eval-Kandidaten | Luna `low`, Terra und Luna mit Mini-Router | ausschließlich gleiches Goldset | keine automatische Promotion und kein Produktionsrouting |

Der lokale `route_escalation`-Router nutzt nur reproduzierbare Signale. Er ist
kein learned oder online lernender Router und verwendet keine Modell-Confidence:

| Vorrang | Lokales Signal | `RouterDecision` |
| ---: | --- | --- |
| 1 | isolierter Router-Contract: `PROVIDER_REFUSAL`, `PROVIDER_INCOMPLETE`, `PROVIDER_TIMEOUT` oder `PROVIDER_SCHEMA_FAILURE` | `HUMAN_REVIEW` |
| 2 | unbekanntes Dokument oder Seite außerhalb der Vorprüfung | `HUMAN_REVIEW` |
| 3 | fehlende Evidence-Quote auf bekannter, renderbarer Seite | `VISION_RECHECK` |
| 4 | geringe lokale Seitenqualität auf zitierter/Blocker-Seite | `VISION_RECHECK` |
| 5 | fachlicher Blocker ohne visuelles Signal | `HUMAN_REVIEW` |
| 6 | kein Signal | `NONE` |

Der Router sortiert und dedupliziert Seiten und begrenzt sie mit
`HV_DECLARATION_MAX_RECHECK_PAGES`; zusätzliche Aufrufe bleiben durch
`HV_DECLARATION_MAX_RECHECK_CALLS`, Token-, Kosten- und Deadline-Limits
begrenzt. Eine nicht renderbare gewählte Seite wird nicht alternativ hochgeladen
und führt zu Human Review.

Der produktive Pipelinepfad ruft den Router aktuell mit
`provider_failure=None` auf. Providerfehler werden daher nicht in Vision
umgeleitet, sondern folgen direkt der bestehenden Workerpolitik nach
`RETRY_WAIT` oder `FAILED`. Die erste Tabellenzeile dokumentiert nur den
isolierten, totalen Router-Contract: Falls einer seiner vier zulässigen
Providerfehler dort übergeben wird, ist ausschließlich `HUMAN_REVIEW`
zulässig.

## Lokale Vorprüfung und Evidence-Gate

Pro PDF gelten höchstens **25 MiB** (`HV_DECLARATION_MAX_PDF_BYTES`), pro
Import standardmäßig zehn Dokumente und 500 Seiten. Die Vorprüfung weist
unlesbare, verschlüsselte oder aktive PDFs sowie eingebettete Dateien ab. Sie
verwendet erst die Textschicht und nur bei Bedarf OCR.

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

Jede **Evidence-Quote** muss lokal auf der exakt angegebenen
`(document_key, pdf_page)` vorkommen. Der Validator normalisiert Unicode,
Zeilenumbrüche, Whitespace und Bindestriche und verlangt einen nichtleeren,
zusammenhängenden Treffer. Eine Seitenzahl, Abschnittsnummer oder Modelltext
ist kein Ersatzbeleg. Fehlercodes wie `EVIDENCE_QUOTE_NOT_FOUND`,
`UNKNOWN_EVIDENCE_DOCUMENT` und `EVIDENCE_PAGE_OUT_OF_RANGE` enthalten keinen
Dokumenttext und dürfen diesen auch nicht in Ledger, Logs oder Tickets bringen.

Häufige Vorprüfungsfehler sind `PDF_SIZE_LIMIT_EXCEEDED`,
`PDF_PAGE_LIMIT_EXCEEDED`, `PDF_UNREADABLE`, `PDF_ENCRYPTED_FORBIDDEN`,
`PDF_ACTIVE_CONTENT_FORBIDDEN`, `PDF_EMBEDDED_FILES_FORBIDDEN`,
`OCR_UNAVAILABLE`, `OCR_TIMEOUT`, `OCR_FAILED`,
`OCR_RENDERED_PIXELS_LIMIT_EXCEEDED`, `OCR_IMPORT_PAGE_LIMIT_EXCEEDED` und
`OCR_IMPORT_PIXEL_LIMIT_EXCEEDED`.

## Provider-Failures, Wiederherstellung und sichere Telemetrie

Inhaltliche Outcomes, Transportfehler und Usage-Metering sind getrennte
Klassen; sie dürfen nicht als ein „vier Outcomes“-Schema zusammengezogen
werden:

| Klasse | Behandlung |
| --- | --- |
| `PROVIDER_REFUSAL` | keine Schema-Reparatur und kein Datenretransmit; bestehende Workerpolitik |
| `PROVIDER_INCOMPLETE` | eigener, begrenzter Worker-Retry; keine Schema-Reparatur |
| `PROVIDER_SCHEMA_FAILURE` | genau ein schema-only repair mit demselben begrenzten Input; danach endgültiger Fehler |
| `PROVIDER_TIMEOUT`, `PROVIDER_CONNECTION_ERROR`, `PROVIDER_RATE_LIMIT`, `PROVIDER_SERVER_ERROR`, `OPENAI_REQUEST_FAILED` | Transportgruppe; Retrybarkeit folgt dem einzelnen stabilen Code und der Workerpolitik |
| `PROVIDER_USAGE_INVALID` (im OpenAI-Adapter konkret `OPENAI_USAGE_INVALID`) | separates Usage-Metering; Kosten-/Usage-Contract fail closed, keine Veröffentlichung |

Der Worker beansprucht Aufträge tenantweise mit Lease, Token und Generation.
Heartbeats verlängern nur die aktuelle Generation. Bei Unterbruch oder
retryfähigem Fehler geht der Auftrag in `RETRY_WAIT`; verspätete Ergebnisse
können nicht publizieren. `POST /api/declaration-imports/{id}/retry` ist nur
für retryfähige Fehler vorgesehen; `cancel` sperrt den aktuellen Lease. Vor
einem Retry Kosten-/Tokenzähler, Fehlerklasse und Limits prüfen, nicht blind
wiederholen.

## Dienste, Wiederherstellung und Status

Für lokale Entwicklung laufen API und Import-Worker getrennt:

```bash
cd backend
HV_SESSION_COOKIE_SECURE=false ./.venv/bin/uvicorn app.main:app --reload
```

```bash
cd backend
./.venv/bin/python -m app.workers.declaration_import
```

Im Betrieb werden beide Dienste mit einem geschützten externen Env-File
gestartet:

```bash
docker compose --env-file /absoluter/pfad/hausverwaltung.env \
  -f docker-compose.prod.yml up -d app worker
```

Vor einem Restore Datenbank und `documents`-Volume als konsistentes Paar
sichern und auch nur aus derselben Sicherungsgeneration wiederherstellen. Nach
dem Restore PostgreSQL-Integrität und gespeicherte SHA-256-Werte prüfen, dann
API und Worker starten. Ein einseitiger Restore kann Dokumentreferenzen und
Speicherinhalt auseinanderziehen und ist kein gültiger Wiederherstellungspfad.
Statusdiagnosen verwenden ausschließlich Metadaten wie `status`,
`error_code`, `current_stage`, Tokens, geschätzte Kosten und Attempts —
nie Dokumenttext oder Provider-Payloads.

Das dauerhafte Pass-Ledger hat **eine Zeile pro SDK-Attempt**. Es referenziert
`declaration_extraction_run_id`; der Repository-Aufruf heißt
`append_pass_entries`. Die Pipeline leert ihre Queue erst nach einem
committeten Checkpoint, Fail- oder Publish-Übergang. Die Tabelle ist
tenant-isoliert per RLS, append-only und die Datenbank validiert exakt 23
sichere JSON-Felder, Typen, Allowlists und die Bindung der Attempt-Nummer.
Python- und DB-Contract müssen deshalb gemeinsam geändert werden.

Zulässig sind nur technische Metadaten wie Pass-ID, Attempt-Nummer, Modell,
Reasoning-Effort, Prompt-/Schema-/Harness-Version und -Hash, Tokenpartition,
geschätzte Kosten, Latenz, Retry-Zähler, `route_action`, Reason-Codes und
Failure-Class. Unzulässig sind Prompttext, OCR-/PDF-Text, Quote, Bild,
Antwortkörper, lokaler Pfad, Tenant-/Property-/User-ID, Header, API-Key und
Exception-Text. Status-Endpunkte bleiben bei ihren aggregierten Metadaten.

## Caching, Kosten und Provider-Daten

Luna setzt den statischen Developer-Block vor dynamische Dokumentdaten und
verwendet einen expliziten Cache-Breakpoint sowie einen 30-Minuten-Cache. Der
stabile Schlüssel enthält nur Modell, Pass sowie Prompt-, Schema- und
Harness-Version/-Hash. Mini erhält **kein** explizites Caching. Die
Konfiguration für Luna-Cache-Writes ist
`declaration_primary_cache_write_multiplier` beziehungsweise
`HV_DECLARATION_PRIMARY_CACHE_WRITE_MULTIPLIER` und ist auf `1.25` validiert.

Kosten werden konservativ aus gewöhnlichen Input-, Cached-Input-,
Cache-Write- und Output-Tokens berechnet. Ungültige Partitionen, negative
Zähler, fehlende Raten oder Cache-Writes für Mini führen fail closed zu
`PROVIDER_USAGE_INVALID`; eine günstigere Schätzung rechtfertigt keinen
Qualitätsverlust.

`HV_OPENAI_API_KEY` ist ein ausschließlich serverseitiges Deployment-Secret:
nie Frontend, Git, Projekt-`.env`, Browser oder Ticket. Nur nach lokaler
Vorprüfung werden begrenzte zulässige Seiteninhalte an den Provider gesendet.
`store=false` wird gesetzt, **ist nicht ZDR** und löst keine Retention-Zusage
aus. API-Daten werden standardmäßig nicht zum Training verwendet; ohne
genehmigte weitergehende Kontrollen kann Abuse Monitoring Inhalte bis zu 30
Tage halten. Vor Produktion müssen AVV/DPA, Region/Data Residency,
Retention-/ZDR-Eignung, Berechtigungen und DSGVO-Bewertung geprüft und
dokumentiert sein.

## Review und fachliche Grenzen

Ein erfolgreicher Lauf endet in `NEEDS_REVIEW`, niemals in einer Anwendung.
Prüfende vergleichen jeden Fakt und jede Regel mit den Seitenbelegen, bearbeiten
oder verwerfen den Entwurf und übernehmen erst danach die Verantwortung.
Konflikte bleiben `NEEDS_CLARIFICATION`; das System erteilt keine
Rechtsauskunft. MEA sind als Zähler/Nenner zu behandeln; eine Skala wird nie
stillschweigend ergänzt oder umgerechnet.

## Netzwerkgrenze des Workers

In Produktion bleibt `backend` ein internes Docker-Netz: Datenbank,
Migration und Bootstrap sind ausschließlich dort, die API nur zusätzlich im
gemeinsamen Traefik-Netz. Ausschließlich der Declaration-Worker hängt außerdem
am projektlokalen nicht-internen Netz `egress`; er hängt nie an Traefik und
kein Dienst veröffentlicht Host-Ports. Seine expliziten Docker-Gateway-
Prioritäten sind `backend: 0` und `egress: 1`: Der Default-Route-Gateway liegt
damit im Egress-Netz, die Datenbank bleibt ausschließlich über `backend`
erreichbar.

Der OpenAI-Client verwendet fest `https://api.openai.com/v1`. Der Harness hat
keine URL-, Browser- oder Tool-Fähigkeit und übermittelt nur die vorgesehenen
Structured-Output-Anfragen. Bei aktivierten Imports prüft der Worker vor einer
Promotion die beiden konfigurierten Modell-IDs über die nicht-billable Models
API (kurzer Timeout, höchstens ein SDK-Retry). Dafür startet der Deploy nach
dem API-Healthcheck, aber vor dem konsumierenden Worker, einen einmaligen
`compose run --rm --no-deps`-Container mit explizitem Python-EntryPoint. Der
normale Worker-EntryPoint und seine Endlosschleife laufen darin nicht. Weder
PDF-/Prompt-Inhalte noch Responses-Aufrufe werden übertragen und der Schlüssel
wird ausschließlich aus seiner Container-Umgebung gelesen. Ein Fehler erhält
den vorherigen Runtime-Stand und verhindert die Promotion.

Das nicht-interne Docker-Netz ist keine Domain-Firewall. Wer Egress auf OpenAI
beschränken muss, benötigt zusätzlich eine Host-/Netzwerk-Allowlist. Der
isolierte Compose-Verifier prüft zusätzlich die laufende Default-Route gegen
das Docker-Egress-Gateway, löst mit seinem Platzhalter-Schlüssel aber keinen
externen Provider-Aufruf aus.

## Lokale Tests und Gold-Evals

Normale Tests nutzen nur synthetische/redigierte Daten und keine Netzwerkaufrufe.
Die flüchtige Testdatenbank läuft nur auf Port 55432:

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

Promotion ist fail closed. Das Goldset benötigt mindestens die neutralen IDs
`gold-units-mea`, `gold-heating-amendment` und
`gold-adversarial-evidence` sowie die festen Vergleichslabels `luna_medium`,
`luna_low`, `terra_primary` und `luna_with_mini_router`. Qualität darf in
keiner repräsentativen Dimension regressieren; niedrigere Kosten kompensieren
das nicht. Ungelabelte reale PDFs liefern höchstens einen beschreibenden
Smoke-Report, **keine Accuracy-Aussage und keine Promotion**.

Ein echter PDF-Smoke ist nie automatisch auszuführen und erzeugt nur einen
`descriptive_only`-Report: Er ist nicht promotionfähig. Er erfordert eine
separate explizite Autorisierung und vergleicht exakt `luna_medium`,
`luna_low`, `terra_primary` und `luna_with_mini_router`.

**Phase A — Aktivierung und Werte:** Vor **jeder Path-Methode** werden exakt
`HV_DECLARATION_LIVE_EVAL=1`, ein serverseitiger API-Key,
`--provider live --live --compare-models`, ein nichtleerer Wert für
`HV_DECLARATION_REAL_PDF_MANIFEST` und die drei positiven, vom Aufrufer
verifizierten Terra-Raten geprüft. Fehlt oder ist einer dieser Werte ungültig,
findet kein Path-/Manifest-/PDF-/Providerzugriff und keine Übertragung statt.

**Phase B — lokale externe Eingabeprüfung:** Erst danach werden Report- und
Manifestpfad lokal geprüft: beide müssen extern liegen, das übergebene Manifest
muss exakt `HV_DECLARATION_REAL_PDF_MANIFEST` entsprechen, Case-Limits
einhalten und pro Case die SHA-256-Prüfung bestehen. In dieser Phase werden
Manifest und deklarierte PDFs lokal gelesen und gehasht. Ein abweichender Hash
verhindert Capability, Adapter, Provider und Übertragung — er behauptet nicht,
dass die Datei nie geöffnet wurde. Erst nach erfolgreicher Phase B wird die
Capability erzeugt und ein Adapter konstruiert. Manifest, PDFs und Bericht
gehören nie ins Repository.

```bash
cd backend
./.venv/bin/python -m app.evals.declaration_knowledge \
  --fixtures tests/fixtures/declaration_eval_cases.json \
  --provider live --live --compare-models \
  --manifest /absoluter/externer/pfad/manifest.json \
  --report-dir /absoluter/externer/pfad/berichte
```

Die drei Phase-A-Raten heißen
`HV_DECLARATION_COMPARE_TERRA_INPUT_USD_PER_MILLION`,
`HV_DECLARATION_COMPARE_TERRA_OUTPUT_USD_PER_MILLION` und
`HV_DECLARATION_COMPARE_TERRA_CACHED_INPUT_USD_PER_MILLION`. Das externe
Die Präsenz von `HV_DECLARATION_REAL_PDF_MANIFEST` ist ein Phase-A-Gate; erst
in Phase B wird sein Wert als absoluter externer Pfad auf Gleichheit mit dem
übergebenen Manifest geprüft und anschließend lokal validiert.

## Forschungseinordnung

Die Struktur folgt [Harness Engineering](https://openai.com/index/harness-engineering/):
Repo-Legibilität, erzwingbare Grenzen, Tests und Evals — nicht Autonomie.
Die Modell-, Vision-, Structured-Output-, Cache- und Datenhinweise sind an den
[Latest model guide](https://developers.openai.com/api/docs/guides/latest-model),
[GPT-5.4 Mini](https://developers.openai.com/api/docs/models/gpt-5.4-mini),
[Vision](https://developers.openai.com/api/docs/guides/images-vision),
[Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs),
[Prompt Caching](https://developers.openai.com/api/docs/guides/prompt-caching)
und [Your data](https://developers.openai.com/api/docs/guides/your-data)
ausgerichtet. Datenschutz- und Betriebsannahmen müssen zusätzlich gegen die
[Business-data-Hinweise](https://openai.com/business-data/) des gewählten
Vertrags geprüft werden. [Evals](https://openai.com/index/evals-drive-next-chapter-of-ai/)
begründen die fail-closed Promotion statt einer Kostenentscheidung nach Gefühl.
Die Routing-Literatur ([RouteLLM](https://proceedings.iclr.cc/paper_files/paper/2025/file/5503a7c69d48a2f86fc00b3dc09de686-Paper-Conference.pdf),
[RouterEval](https://aclanthology.org/2025.findings-emnlp.208.pdf)) motiviert
Vergleich und Messung; ohne repräsentative gelabelte Daten bleibt das
Produktionsrouting bewusst deterministisch. Die Dokumentgrenze folgt außerdem
den Empfehlungen zu [Prompt-Injection-Resistenz](https://openai.com/index/designing-agents-to-resist-prompt-injection/).

## Betrieb sauber beenden

Lokal gestartete API- und Worker-Prozesse werden im jeweiligen Vordergrund mit
`Ctrl-C` beendet. Die isolierte Testdatenbank nur mit ihrem exakten
Projektnamen stoppen:

```bash
docker compose -p hv-declaration-tests -f infra/docker-compose.declaration-test.yml down
```

Echte Nutzer-PDFs, Manifestdaten, API-Schlüssel und Provider-Payloads bleiben
außerhalb von Git, Test-Fixtures, Reports, Logs und Tickets.

## Explicit queue principals (migration 0043)

New start/retry commands require a UUID `Idempotency-Key`. Keep the key when a
response is lost. An identical replay returns its `command_receipt_id` alongside
the current `queue_request_id` and import state; it never queues work again or
replaces a later principal. A fresh command against active work conflicts. The
UI retains its command key across request failures. Queue history is immutable
and tenant-safe; `initiated_by` remains the original draft creator.

Workers resolve the triggering actor from the current receipt, then check active
writer membership under the shared ACL lock before claiming, heartbeat, source
reads, target-data reads, budget reservation/checkpoint and publication. Lease
recovery retains that receipt. Unknown old queue principals are not guessed from
the creator: they are cancelled as `JOB_PRINCIPAL_UNVERIFIED`. A revoked,
deactivated or non-writer actor causes `JOB_PERMISSION_CHANGED`. A currently
authorized writer can explicitly retry these permission cancellations with a new
key; ordinary user cancellation remains terminal. Run budgets still apply.

Cancellation commits separately from the failed effect transaction, fences stale
workers, closes running extraction records, and preserves source originals and
persisted budget reservations. Both pipeline- and heartbeat-origin cancellation
are reported as permission changes. Already-dispatched provider work cannot be
recalled; its output cannot publish after revocation. No database transaction is
held across PDF parsing, OCR or provider I/O. Populated downgrade refuses to erase
queue history; repair forward. Worker readiness requires the new queue column.

This is an organization-level live-principal prerequisite. It does not grant a
property-source or accounting-scope entitlement, automatically classify old
sources, activate live providers or satisfy the full B1 rollout gate. Browser
acceptance uses synthetic local originals and the production cancellation helper
against the guarded disposable database; it does not run a live provider.

Interactive declaration endpoints also revalidate the current writer role inside
the transaction that handles source access and review/approval. A role downgrade
between initial authentication and this check returns 403; deactivation or missing
membership returns 401. A role change concurrent with an already-authorized short
request waits for that request's shared ACL lock. Subsequent requests use the
changed permissions. Approved property-document metadata/PDF routes require a
currently active member; their unclassified read-only compatibility is retained.
This check does not confer accounting-scope access. The optional knowledge-provider
workflow uses the separate per-phase principal checks described below.

## Knowledge-query principal checks (migration 0044)

Approved knowledge overview, rules and lexical search now use the live-member
request guard. Optional Q&A persists the initiating actor on its content-free cost
ledger and checks active membership separately before source retrieval, reservation
and final answer validation. Read-only members retain current approved-knowledge
compatibility. No database transaction spans provider network I/O.

Deactivation or membership removal during provider execution suppresses the answer
with HTTP 401. A separate cancellation transaction records `QA_PERMISSION_CHANGED`
and conservatively retains the full reserved amount as estimated cost, with a safe
audit event. Already-dispatched provider input cannot be recalled. Historical null
actors stay unknown; an active member can expire a timed-out reservation without
adopting its identity. Downgrade refuses to erase recorded principals; repair
forward. This is an organization-level prerequisite, not accounting-scope source
classification or authorization to enable a live provider.

## Scheduling metadata before source access (migration 0046)

Workers require `next_declaration_queue_metadata` and
`lock_declaration_lease_metadata` at startup. These tenant-fenced helpers expose
only bounded scheduling/fence metadata and perform ACL-before-row locking. The
worker resolves and sets its persisted queue principal before loading the import
through ordinary RLS. A hidden revoked job can still be safely cancelled through
the existing exact-fence cancellation helper; absence from a source query is not
used as evidence that no job exists. Lease expiry is checked with the database
clock after lock waits. Provider I/O remains outside database transactions.

No production source policy is enabled by this migration. Property-data-domain
checks must join queue-principal resolution before that later activation. Do not
remove the source-query policy to make an actorless worker find jobs. Downgrade
removes only the two scheduling helpers and preserves all queue and source data;
updated workers will report schema-not-ready until the helpers are restored.

## Database worker commands (0070)

Declaration workers require their own `app_worker` connection through
`HV_DATABASE_URL_WORKER`. There is no fallback to the web URL. Provision the login
before migration; fresh development/production bootstrap scripts include it. For
an existing production volume use the separately authorized procedure in DEPLOY.md.
Never reset a populated volume to rerun init scripts. The web container must not
receive the worker credential.

`app_worker` inherits only `app_rw` and has no member roles. Neither runtime role
may have superuser, BYPASSRLS, CREATEROLE, CREATEDB or REPLICATION. Worker sessions
validate the role and membership graph; losing inheritance also fails validation.
Claim/renew/transition are worker-only database commands. Manual cancellation
remains a current-user prepare operation. Lease duration is 30–3600 seconds and
database time owns expiry/scheduling. An old token cannot renew or complete work.

Strict processing state and maximum budget cannot be changed with raw runtime SQL.
Reclaim closes abandoned attempts and installs a new fence. Reservation/accounting
corrections remain part of the trusted pipeline; recorded overrun evidence must not
be dropped merely because it exceeds a configured prospective budget.

The existing NEEDS_REVIEW revision increment is still a compatibility exception,
not proof that candidate review and audit happened atomically. Worker child-row
and review-effect command closure remain required before source activation. Do not
infer B1 completion from the accepted lifecycle-command increment.

## Worker output receipts (0071)

Workers require the effect-access helper and private context table at startup.
Each output transaction obtains its current lease through
`lock_declaration_lease_metadata`; only the actual app_worker login receives a
private receipt keyed to that database connection and transaction. Ordinary runtime
SQL cannot read or write the receipt table. A new transaction must validate the
lease again; setting a token GUC is insufficient. Expired receipts are removed in
bounded, nonwaiting batches. The table contains no source text or approval history.

Strict output triggers guard runs, passes, candidates, evidence, issues and page
counts. Manual cancellation/reclaim retains trusted cleanup. Downgrade refuses
ENFORCED/PAUSED organizations and takes NOWAIT dependency locks. Source activation
remains disabled pending atomic human-review effects and other B1 coverage.
