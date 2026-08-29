# Declaration Agentic Modernization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Modernize the declaration extraction workflow with deterministic evidence verification, bounded image escalation, explicit provider failures, sanitized per-pass observability, and gold-eval promotion gates while preserving human approval and every public surface.

**Architecture:** `DeclarationPipeline` remains the sole bounded manager: it runs fixed Responses API passes, validates locally, asks a pure deterministic router whether a page may receive the existing bounded vision recheck, then publishes only review candidates. Provider adapters return typed structured data plus sanitized metadata; a local evidence index, ledger, and eval harness never retain document content in durable telemetry.

**Tech Stack:** Python 3.12, FastAPI, Pydantic 2, SQLAlchemy 2 async, PostgreSQL 16, OpenAI Python SDK Responses API with Structured Outputs, pdfplumber/Tesseract/pdf2image, pytest, Docker Compose, React/TypeScript unchanged.

---

## Ausführungsstatus (maßgeblich, 2026-08-29)

| Bereich | Status |
| --- | --- |
| Tasks 1–6: Provider-Contract, Evidence, Router, Vision, Kosten, Pass-Ledger/RLS | implementiert und fokussiert verifiziert |
| Tasks 7–8: Gold-Eval, feste Modellvergleichslabels, passiver Adversarial-Fall, hart gegateter beschreibender Live-Smoke | implementiert und fokussiert verifiziert, ohne Live-Aufruf |
| Task 9: Dokumentation und Dokumentations-Contracts | implementiert und fokussiert verifiziert |
| Task 10: vollständige Backend-/Frontend-Suite, Fake-Eval, npm-Audit und finale unabhängige Review | technisch vollständig verifiziert; Review PASS, ohne Live-Aufruf |

Die nachfolgenden `[ ]`-Unterpunkte sind das historische TDD- und
Review-Rezept, nicht ein Live-Fortschrittsindikator. Diese Tabelle ist der
aktuelle Status. Es wurde kein Commit erzeugt und kein echter PDF- oder
Live-Provider-Aufruf ausgeführt.

## Boundary and non-negotiable constraints

- Do not change any API route, response shape, frontend source, Traefik configuration, approval rule, or canonical-data mutation path.
- Do not introduce the Agents SDK, multiple agents, handoffs, tools, model-side side effects, free-form JSON parsing, or autonomous approval.
- Do not persist or log prompts, PDF/OCR text, quotes, images, provider bodies, property/tenant/user IDs, local PDF paths, exception bodies, or credentials in any new ledger/report.
- Do not make a live provider call while implementing this plan. The only future live command requires separate explicit authorization and the external manifest gates in Task 8.
- Do not commit. Each task ends with a diff and verification checkpoint instead of a commit step.

## File map

### Create

- `backend/app/declarations/evidence.py` — run-local evidence text index, canonical normalization, and quote validation result.
- `backend/app/declarations/routing.py` — pure RouterDecision contract and full deterministic decision table.
- `backend/app/declarations/observability.py` — allowlisted per-pass metadata, prompt/schema fingerprinting, and safe ledger serialization.
- `backend/migrations/versions/0028_declaration_pass_ledger.py` — additive tenant-bound run-pass persistence after the current `0027` head.
- `backend/tests/test_declaration_evidence.py` — quote/page, normalization, and no-content failure tests.
- `backend/tests/test_declaration_routing.py` — total router decision-table tests and cap/order tests.
- `backend/tests/test_declaration_observability.py` — ledger allowlist/fingerprint/redaction tests.

### Modify

