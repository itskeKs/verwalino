# Teilungserklärungs-Import: Agentic Modernization Design

Stand: 2026-08-29 · Status: **Variante 2 implementiert und technisch vollständig verifiziert; fachliche/Privacy-Produktivfreigabe und autorisierter Live-Eval bleiben offen**

## Entscheidung in einem Satz

Der Import bleibt ein begrenzter, deterministisch orchestrierter Manager-Workflow über die OpenAI Responses API: `gpt-5.6-luna` ist das Primärmodell, `gpt-5.4-mini` führt ausschließlich anhand lokaler, messbarer Signale begrenzte Vision-Nachprüfungen aus, und eine berechtigte Person entscheidet weiterhin über jede Übernahme.

**Verifizierte Ausführungspolitik (2026-08-29):** Luna läuft im Produkt explizit mit `reasoning.effort="medium"`, dem dokumentierten aktuellen Default. Mini läuft im begrenzten Vision-Pass explizit mit `reasoning.effort="none"`, seinem dokumentierten Default. Terra und Luna mit `reasoning.effort="low"` sind ausschließlich Eval-Kandidaten. Eine Promotion vergleicht Luna `medium` und `low` auf demselben Goldset; sie verändert keine Produktkonfiguration automatisch. Unabhängige Pässe verwenden weder `previous_response_id` noch persistierte Reasoning-Zustände ([Latest model guide](https://developers.openai.com/api/docs/guides/latest-model)).

## Ausgangslage, geschlossene Lücken und Restgrenzen

Die bestehende Strecke besitzt bereits wichtige Grenzen: lokale PDF-/OCR-Vorprüfung, feste Extraktionspässe, Structured Outputs, `store=false`, Token-/Kostenlimits, stabile Providerfehler und `NEEDS_REVIEW` vor jeder kanonischen Änderung. `docs/operations/declaration-imports.md` beschreibt diese Betriebsgrenzen.

Die folgenden Lücken wurden im begrenzten Workflow geschlossen. Die ersten
beiden Spalten beschreiben die Ausgangslage vor der Modernisierung; die dritte
Spalte ist der implementierte Contract. Die Tabelle ist keine Behauptung einer
fachlichen Genauigkeit auf ungelabelten echten PDFs:

| Ausgangslage vor Modernisierung | damalige Folge | implementierter Zielzustand |
| --- | --- | --- |
| `DeclarationPipeline._preflight_documents` baut `PageInput` ohne `image_data_url`. | Die regulären Extraktionspässe und der Recheck erhalten trotz lokaler Renderbarkeit kein Bild. | Nur deterministisch ausgewählte Seiten erhalten eine flüchtige lokale Bilddarstellung für einen Vision-Recheck. |
| `OpenAiDeclarationProvider._page_content_blocks` fügt Bilder nur ein, wenn ein solches Feld vorhanden ist. | Vision ist faktisch nur in künstlich vorbereiteten Aufrufen möglich. | Bilder werden nur nach Routerentscheidung, nie pauschal, an den Eskalationspass gegeben. |
| `validate_reconciliation` prüft Dokument- und Seitengrenzen, nicht den Zitatinhalt. | Ein erfundenes oder auf der Seite nicht vorkommendes Zitat kann weitergereicht werden. | Jede Evidence-Quote wird deterministisch gegen den lokalen, seitengebundenen Text geprüft; ein Nichttreffer ist ein Blocker. |
| Der Provider behandelt `incomplete` im Schema-Reparaturpfad. | Abbruch, Ablehnung und tatsächlich reparierbares Schemaformat sind nicht getrennt mess- oder steuerbar. | Refusal, incomplete, timeout und schema failure haben je eigene Fehlerklasse, Retry- und Reviewwirkung. |
| Run und Eval melden derzeit primär Aggregate. | Pass-, Modell-, Prompt- und Fehlersignale sind nicht auditierbar. | Ein sanitisiertes Per-Pass-Ledger enthält nur technische Metadaten. |
| Der Live-Harness bewertet operative Metriken, nicht Gold-Genauigkeit. | Ein Live-Lauf kann schnell und günstig sein, ohne die Fachlichkeit nachzuweisen. | Externe, repräsentative Gold-Evals bewerten fachliche Genauigkeit und Promotion-Gates. |

## Ziele

1. Bewahre den bestehenden API-, Frontend-, Traefik- und Human-Approval-Vertrag unverändert.
2. Behandle hochgeladenen PDF-Text und gerenderte Seiten strikt als untrusted data; sie erhalten weder Tools noch Ausführungsrechte.
3. Prüfe angegebene Belegzitate lokal und deterministisch auf ihrer bezeichneten Seite, bevor Kandidaten entstehen.
4. Route Eskalation allein aus reproduzierbaren Signalen, nicht aus Modell-Confidence oder Modell-Selbstbewertung.
5. Mache jeden Providerpass mit datensparsamem Ledger, Schemaprompt-Fingerprints und stabilen Failure Classes nachvollziehbar.
6. Vergleiche Luna und Terra sowie die Vision-Eskalation mit versionierten Gold-Evals; ein Live-Lauf bleibt explizit gesperrt und extern manifestiert.

## Nicht-Ziele

- Kein Agents SDK, keine autonom kooperierenden Agents, keine Handoffs und kein Tool-Aufruf durch ein Modell.
- Keine automatische Freigabe, Rechtsauskunft, kanonische Datenänderung oder Absenkung der bestehenden Reviewpflicht.
- Keine Änderung öffentlicher API-Antworten, des Frontends, der Traefik-Router oder des Uploadvertrags.
- Keine Speicherung von Prompttext, Dokumentseiten, OCR-Text, Bildern, Zitaten, Modellantworten oder API-Schlüsseln im Ledger, Report, Log oder Ticket.
- Kein produktiver oder Live-OpenAI-Aufruf ohne einen separaten, ausdrücklich autorisierten Auftrag sowie ein externes, nicht versioniertes Manifest.
- Kein lernender Online-Router und keine Ausweitung des Modellpools über Luna, Terra und die bereits konfigurierte Mini-Eskalation.

## Architektur und Datenfluss

```text
PDF -> lokale Vorprüfung/OCR -> PageInput(text, quality, ephemeral image factory)
                                |
                                v
          Luna: inventory -> core + rules -> reconciliation (Structured Outputs)
                                |
             deterministic evidence-text validator + domain validation
                                |
              router decision table (local signals only)
                   | NONE / HUMAN_REVIEW
                   | VISION_RECHECK
                   v
    local rendering only for selected page keys -> gpt-5.4-mini recheck
                                |
                  merge -> validate again -> candidate drafts -> NEEDS_REVIEW
                                                               |
                                                         human approval
```

`DeclarationPipeline` bleibt Manager und besitzt Reihenfolge, Limits, Reservations, Retrygrenzen und Veröffentlichung. Provider bleiben austauschbare Ausführungsadapter; sie dürfen keine Routerentscheidung treffen. Die neue Bilddarstellung lebt nur im Arbeitsspeicher beziehungsweise in einer bereits begrenzten lokalen temporären OCR-Operation, wird nach Abschluss verworfen und nie auf dem Modell- oder Import-ORM gespeichert.

### Feste Passfolge und Abbruchkriterien

| Pass | Modell | Eingabe | Erfolg | Stop/Weitergabe |
| --- | --- | --- | --- | --- |
| `inventory` | Luna, `medium` | textuelle Batches | valides `DocumentInventoryOutput` | feste Schemafehler beenden den Lauf; fachliche Unklarheit wird als Output weitergereicht. |
| `extract_core` | Luna, `medium` | derselbe Textbatch | valides `CoreExtractionOutput` | gleiches Budget und dieselben Data-Boundaries. |
| `extract_rules` | Luna, `medium` | derselbe Textbatch | valides `RuleExtractionOutput` | gleiches Budget und dieselben Data-Boundaries. |
| `reconcile` | Luna, `medium` | nur strukturierte Passoutputs | valides `ReconciliationOutput` | kein Bild, keine Tools, keine neue Dokumentübertragung. |
| `evidence_validate` | lokal | Quote plus exakt bezeichnete Seiten-Textschicht | jede Quote kommt als normalisierter Substring auf dieser Seite vor | Nichttreffer erzeugt `EVIDENCE_QUOTE_NOT_FOUND`-Blocker. |
| `vision_recheck` | gpt-5.4-mini, `none` | selektierte Seite als Bild plus lokaler Text und Issue-Codes | valides `TargetedRecheckOutput` | höchstens bestehende Recheck-Seiten/-Aufrufe/-Budgets; danach erneute vollständige Validierung. |
| `candidate_publish` | kein Modell | validierte Daten | atomare Draft-Veröffentlichung | endet immer in `NEEDS_REVIEW`, nie in automatischer Anwendung. |

Eine echte Ablehnung beendet den betreffenden Modellpass ohne Datenretransmit. Ein abgebrochener (`incomplete`) Output ist ebenfalls keine Schema-Reparatur: Er erhält eine eigene Failure Class und folgt nur dem explizit konfigurierten Provider-/Worker-Retry. Ausschließlich ein feststellbarer Structured-Output-Parse- oder Schemafehler erhält exakt einen schema-only repair mit demselben begrenzten Input und Rest-Outputbudget. Ein Timeout wird als transport-/ausführungsbezogener, retryfähiger Fehler behandelt. Nach dem gesetzten Maximum bleibt ein sicherer, stabiler Status stehen.

## Deterministische Evidenz- und Sicherheitsgrenze

`EvidenceTextIndex` wird aus den lokalen `PageInput.text`-Werten gebildet und ist nur für die Dauer eines Pipeline-Laufs verfügbar. Für jedes `EvidencePayload` gilt:

1. Prüfe zunächst `document_key` und `pdf_page` gegen die vorgeprüften Seiten.
2. Normalisiere sowohl Quote als auch Seitentext identisch: Unicode NFKC, Zeilenende zu Leerzeichen, Unicode-Whitespace kollabieren, typografische Bindestriche vereinheitlichen und `casefold()`.
3. Akzeptiere die Quote nur, wenn der normalisierte Quote-String nicht leer ist und als zusammenhängender Substring in genau dieser Seite vorkommt.
4. Bei `printed_page`, `section` oder `field_path` findet keine heuristische Freigabe statt; sie bleiben beschreibende Metadaten, nicht der Beweis.
5. Bei Fehlschlag wird kein Text in Fehlernachricht oder Ledger geschrieben. Der Kandidat erhält `EVIDENCE_QUOTE_NOT_FOUND` mit Subject-Key und Seitenreferenz; die Routertabelle kann diese Seite begrenzt zur Vision-Nachprüfung auswählen.

Das ist eine Provenance-Grenze: Dokumentinhalt bleibt passives, klar markiertes Nutzdatenmaterial. Prompts behalten den vorhandenen untrusted-data-Hinweis und werden als Versionsschema gehasht. Das folgt dem Prinzip, Herkunftssignale an Daten beizubehalten, statt Instruktionen aus Dokumenten vertrauenswürdig erscheinen zu lassen ([Microsoft Research: Spotlighting](https://www.microsoft.com/en-us/research/publication/defending-against-indirect-prompt-injection-attacks-with-spotlighting/)).

## Router-Contract

Der Router ist eine reine, seitenechte Python-Funktion. Er erhält keine Dokumenttexte, keine Modellantwort, keine Modell-Confidence als Signal und führt keine Nebenwirkung aus.

```python
RouterDecision(
    action: Literal["NONE", "VISION_RECHECK", "HUMAN_REVIEW"],
    reason_codes: tuple[str, ...],
    page_keys: tuple[tuple[str, int], ...],
    model: Literal["gpt-5.4-mini"] | None,
)
```

Die Tabelle ist vollständig; Treffer werden nach der angegebenen Priorität ausgewertet. `page_keys` werden lexikographisch sortiert, dedupliziert und hart auf `declaration_max_recheck_pages` begrenzt.

| Priorität | Messbare Eingabe | Entscheidung | Seiten | Begründung |
| ---: | --- | --- | --- | --- |
| 1 | isolierter Router-Contract: `PROVIDER_REFUSAL`, `PROVIDER_INCOMPLETE`, `PROVIDER_TIMEOUT` oder `PROVIDER_SCHEMA_FAILURE` | `HUMAN_REVIEW` | keine | Ein nicht vorliegender strukturierter Output ist kein Anlass, Inhalt an ein anderes Modell zu senden. |
| 2 | Evidence verweist auf unbekanntes Dokument oder Seite außerhalb der vorgeprüften Grenzen | `HUMAN_REVIEW` | keine | Lokale Provenance ist verletzt und kann nicht durch Vision repariert werden. |
| 3 | `EVIDENCE_QUOTE_NOT_FOUND` auf einer existierenden Seite **und** die Seite ist lokal renderbar | `VISION_RECHECK` | genau die Evidenzseiten | Vision darf nur lokalen Text-/Layoutkonflikt auflösen. |
| 4 | Seite hat `quality < 0.85` und ist entweder zitiert oder erzeugt einen Blocker | `VISION_RECHECK` | diese Seite | Die Qualitätszahl stammt aus lokaler Vorprüfung, nicht aus Modellvertrauen. |
| 5 | ein fachlicher Blocker aus MEA, Einheit, Regelbeziehung oder Formel ohne Signal aus 3/4 | `HUMAN_REVIEW` | keine | Eine Rechen- oder Rechtsregel wird nicht durch einen weiteren Modellaufruf "repariert". |
| 6 | kein Signal aus 1–5 | `NONE` | keine | Pipeline publiziert den Entwurf mit allen verbleibenden Warnungen für Review. |

Im produktiven Pipelinepfad wird der Router derzeit mit `provider_failure=None`
aufgerufen. Providerfehler folgen somit direkt der bestehenden Workerpolitik
nach `RETRY_WAIT` oder `FAILED`, niemals einem Vision-Recheck. Die erste
Zeile der Tabelle beschreibt nur den isolierten Router-Contract; für seine vier
zulässigen Providerfehler ist `HUMAN_REVIEW` die einzige mögliche Route.

Der Router dient der messbaren Kosten-/Qualitätssteuerung, nicht einem Vorhersageversprechen. Seine späteren Grenzwerte werden ausschließlich aus Gold-Evals und geschlossenen Promotion-Gates geändert. Das entspricht der Daten- und Präferenzorientierung von Modellrouting, nicht modellseitiger Selbsteinschätzung ([RouteLLM proceedings](https://proceedings.iclr.cc/paper_files/paper/2025/file/5503a7c69d48a2f86fc00b3dc09de686-Paper-Conference.pdf), [RouterEval](https://aclanthology.org/2025.findings-emnlp.208.pdf)).

## Provider- und Ledger-Contract

`ProviderCallResult` und `ProviderFailure` werden um einen strikt sanitisierbaren `ProviderPassMetadata`-Wert ergänzt. Es gibt genau einen Ledgereintrag je SDK-Attempt; lokale Vorab-Abweisungen versprechen keinen Ledgereintrag. Zulässige Felder sind:

| Feld | Regel |
| --- | --- |
| `pass_id`, `attempt_no`, `stage` | feste ASCII-Enums; keine frei eingegebenen Namen. |
| `configured_model`, `returned_model_id`, `reasoning_effort` | Modellkonfiguration, optionale vom Response gelesene Modell-ID und allowlist-validierter Ausführungsmodus; keine Antwortdaten. |
| `request_id` | optionale API-Request-ID; Regex-begrenzt und nur wenn SDK/Response sie liefert. |
| `prompt_version`, `prompt_sha256` | feste Promptversion und SHA-256 ausschließlich über den statischen Developer-Prompt. |
| `schema_version`, `schema_sha256` | feste Schemaversion und SHA-256 ausschließlich über das Structured-Output-JSON-Schema. |
| `harness_version`, `harness_sha256` | feste Harnessversion und SHA-256 über den statischen Request-Harness ohne Nutzdaten. |
| `input_tokens`, `cached_input_tokens`, `cache_write_input_tokens`, `output_tokens`, `estimated_cost_usd`, `latency_ms`, `retry_count` | nichtnegative technische Zähler; Cost bleibt `Decimal`. |
| `route_action`, `route_reason_codes`, `failure_class` | feste, allowlist-validierte Enums/Codes ohne Exception-Text. |

Nicht zulässig sind Prompttext, Request-/Response-Bodies, Quote, OCR-Text, Bilddaten, PDF-Pfad, Dokumentname, Benutzer-, Tenant- oder Property-ID, Header, API-Key und rohe Exception-Nachrichten. Die untergeordnete, tenant- und run-gebundene Tabelle `declaration_import_pass` speichert genau ein `safe_metadata`-JSONB je SDK-Attempt, gebunden an `declaration_extraction_run_id`. `append_pass_entries` leert die Pipeline-Queue erst nach einem committeten Checkpoint, Fail- oder Publish-Übergang. Statusendpunkte liefern weiterhin nur die bisher erlaubten Aggregate. Alle Protokollierungsstellen serialisieren ausschließlich `as_safe_dict()`; der DB-Validator erzwingt exakt 23 sichere Felder, Typen, Allowlists und die Attempt-Bindung.

Responses API und Structured Outputs bleiben der Adaptermechanismus; die vorhandene SDK-Parse-Nutzung wird nicht durch freien JSON-Text ersetzt. Die aktuelle API-Dokumentation dokumentiert Structured Outputs als schemaorientierten Output-Contract ([OpenAI Structured Outputs guide](https://developers.openai.com/api/docs/guides/structured-outputs)).

### GPT-5.6 Prompt-Cache- und Kostenpolicy

Nur Luna erhält eine explizite Cache-Policy. Der Request besitzt nach dem statischen Developer-Prompt einen expliziten Cache-Breakpoint mit 30-Minuten-TTL; dynamischer Dokument-, OCR-, Bild- und Reconciliation-Input folgt danach und wird nie absichtlich als Cache-Suffix geschrieben. `prompt_cache_key` wird stabil allein aus Modell, Pass sowie Prompt-, Schema- und Harness-Versionen/-Hashes abgeleitet. Er enthält keine Tenant-, Dokument-, Import-, Property- oder Benutzerkennung. Mini erhält keine Behauptung oder Konfiguration für explizites Caching; sein implizites Cacheverhalten wird lediglich über die vom Response gelieferten Zähler gemessen. Die validierte Luna-Cache-Write-Konfiguration heißt `declaration_primary_cache_write_multiplier` beziehungsweise `HV_DECLARATION_PRIMARY_CACHE_WRITE_MULTIPLIER` ([Prompt caching guide](https://developers.openai.com/api/docs/guides/prompt-caching)).

Für jeden Pass berechnet der Adapter Kosten fail-closed aus vollständig validierten Zählern: `ordinary_input_tokens = input_tokens - cached_input_tokens - cache_write_input_tokens`. Gewöhnliche Eingabe nutzt den normalen Modell-Inputpreis, `cached_input_tokens` den Cached-Inputpreis und Luna-`cache_write_input_tokens` den `1.25 ×` Luna-Inputpreis. Unbekannte negative Werte, nicht-ganzzahlige Zähler, fehlende Rates, `cached_input_tokens + cache_write_input_tokens > input_tokens` oder ein Cache-Write auf Mini sind ein sicherer `PROVIDER_USAGE_INVALID`-Fehler. Aktuelle konfigurierbare Raten pro Million Tokens: Luna Input/Cached/Output `0.20/0.02/1.20` USD; Mini Input/Cached/Output `0.75/0.075/4.50` USD. Terra erhält keine unbestätigte Produktionsrate und bleibt Eval-only.

## Evaluation und Promotion

Die versionierte synthetische Fixture bleibt ein schneller deterministischer CI-Schutz. Ergänzend entsteht ein externes, nur durch neutrale Case-IDs identifiziertes Gold-Manifest. Es enthält keine PDFs, Texte, Zitate oder Pfade im Repository; der lokale Manifestpfad und dessen SHA-256 bleiben weiterhin die Live-Sperre.

Jeder Goldfall hat strukturierte Sollwerte für mindestens:

- Einheiten und Zuordnungen einschließlich Nummer, Nutzungsart, Fläche und Sondereigentum;
- MEA-Zähler/Nenner, eine bewusst abweichende Skala und einen vollständigen Summenfall;
- Regelgeltung, Konflikt, Aufhebung und `NEEDS_CLARIFICATION`;
- Heizkostenregel mit Flächen-/Verbrauchsanteil und exakter Formel;
- Änderungswirkung auf eine frühere Erklärung beziehungsweise einen Nachtrag;
- Belegseiten, echte Quote-Position und eine absichtlich falsche Seiten-/Quote-Kombination;
- adversarial content wie "ignore previous instructions", gefälschte Toolaufforderungen und überredende Freigabeanweisungen als passive Dokumentdaten.

Die Auswertung vergleicht exakt die vier Labels `luna_medium`, `luna_low`, `terra_primary` und `luna_with_mini_router` unter identischem Goldset. Die erforderlichen neutralen Fall-IDs sind `gold-units-mea`, `gold-heating-amendment` und `gold-adversarial-evidence`. Terra ist ein Eval-Kandidat, kein automatisches Produktionsrouting. Ein gated Live-Eval liefert ausschließlich einen `descriptive_only`-Report und ist nicht promotionfähig.

**Phase A — Aktivierung und Werte:** Vor jeder **Path-Methode** prüft der
Harness exakt `HV_DECLARATION_LIVE_EVAL=1`, serverseitigen API-Key,
`--provider live --live --compare-models`, einen nichtleeren Wert für
`HV_DECLARATION_REAL_PDF_MANIFEST` sowie drei positive, vom Aufrufer
verifizierte Terra-Raten. Bei fehlendem oder ungültigem Wert gibt es keinen
Path-/Manifest-/PDF-/Providerzugriff und keine Übertragung.

**Phase B — lokale externe Eingabeprüfung:** Erst danach prüft der Harness den
externen Report- und Manifestpfad, Manifestgleichheit zu
`HV_DECLARATION_REAL_PDF_MANIFEST`, Case-Limits und die SHA-256-Werte. In
dieser Phase werden Manifest und deklarierte PDFs lokal gelesen und gehasht.
Ein Hash-Mismatch verhindert Capability, Adapter, Provider und Übertragung,
ohne zu behaupten, die PDF sei nie geöffnet worden. Erst nach erfolgreicher
Phase B entstehen Capability und Adapter.

Promotion von Prompt, Schema, Modell, Routergrenze oder Provideradapter ist nur erlaubt, wenn der Kandidat gleichzeitig:

1. alle deterministischen Sicherheitsgates erfüllt (`candidate_schema_validity=1`, keine ungültige Zitatannahme, keine Tenant-Leakage, exakte Brüche, vollständige Blocker-Erkennung);
2. auf dem repräsentativen Goldset keine schlechtere fachliche Metrik als die freigegebene Baseline zeigt: Units/MEA/Regeln/Heizkosten/Änderungswirkung/Belegseiten jeweils mindestens Baseline, Zitattext-Trefferquote `1.0`;
3. Luna `medium` gegen Luna `low` sowie Luna gegen Terra mit identischen Goldfällen vergleicht, eine dokumentierte Kosten- und Latenzveränderung pro Fall ausweist und das gewählte Modell die vereinbarten Qualitätsgates erfüllt;
4. beim Mini-Pfad nachweist, dass jede Eskalation durch einen aufgezeichneten Router-Code begründet ist und kein unberechtigter Bildpass stattfindet;
5. einen Review von Engineering und fachlich zuständiger Person erhält. Ein fehlendes Goldlabel, ein Qualitätsrückgang oder ein offener Privacy-Test sperrt die Promotion.

Die Entscheidung bleibt absichtlich ein einfacher, fester Workflow mit programmatischen Gates; diese Form ist für klar zerlegbare Aufgaben geeignet und vermeidet vorzeitige Agent-Komplexität ([Anthropic: Building Effective AI Agents](https://www.anthropic.com/engineering/building-effective-agents)).

## Failure Modes

| Failure class | Retry | Kandidaten/Reviewwirkung | Ledger |
| --- | --- | --- | --- |
| `PROVIDER_REFUSAL` | nein | sicher fehlschlagen, kein Datenretransmit | Pass/Modell/Request-ID falls vorhanden, keine Antwortbegründung |
| `PROVIDER_INCOMPLETE` | bestehende begrenzte Workerpolitik | `RETRY_WAIT` oder endgültig `FAILED`; nicht als Schema-Reparatur | Tokens, Latenz, Failure Class |
| `PROVIDER_SCHEMA_FAILURE` | genau ein schema-only repair | zweiter Fehler endgültig `FAILED` | beide Versuche und Schemahash |
| `PROVIDER_TIMEOUT`, `PROVIDER_CONNECTION_ERROR`, `PROVIDER_RATE_LIMIT`, `PROVIDER_SERVER_ERROR`, `OPENAI_REQUEST_FAILED` | Transportgruppe; Retrybarkeit je stabilem Code und Workerpolitik | `RETRY_WAIT` oder `FAILED`, keine Veröffentlichung | Reservation/observed usage und Failure Class |
| `PROVIDER_USAGE_INVALID` (im OpenAI-Adapter konkret `OPENAI_USAGE_INVALID`) | nein | separater Usage-/Kosten-Contract, fail closed | nur sichere Zähler und Failure Class |
| `EVIDENCE_QUOTE_NOT_FOUND` | kein Providerretry | Mini nur bei Routerregel 3; danach Blocker für Human Review | Routercode und Seitenanzahl, kein Quote |
| `EVIDENCE_PAGE_OUT_OF_RANGE`/unbekanntes Dokument | nein | Blocker, keine Vision | lokale Failure-/Issue-Codes |
| Bild nicht renderbar oder Bildbudget erschöpft | nein | bestehender Textentwurf mit Blocker/Human Review, nie Ersatzupload | lokale Route- und Failure-Class |

## Migration, Rollout und Rücknahme

1. Migriere das Ledger additiv; alte Runs bleiben ohne Passdaten lesbar und erhalten keinen künstlichen Backfill.
2. Deploye erst nach Migration, Tests sowie fachlichem und Privacy-Review. Der Quote-Validator bleibt ein Blocker bei unverändertem Review-UI; es werden nur sanitisierte Metriken beobachtet.
3. Der Codepfad für den Mini-Recheck bleibt durch die feste Routertabelle, bestehende Rechecklimits und Human Approval begrenzt. Ein autorisierter Gold-Live-Eval ist eine spätere, separate Betriebsentscheidung, keine automatische Aktivierung.
4. Vergleiche Luna, Terra und Mini nur in gated Eval-Läufen; ändere keine Laufzeitmodellkonfiguration durch Eval-Ergebnisse allein.
5. Bei Regression nicht deployen beziehungsweise die Anwendungsversion zurückrollen. Die additive Migration wird nur bewusst zurückgerollt, wenn keine zu erhaltenden Ledgerdaten davon betroffen sind; Human Approval bleibt unverändert.

## Dokumentations-Informationsarchitektur

| Dokument | Verantwortete Information |
| --- | --- |
| `README.md` | aktuelle lokale Setup-Kurzform: `uv sync --extra dev --extra ocr --extra ops`, sichere Test-/Live-Grenze und Verweis auf Runbook. |
| `docs/architecture.md` | aktueller Top-Level-Abschnitt für deterministische Extraction Manager, Trust Boundary, Human Approval und keine Agent-Tools. |
| `docs/operations/declaration-imports.md` | Betrieb: Routercodes, Failure Classes, Ledger-Privacy, Gold- und Live-Eval-Gates, Recovery. |
| `docs/roadmap.md` | erledigter/kommender Stand dieser Modernisierung und explizite Nicht-Ziele. |
| `docs/superpowers/specs/...design.md` | dauerhaftes Entscheidungsprotokoll mit Contracts und Begründung. |
| `docs/superpowers/plans/...modernization.md` | testbare Umsetzungsreihenfolge, Red/Green-Kommandos und Review-Checkpoints. |

## Abnahmekriterien

- Eine rote Evidence-Quote auf einer vorhandenen, renderbaren Seite erzeugt deterministisch `VISION_RECHECK`; ein unbekanntes Dokument oder eine ungültige Seite nie.
- Ein echter Bildblock wird nur für routerselektierte, lokal erzeugte Seiten an den Mini-Recheck übergeben; reguläre Luna-Pässe bleiben textorientiert.
- Refusal, incomplete, timeout und schema failure sind in Provider-, Pipeline-, Ledger- und Evaltests unterscheidbar; nur schema failure erhält einen Reparaturversuch.
- Das Ledger enthält die erlaubten passweisen Felder, akzeptiert keine Nutzdatenfelder und der bestehende Aggregate-Statusvertrag bleibt stabil.
- Alle repräsentativen Golddimensionen werden durch konkrete Evalfälle und Promotion-Gates gemessen.
- Das Ledger trennt Prompt-, Schema- und Harness-Version/Hash, enthält Reasoning- und Cache-Zähler, speichert keine Nutzdaten und weist ungültige Cache-Kosten fail-closed ab.
- Luna nutzt explizit `medium`; Mini nutzt explizit `none`; Terra und Luna `low` bleiben Eval-only, und unabhängige Pässe teilen keinen Response-/Reasoning-Zustand.
- Ohne externe Autorisierung und Live-Manifest wird kein OpenAI-Live-Aufruf ausgeführt.
- API, Frontend und Traefik erfahren keine funktionale Änderung; jede kanonische Übernahme benötigt weiterhin Human Approval.