- `backend/app/declarations/provider.py` — typed response metadata and explicit provider failure classes.
- `backend/app/declarations/openai_provider.py` — classify Responses outcomes, collect safe IDs/model data, and restrict schema repair to schema failures.
- `backend/app/declarations/fake_provider.py` — deterministic metadata/failure fixtures matching the production contract.
- `backend/app/declarations/batching.py` — bounded, deterministic provider-batch preparation.
- `backend/app/core/config.py` — explicit reasoning effort and cache-rate configuration without a Terra production default.
- `backend/app/declarations/preflight.py` — bounded, ephemeral page-image rendering seam for a selected page only.
- `backend/app/declarations/pipeline.py` — image-capable selected `PageInput`, evidence gate, pure router call, pass ledger checkpointing, and unchanged publish/approval path.
- `backend/app/declarations/validation.py` — combine existing domain validation with quote results without replacing current checks.
- `backend/app/models/__init__.py`, `backend/app/models/declaration_import.py`, `backend/app/repositories/declaration_import_repo.py` and `backend/app/services/declaration_import_service.py` — run-scoped ledger persistence/read support, tenant-scoped and additive; `append_pass_entries` ist der einzige Append-Pfad.
- `backend/app/evals/declaration_knowledge.py` — versioned representative-gold scoring, model comparison and safe live-gate reporting.
- `backend/tests/fixtures/declaration_eval_cases.json` — neutrale synthetische Goldbeobachtungen zu Einheiten, MEA, Regeln, Heizung, Änderungen, Evidence-Seiten und passivem hostile document text.
- `backend/tests/test_declaration_batching.py`, `backend/tests/test_declaration_import_api.py`, `backend/tests/test_declaration_preflight.py`, `backend/tests/test_declaration_real_pdf_smoke.py`, `backend/tests/test_declaration_pass_ledger.py` and `backend/tests/test_rls_coverage.py` — Integration, RLS und Live-Gate-Contracts.
- `backend/tests/test_declaration_openai_provider.py` — mocked Responses classification and safe metadata contract tests.
- `backend/tests/test_declaration_runtime_config.py` — effort/cache-rate configuration tests.
- `backend/tests/test_declaration_pipeline.py` — regular path, vision path, failure and persistence integration tests.
- `backend/tests/test_declaration_evals.py` — gold dimensions, comparison gates and no-live-without-gates tests.
- `.env.example` and `docker-compose.prod.yml` — explizite, serverseitige Runtime-Konfiguration ohne Terra-Produktionsrate.
- `README.md`, `docs/architecture.md`, `docs/roadmap.md`, `docs/operations/declaration-imports.md`, `docs/superpowers/specs/2026-08-29-declaration-agentic-modernization-design.md` and this plan — current setup, trust boundary, runbook, roadmap, decision record and plan status.

## Shared test setup

Every backend test in this plan uses the existing isolated declaration database and no network request:

```bash
docker compose -p hv-declaration-tests -f infra/docker-compose.declaration-test.yml up -d --wait
unset hv_database_url hv_database_url_migrator hv_database_url_sync database_url database_url_migrator database_url_sync
export HV_DATABASE_URL='postgresql+asyncpg://app_rw:app_secret@localhost:55432/hausverwaltung'
export HV_DATABASE_URL_MIGRATOR='postgresql+asyncpg://app_migrator:migrator_secret@localhost:55432/hausverwaltung'
export HV_DATABASE_URL_SYNC='postgresql://app_migrator:migrator_secret@localhost:55432/hausverwaltung'
export HV_ENVIRONMENT=test
cd backend
```

Expected: the isolated service is healthy on port 55432. Never point these variables at a user database.

### Task 1: Define the safe observability and explicit failure contracts

**Files:**

- Create: `backend/app/declarations/observability.py`
- Modify: `backend/app/declarations/provider.py`
- Modify: `backend/app/declarations/fake_provider.py`
- Test: `backend/tests/test_declaration_observability.py`
- Test: `backend/tests/test_declaration_openai_provider.py`

- [ ] **Step 1: Write contract tests before production changes**

Add `test_pass_metadata_rejects_document_content_fields`, `test_safe_ledger_payload_contains_only_allowlisted_fields`, `test_independent_prompt_schema_harness_fingerprints`, `test_provider_failure_classes_are_distinct`, and `test_usage_cost_rejects_invalid_cache_token_partition`. Build the test values from this exact safe payload:

```python
safe = {
    "pass_id": "inventory", "attempt_no": 1, "stage": "provider",
    "configured_model": "gpt-5.6-luna", "returned_model_id": "gpt-5.6-luna",
    "reasoning_effort": "medium", "request_id": "req_123",
    "prompt_version": "declaration-prompt-v2", "prompt_sha256": "a" * 64,
    "schema_version": "declaration-schema-v2", "schema_sha256": "b" * 64,
    "harness_version": "responses-harness-v2", "harness_sha256": "c" * 64,
    "input_tokens": 12, "cached_input_tokens": 2, "cache_write_input_tokens": 1, "output_tokens": 4,
    "estimated_cost_usd": "0.000012", "latency_ms": 17, "retry_count": 0,
    "route_action": "NONE", "route_reason_codes": [], "failure_class": None,
}
```

Assert that adding `quote`, `prompt`, `document_key`, `image_data_url`, `response_body`, `tenant_id`, `path`, or `exception_message` raises `ValueError` and that no rejected value appears in the exception text.

- [ ] **Step 2: Run the new tests and verify RED**

Run: `./.venv/bin/pytest -q tests/test_declaration_observability.py`

Expected: FAIL during collection because `app.declarations.observability` does not exist.

- [ ] **Step 3: Implement immutable, allowlisted metadata**

Create `ProviderPassMetadata` and `PassLedgerEntry` as frozen dataclasses. Give `PassLedgerEntry.as_safe_dict()` only the fields listed in the design, validate ASCII enums with anchored regexes, represent cost as `Decimal`, and calculate three independent hashes: `prompt_sha256` from the static Developer-Prompt, `schema_sha256` from canonical Structured-Output JSON Schema and `harness_sha256` from the static request harness. No hash input includes document, reconciliation, quote, image, tenant or request data. Add these explicit `ProviderFailure` codes in `provider.py`:

```python
PROVIDER_REFUSAL = "PROVIDER_REFUSAL"
PROVIDER_INCOMPLETE = "PROVIDER_INCOMPLETE"
PROVIDER_TIMEOUT = "PROVIDER_TIMEOUT"
PROVIDER_SCHEMA_FAILURE = "PROVIDER_SCHEMA_FAILURE"
```

Extend `ProviderUsage` with `cached_input_tokens` and `cache_write_input_tokens`; extend `ProviderCallResult` only with the typed metadata needed to build a safe pass entry. Calculate cost as ordinary input plus cached input plus cache-write input plus output; reject invalid token partitions or absent/invalid rates before publishing a result. Extend the fake provider so every successful fake pass has fixed safe configured and returned model IDs, `reasoning_effort`, `request_id=None`, deterministic three-part versions/hashes and zero cache-write tokens; never synthesize a document-derived value.

- [ ] **Step 4: Add explicit runtime configuration and its tests**

In `backend/app/core/config.py`, add `declaration_primary_reasoning_effort="medium"`, `declaration_escalation_reasoning_effort="none"`, `declaration_primary_cached_input_usd_per_million=Decimal("0.02")`, `declaration_primary_cache_write_multiplier=Decimal("1.25")`, and `declaration_escalation_cached_input_usd_per_million=Decimal("0.075")`. Retain the existing normal input/output defaults (`0.20/1.20` Luna and `0.75/4.50` Mini); do not add Terra rates. Validate the two effort enums, all rates as positive finite decimals and the Luna cache-write multiplier as exactly `1.25`.

Add `test_declaration_runtime_defaults_use_explicit_medium_and_none_effort`, `test_declaration_runtime_defaults_use_verified_luna_and_mini_cached_rates`, and `test_invalid_effort_or_cache_rate_is_rejected` in `backend/tests/test_declaration_runtime_config.py`.

- [ ] **Step 5: Run GREEN contract tests**

Run: `./.venv/bin/pytest -q tests/test_declaration_observability.py tests/test_declaration_openai_provider.py tests/test_declaration_runtime_config.py`

Expected: PASS; the mocked SDK is the only Responses client used.

- [ ] **Step 6: Review checkpoint**

Run: `git diff --check && git diff -- backend/app/declarations/provider.py backend/app/declarations/observability.py backend/app/declarations/fake_provider.py backend/app/core/config.py backend/tests/test_declaration_observability.py backend/tests/test_declaration_runtime_config.py`

Expected: no whitespace errors; no prompt, document text, image data, credential, API or frontend change in the diff.

### Task 2: Make quote-to-page evidence validation deterministic

**Files:**

- Create: `backend/app/declarations/evidence.py`
- Modify: `backend/app/declarations/validation.py`
- Modify: `backend/app/declarations/pipeline.py`
- Test: `backend/tests/test_declaration_evidence.py`
- Test: `backend/tests/test_declaration_pipeline.py`

- [ ] **Step 1: Write quote validation tests**

Create tests named `test_quote_on_exact_referenced_page_is_accepted`, `test_quote_normalization_handles_unicode_whitespace_and_dash_variants`, `test_quote_on_another_page_is_blocked`, `test_unknown_document_or_page_is_not_routable`, and `test_quote_failure_message_contains_no_quote_or_page_text`. Use `PageInput("declaration-main", 4, "Heizkosten – 70 % Verbrauch", 0.99)` and test both the accepted quote `"Heizkosten - 70 % Verbrauch"` and the rejected page-5 reference.

- [ ] **Step 2: Run RED evidence tests**

Run: `./.venv/bin/pytest -q tests/test_declaration_evidence.py`

Expected: FAIL during collection because `EvidenceTextIndex` and `validate_evidence_quote` do not exist.

- [ ] **Step 3: Implement the run-local index and gate**

Implement `EvidenceTextIndex.from_pages(pages)` as an in-memory mapping keyed by `(document_key, pdf_page)`. Implement `normalize_evidence_text(value)` with Unicode NFKC, CR/LF conversion, whitespace collapse, dash unification and `casefold()`. Implement `validate_evidence_quote(evidence, index)` to return an issue with code `EVIDENCE_QUOTE_NOT_FOUND` only when a known page lacks the normalized contiguous quote; preserve the existing `UNKNOWN_EVIDENCE_DOCUMENT` and `EVIDENCE_PAGE_OUT_OF_RANGE` checks.

Add an optional `evidence_index` parameter to `validate_reconciliation`, then append quote issues after the current document/page validation. In `DeclarationPipeline`, build the index from the same local pages passed to batching and supply it before `_candidate_drafts`. Do not persist the index or its normalized strings.

- [ ] **Step 4: Run GREEN evidence and regression tests**

Run: `./.venv/bin/pytest -q tests/test_declaration_evidence.py tests/test_declaration_pipeline.py -k 'evidence or validation or known_out_of_range'`

Expected: PASS; an invented quote produces a blocker but does not expose the quote in a failure message.

- [ ] **Step 5: Review checkpoint**

Run: `git diff --check && rg -n 'quote|text' backend/app/declarations/evidence.py backend/app/declarations/validation.py`

Expected: document text is used only in the ephemeral index and never in a log, ORM field, exception detail, or safe ledger dictionary.

### Task 3: Add a pure deterministic escalation router

**Files:**

- Create: `backend/app/declarations/routing.py`
- Modify: `backend/app/declarations/pipeline.py`
- Test: `backend/tests/test_declaration_routing.py`
- Test: `backend/tests/test_declaration_pipeline.py`

- [ ] **Step 1: Write the complete decision-table tests**

Create `test_failure_classes_never_route_to_vision`, `test_unknown_evidence_never_routes_to_vision`, `test_missing_quote_on_renderable_known_page_routes_to_mini`, `test_low_quality_cited_blocker_page_routes_to_mini`, `test_domain_blocker_without_visual_signal_routes_to_human_review`, and `test_router_sorts_deduplicates_and_caps_page_keys`. Each test passes only typed local data: issue code, severity, existing page keys, quality, renderability and failure class; it must not accept confidence or text.

- [ ] **Step 2: Run RED router tests**

Run: `./.venv/bin/pytest -q tests/test_declaration_routing.py`

Expected: FAIL during collection because `RouterDecision` and `route_escalation` do not exist.

- [ ] **Step 3: Implement the total router contract**

Create:

```python
@dataclass(frozen=True)
class RouterDecision:
    action: Literal["NONE", "VISION_RECHECK", "HUMAN_REVIEW"]
    reason_codes: tuple[str, ...]
    page_keys: tuple[tuple[str, int], ...]
    model: str | None

def route_escalation(*, issues, page_inventory, provider_failure, max_pages: int) -> RouterDecision: ...
```

Implement exactly the six-priority table in the design. For `VISION_RECHECK`, set `model="gpt-5.4-mini"`; otherwise set `model=None`. Reject `max_pages < 1` before a provider call. Do not import the provider, inspect document text, call a model or use confidence as an input.

Replace the current broad `_selected_recheck_pages` policy with an adapter that first calls `route_escalation`; preserve all existing recheck count/token/cost reservations in `_call_rechecks`.

- [ ] **Step 4: Run GREEN router and pipeline tests**

Run: `./.venv/bin/pytest -q tests/test_declaration_routing.py tests/test_declaration_pipeline.py -k 'recheck or router or evidence'`

Expected: PASS; a high-confidence but quote-missing page routes from its local evidence signal, and a low-confidence page alone does not route.

- [ ] **Step 5: Review checkpoint**

Run: `git diff --check && rg -n 'confidence' backend/app/declarations/routing.py`

Expected: no match; the router has no model-self-confidence dependency.

### Task 4: Produce real bounded vision input only after routing

**Files:**

- Modify: `backend/app/declarations/preflight.py`
- Modify: `backend/app/declarations/pipeline.py`
- Modify: `backend/app/declarations/openai_provider.py`
- Test: `backend/tests/test_declaration_preflight.py`
- Test: `backend/tests/test_declaration_pipeline.py`
- Test: `backend/tests/test_declaration_openai_provider.py`

- [ ] **Step 1: Write red tests for selected-page rendering**

Add `test_preflight_renders_only_explicit_selected_page_as_ephemeral_data_url`, `test_regular_pipeline_passes_never_receive_image_data_url`, `test_router_selected_page_reaches_mini_as_input_image`, and `test_unrenderable_selected_page_becomes_human_review_without_sdk_call`. The SDK mock assertion must require `input_image` only in the recheck request and require every Luna inventory/core/rules request to contain no image block.

- [ ] **Step 2: Run RED vision tests**

Run: `./.venv/bin/pytest -q tests/test_declaration_preflight.py tests/test_declaration_pipeline.py tests/test_declaration_openai_provider.py -k 'image or vision or selected_page'`

Expected: FAIL because regular pipeline-created `PageInput` has no selectable image source.

- [ ] **Step 3: Implement a narrow local rendering seam**

Add `PdfPreflight.render_page_data_url(pdf_bytes, page_number, *, budget)` that reuses the existing pixel, deadline and temporary-byte protections and returns only a local `data:image/png;base64,...` value. It must neither create a durable file nor alter `PageArtifact` storage. Retain source document bytes only within `_preflight_documents` long enough to map router-selected `(document_key, pdf_page)` values to the existing local store object; render after a `VISION_RECHECK` decision, attach `image_data_url` only to those recheck `PageInput` instances, and discard the values after `_call_rechecks`.

In `OpenAiDeclarationProvider`, keep `_page_content_blocks(..., include_images=True)` exclusive to `recheck`; reject an image block for inventory/core/rules/reconcile before SDK invocation. Keep `detail="high"`, `store=False`, configured image-rate estimation and existing hard recheck bounds.

- [ ] **Step 4: Run GREEN selected-vision tests**

Run: `./.venv/bin/pytest -q tests/test_declaration_preflight.py tests/test_declaration_pipeline.py tests/test_declaration_openai_provider.py -k 'image or vision or selected_page or recheck'`

Expected: PASS; exactly the selected local page is supplied to Mini, no primary pass gets an image, and rendering failure triggers no alternate upload.

- [ ] **Step 5: Review checkpoint**

Run: `git diff --check && git diff -- backend/app/declarations/preflight.py backend/app/declarations/pipeline.py backend/app/declarations/openai_provider.py`

Expected: no new network client, tool call, persistence field for image data, API route, frontend file or Traefik file.

### Task 5: Classify Responses outcomes without conflating repair and retry

**Files:**

- Modify: `backend/app/declarations/openai_provider.py`
- Modify: `backend/app/declarations/pipeline.py`
- Test: `backend/tests/test_declaration_openai_provider.py`
- Test: `backend/tests/test_declaration_pipeline.py`

- [ ] **Step 1: Add response-outcome tests**

Create `test_refusal_is_permanent_and_never_repairs_or_retransmits`, `test_incomplete_is_distinct_from_schema_failure`, `test_timeout_is_retryable_with_sanitized_usage`, `test_schema_failure_gets_exactly_one_schema_only_repair`, `test_second_schema_failure_never_makes_a_third_call`, `test_luna_uses_medium_without_previous_response_id_and_static_cache_key`, and `test_mini_uses_none_without_explicit_cache_write`. Extend the mocked response fixture with a response model ID, request ID and cached-token details; assert safe metadata preserves only allowlisted identifiers.

- [ ] **Step 2: Run RED outcome tests**

Run: `./.venv/bin/pytest -q tests/test_declaration_openai_provider.py tests/test_declaration_pipeline.py -k 'refusal or incomplete or timeout or schema_failure'`

Expected: FAIL because current `incomplete` is treated as a schema-repair candidate.

- [ ] **Step 3: Implement explicit classification**

In `OpenAiDeclarationProvider._run`, classify in this order: refusal payload to `PROVIDER_REFUSAL` (permanent), `status == "incomplete"` to `PROVIDER_INCOMPLETE` (retryability from the existing configured worker policy), transport timeout to `PROVIDER_TIMEOUT` (retryable), and parser/strict-schema mismatch to `PROVIDER_SCHEMA_FAILURE`. Only the last category performs one repair call. Aggregate usage only from observed safe token counters; reserve/charge conservatively when the response omits usage. Capture latency with monotonic time and response model/request ID only if they pass the metadata validator.

Set Luna requests to `reasoning={"effort": "medium"}` and Mini requests to `reasoning={"effort": "none"}` from the new validated config. Do not set `previous_response_id` on any pass. For Luna only, set a stable `prompt_cache_key` derived exclusively from model/pass/prompt/schema/harness versions/hashes and place the explicit cache breakpoint immediately after the static developer prompt; dynamic document and reconciliation content must follow it and never be deliberately cache-written. Do not set an explicit cache-write policy on Mini; record its returned implicit cached-token count if supplied.

Calculate `ProviderUsage.cost_usd` fail-closed using `ordinary = input_tokens - cached_input_tokens - cache_write_input_tokens`: `ordinary * input_rate + cached_input_tokens * cached_input_rate + cache_write_input_tokens * (input_rate * Decimal("1.25")) + output_tokens * output_rate`, divided by one million. Require nonnegative integer counters, positive finite configured rates, `cached_input_tokens + cache_write_input_tokens <= input_tokens`, and `cache_write_input_tokens == 0` for Mini. Any violation raises `PROVIDER_USAGE_INVALID` before a result or ledger entry is published.

In `_call` and `_call_recheck_batch`, settle the existing reservation and append a safe failure ledger entry before re-raising; retain existing retry-state transitions. Do not use exception strings as error codes or ledger values.

- [ ] **Step 4: Run GREEN outcome tests**

Run: `./.venv/bin/pytest -q tests/test_declaration_openai_provider.py tests/test_declaration_pipeline.py -k 'refusal or incomplete or timeout or schema_failure or reservation'`

Expected: PASS; refusal makes one SDK call, incomplete makes no repair call, schema failure makes at most two calls, timeout remains retryable.

- [ ] **Step 5: Review checkpoint**

Run: `git diff --check && rg -n 'str\(exc\)|response\.text|output_text|exception_message' backend/app/declarations/openai_provider.py backend/app/declarations/pipeline.py`

Expected: no match for durable telemetry construction.

### Task 6: Persist and expose only the sanitized per-pass ledger internally

**Files:**

- Create: `backend/migrations/versions/0028_declaration_pass_ledger.py`
- Modify: `backend/app/models/declaration_import.py`
- Modify: `backend/app/repositories/declaration_import_repo.py`
- Modify: `backend/app/declarations/pipeline.py`
- Test: `backend/tests/test_declaration_pipeline.py`
- Test: `backend/tests/test_rls_coverage.py`

- [ ] **Step 1: Write red persistence and isolation tests**

Add `test_pass_ledger_persists_one_safe_row_per_attempt`, `test_failed_pass_is_ledgered_before_retry_wait`, `test_status_endpoint_keeps_existing_aggregate_shape`, and `test_declaration_pass_ledger_is_covered_by_rls`. The persisted JSON must equal `PassLedgerEntry.as_safe_dict()`, including `reasoning_effort`, all six separate prompt/schema/harness version/hash fields and both cache-token fields; it must reject a payload containing `"quote"`, `"prompt"`, `"document_key"` or `"image_data_url"`.

- [ ] **Step 2: Run RED persistence tests**

Run: `./.venv/bin/pytest -q tests/test_declaration_pipeline.py tests/test_rls_coverage.py -k 'ledger'`

Expected: FAIL because no pass-ledger model/table/repository method exists.

- [ ] **Step 3: Add the additive migration and repository operation**

Create `backend/migrations/versions/0028_declaration_pass_ledger.py` with `revision = "0028"` and `down_revision = "0027"`. It creates a `declaration_import_pass` table with `id`, `organization_id`, `declaration_extraction_run_id`, ordered `attempt_no`, `safe_metadata JSONB`, timestamps, foreign keys and indexes matching existing declaration tables. Enable and force RLS with the repository's current organization policy; the DB contract validates exactly 23 safe keys, types, allowlists and attempt binding; register it in `test_rls_coverage.py`.

Add a typed ORM model and `DeclarationImportRepository.append_pass_entries(run_id, entries)`. It serializes through `as_safe_dict()` immediately before persistence and rejects non-typed input. Call it at each logical pass success and failure boundary inside the active lease transaction; drain only after the enclosing checkpoint/fail/publish transaction commits. Do not add the ledger to the public status schema or API response.

- [ ] **Step 4: Run GREEN migration and persistence tests**

Run: `./.venv/bin/pytest -q tests/test_declaration_pipeline.py tests/test_rls_coverage.py -k 'ledger'`

Expected: PASS; tenant isolation holds and status aggregates remain unchanged.

- [ ] **Step 5: Review checkpoint**

Run: `git diff --check && git diff -- backend/migrations/versions backend/app/models/declaration_import.py backend/app/repositories/declaration_import_repo.py`

Expected: an additive tenant-bound table only; no data backfill, destructive migration or public API schema change.

### Task 7: Extend deterministic evals with representative gold accuracy

**Files:**

- Modify: `backend/tests/fixtures/declaration_eval_cases.json`
- Modify: `backend/app/evals/declaration_knowledge.py`
- Modify: `backend/tests/test_declaration_evals.py`

- [ ] **Step 1: Write failing representative-gold tests**

Add named tests `test_gold_fixture_covers_units_mea_rules_heating_amendment_and_evidence_pages`, `test_gold_fixture_contains_adversarial_document_data_without_executable_instruction`, `test_quote_text_accuracy_rejects_wrong_page_or_quote`, `test_model_comparison_requires_identical_case_set`, and `test_promotion_gate_rejects_quality_regression_despite_lower_cost`. Define neutral case IDs `gold-units-mea`, `gold-heating-amendment`, and `gold-adversarial-evidence`; only synthetic redacted text may be versioned.

- [ ] **Step 2: Run RED gold tests**

Run: `./.venv/bin/pytest -q tests/test_declaration_evals.py -k 'gold or quote_text_accuracy or promotion or model_comparison'`

Expected: FAIL because the current fixture/report does not contain versioned gold dimensions or comparison gates.

- [ ] **Step 3: Implement gold schema and metrics**

Extend `gold_case` and `evaluate_case` with typed, content-safe comparison inputs for expected units, exact MEA fractions, rule effects, amendment lineage, evidence `(source_id, page, quote_hash)` and adversarial-input outcome. Hash an expected synthetic quote before comparison; reports retain only hash/match boolean, never quote content. Add metrics `evidence_quote_text_accuracy`, `heating_effect_accuracy`, `amendment_effect_accuracy`, and per-dimension exact counters. Der passive Adversarial-Marker wird aus tatsächlichem synthetischem `PageInput.text` gelesen und muss zu Abstention, keinen Aktionen und Router `NONE` führen.

Add `ModelComparisonReport` requiring the exact same neutral case IDs for `luna_medium`, `luna_low`, `terra_primary`, and `luna_with_mini_router`. Implement `validate_promotion_gates(candidate, baseline)` to require all existing safety gates, `evidence_quote_text_accuracy == Decimal("1")`, no decrease in each representative quality metric, and an identical-case comparison of Luna `medium` versus `low`. It returns only stable gate names.

- [ ] **Step 4: Run GREEN deterministic evals**

Run: `./.venv/bin/pytest -q tests/test_declaration_evals.py && ./.venv/bin/python -m app.evals.declaration_knowledge --fixtures tests/fixtures/declaration_eval_cases.json --provider fake`

Expected: PASS; the fake CLI emits only neutral IDs/metrics and no fixture text.

- [ ] **Step 5: Review checkpoint**

Run: `git diff --check && rg -n 'ignore previous|quote' backend/tests/fixtures/declaration_eval_cases.json backend/app/evals/declaration_knowledge.py`

Expected: synthetic hostile text appears only in the fixture input; emitted reports and error strings use neutral case IDs, hashes and stable gate names.

### Task 8: Add a gated comparison harness without enabling live traffic

**Files:**

- Modify: `backend/app/evals/declaration_knowledge.py`
- Modify: `backend/tests/test_declaration_evals.py`
- Modify: `docs/operations/declaration-imports.md`

- [ ] **Step 1: Write red gate tests**

Add `test_live_comparison_requires_compare_models_flag_authorization_external_manifest_and_hash`, `test_live_comparison_does_not_open_pdf_when_any_gate_is_missing`, and `test_live_comparison_report_contains_model_comparison_and_safe_pass_metadata_only`. Mock the manifest reader and provider factory; assert both remain uncalled when `--compare-models` or any other gate is absent.

- [ ] **Step 2: Run RED live-gate tests**

Run: `./.venv/bin/pytest -q tests/test_declaration_evals.py -k 'live_comparison or live_gate'`

Expected: FAIL because the current live report does not model the three-way comparison or per-pass safe metadata.

- [ ] **Step 3: Implement gated, externally manifested comparison**

Extend the existing `live_eval_missing_gates`, `validate_real_pdf_manifest`, and `run_real_pdf_smoke` rather than creating a second live client. **Phase A** validates caller-supplied `--provider live --live --compare-models`, exact `HV_DECLARATION_LIVE_EVAL=1`, nonempty server-side `HV_OPENAI_API_KEY`, presence of `HV_DECLARATION_REAL_PDF_MANIFEST` and all three positive caller-verified Terra rates before every Path operation. **Phase B** then validates external report/manifest paths, absolute-path equality with `HV_DECLARATION_REAL_PDF_MANIFEST`, case limits and manifest/PDF SHA-256 locally; it may read locally, but a failure constructs no capability, adapter or provider and performs no transfer. Construct each provider configuration one at a time; close it before the next. Persist a `descriptive_only`, non-promotion report only to caller-selected paths outside the repository. Return neutral case IDs, aggregate model metrics, safe pass metadata and stable gates only.

- [ ] **Step 4: Run GREEN live-gate tests without a live call**

Run: `./.venv/bin/pytest -q tests/test_declaration_evals.py -k 'live_comparison or live_gate or manifest'`

Expected: PASS; no SDK network method is invoked by the tests.

- [ ] **Step 5: Review checkpoint**

Run: `git diff --check && rg -n 'run_real_pdf_smoke|HV_DECLARATION_LIVE_EVAL|REAL_PDF_MANIFEST' backend/app/evals/declaration_knowledge.py docs/operations/declaration-imports.md`

Expected: every documented live command includes the explicit gates and says that separate authorization is required.

### Task 9: Refresh the top-level documentation and operational runbook

**Files:**

- Modify: `README.md`
- Modify: `docs/architecture.md`
- Modify: `docs/roadmap.md`
- Modify: `docs/operations/declaration-imports.md`
- Modify: `docs/superpowers/specs/2026-08-29-declaration-agentic-modernization-design.md`
- Modify: `docs/superpowers/plans/2026-08-29-declaration-agentic-modernization.md`

- [ ] **Step 1: Write documentation acceptance checks**

Add `test_readme_declaration_setup_includes_all_required_extras` to `backend/tests/test_declaration_runtime_config.py`. It reads only `README.md` and requires the exact installation command `uv sync --extra dev --extra ocr --extra ops`. Add `test_runbook_states_no_live_call_without_all_gates` requiring refusal/incomplete/timeout/schema failure, quote validation, router reason codes and Human Approval wording in the runbook.

- [ ] **Step 2: Run RED documentation checks**

Run: `./.venv/bin/pytest -q tests/test_declaration_runtime_config.py -k 'readme or runbook'`

Expected: FAIL because the current README/runbook do not describe all modernization contracts and the README lacks the exact extras command.

- [ ] **Step 3: Update each document at its information boundary**

In `README.md`, add only the local installation quick start and a link to the operations runbook. In `docs/architecture.md`, add a dated declaration-import section that describes the fixed manager workflow, explicit Luna/Mini reasoning policy, no shared response state, local quote gate, deterministic router, ephemeral image boundary, static-only Luna cache key/breakpoint and unchanged Human Approval. In `docs/roadmap.md`, mark this plan as the next bounded modernization milestone and list its explicit non-goals. In `docs/operations/declaration-imports.md`, document failure classes, separated safe-ledger fields, cache-cost validation, router table, selected-page vision limit, gold/comparison gates, `--compare-models`, recovery and the no-live-without-authorization rule. Keep this design and plan synchronized with implemented names; never add a production key or a real manifest example.

- [ ] **Step 4: Run GREEN documentation checks**

Run: `./.venv/bin/pytest -q tests/test_declaration_runtime_config.py -k 'readme or runbook'`

Expected: PASS; documentation names the exact setup command and all live gates.

- [ ] **Step 5: Review checkpoint**

Run: `git diff --check && git diff -- README.md docs/architecture.md docs/roadmap.md docs/operations/declaration-imports.md`

Expected: no secret, manifest path, PDF text, unsupported product commitment or API/frontend/Traefik change.

### Task 10: Run the focused verification matrix and inspect scope

**Files:**

- Modify: no additional files

- [ ] **Step 1: Run focused backend tests**

Run: `./.venv/bin/pytest -q tests/test_declaration_evidence.py tests/test_declaration_routing.py tests/test_declaration_observability.py tests/test_declaration_openai_provider.py tests/test_declaration_pipeline.py tests/test_declaration_evals.py tests/test_declaration_runtime_config.py tests/test_rls_coverage.py`

Expected: PASS; no test opens an external manifest or invokes the network.

- [ ] **Step 2: Run deterministic fake eval**

Run: `./.venv/bin/python -m app.evals.declaration_knowledge --fixtures tests/fixtures/declaration_eval_cases.json --provider fake`

Expected: exit 0 and JSON containing neutral case IDs/metrics only.

- [ ] **Step 3: Run the existing broader verification commands**

Run: `./.venv/bin/pytest -q`

Expected: at least the current baseline remains green: 1075 passed, 2 skipped before this work; report any changed count or failure exactly.

Run: `cd ../frontend && npm test && npm run lint && npm run build`

Expected: the existing frontend suite (baseline 56 passed), lint and production build remain green; no frontend source change is expected.

- [ ] **Step 4: Inspect the final diff and document unresolved failures**

Run: `git status --short && git diff --check && git diff --stat && git diff --name-only`

Expected: only the file map above plus any test-support migration registration changed. If an unrelated user change appears, leave it untouched and list it separately. If a test fails, do not mask it; record command, failure class and whether it blocks rollout.

- [ ] **Step 5: Final review checkpoint instead of a commit**

Confirm all of the following in the handoff: no commit was created; no provider live call was made; no API/frontend/Traefik behavior changed; the Human Approval gate remains present; and only safe metadata can persist or appear in reports.

## Plan self-review

Coverage: Tasks 1–6 implement typed failures, explicit effort/cache cost accounting, untergeordnetes SDK-Attempt-Ledger, quote validation, deterministic routing, true selected-page vision and durable observability. Tasks 7–8 add the required representative gold cases, Luna `medium`/`low`, Terra and Mini comparison plus gated live evaluation through `--compare-models`. Task 9 makes README, architecture, roadmap, runbook, design and plan current. Task 10 verifies scope and records every failure.

Consistency: `EVIDENCE_QUOTE_NOT_FOUND`, `PROVIDER_REFUSAL`, `PROVIDER_INCOMPLETE`, `PROVIDER_TIMEOUT`, `PROVIDER_SCHEMA_FAILURE`, `RouterDecision`, `route_escalation`, `PassLedgerEntry`, `prompt_sha256`, `schema_sha256`, `harness_sha256`, `evidence_quote_text_accuracy`, `validate_promotion_gates`, and `--compare-models` use the same spelling in every task.

No-live assurance: all listed test commands use fake/mock providers. The one future live harness is explicitly gated, external-manifested and requires separate user authorization.

## Execution handoff

Tasks 1–10 sind implementiert und technisch verifiziert. Das ist keine fachliche, Privacy- oder Produktivfreigabe; ein Live-Eval benötigt weiterhin die separate Autorisierung und alle externen Gates. Der Plan ist in `docs/superpowers/plans/2026-08-29-declaration-agentic-modernization.md` dokumentiert. Kein Commit und kein Live-Provider-Aufruf wurden durch diese Arbeit erzeugt.
