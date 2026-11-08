# ADR 0014: Provider-neutral AI transports

- Status: accepted direction; implementation acceptance remains open
- Date: 2026-11-08
- Issue: [#66](https://github.com/itskeKs/verwalino/issues/66)

## Context and inventory

The owner's decision selects organization-owned keys, OpenAI-compatible APIs (including
explicitly configured self-hosted endpoints), Anthropic, and one administrator-selected
provider-listed model for every task. There is no application-owned key or automatic
provider fallback.

The existing declaration worker runs inventory, core extraction, rule extraction,
reconciliation and bounded targeted rechecks through `DeclarationExtractionProvider`.
`DeclarationPipeline` remains the deterministic manager: the model proposes typed
candidates; evidence validation, conflict routing, human review and approval decide what
can become application data. The model has no shell, database or arbitrary network
tools. Property questions use `KnowledgeAnswerProvider`, bounded retrieval and an
allowlist of citation identities. Invoice/intake extraction currently uses local OCR;
the workflow runner schedules these workers rather than an autonomous agent loop. No new
AI feature is introduced.

The existing pipeline reserves input/output/cost before calls, permits at most one
schema-only repair, aggregates usage across attempts, and retains a conservative
reservation when trustworthy usage is absent. Worker leases, per-run caps and the
organization's shared UTC monthly budget remain in place. Unknown-price models are
selectable and explicitly unpriced: their monetary limit is not enforced, while
input/output/attempt/time bounds still apply. Model names alone never establish prices
on another origin. The existing documented OpenAI prices remain scoped to OpenAI; the
already implemented Anthropic Haiku 4.5 tariff is scoped to Anthropic.

Tests use fake providers and mocked HTTP. `app.evals.recorded_import` reads actual
stored imports without constructing a provider, checks the unchanged Lindenallee
unit/share gold assertions and exports content-free provenance/accounting. Existing
sanitized OpenAI observations are comparison evidence, not evidence for another
provider. No model receives the evaluated badge from mocks or a default choice.

## Comparison

| Option | Data protection | Budget and deterministic control | Evidence | Operational weight |
| --- | --- | --- | --- | --- |
| Compatible transport plus Anthropic | Only the selected origin receives documents; local endpoints can keep inference on the owner's network. Retention depends on the provider contract. | Keeps existing reservations, typed contracts, manager and human approval. | Existing evaluation harness can inspect each actual product run. | Existing Python process and HTTP client; no new service or heavy runtime. |
| opencode through ACP | Adds a coding-agent process and its permissions/configuration to the document boundary. | Would require a second integration to constrain tools, retries and cost to the existing ledger. | Existing product evals would still need adapters; ACP itself proves no extraction quality. | Additional executable, process lifecycle and protocol. |
| Keep OpenAI only | Fixed remote provider; cannot satisfy the local/non-OpenAI owner decision. | Existing bounded manager and accounting work. | Existing OpenAI observations only. | Lowest immediate change, continued vendor lock-in. |

## Decision and implementation

Keep the existing interfaces and manager. The provider factory creates the existing
OpenAI Responses adapter for OpenAI, a Chat Completions adapter for compatible
endpoints, or the already implemented Messages adapter for Anthropic. Compatible
requests use `response_format.json_schema`; original Pydantic validation remains
mandatory. Refusals, incomplete responses and malformed schemas retain reported usage
and enter the existing bounded failure/repair paths. No response body or document text
is written to diagnostic logs.

The Chat Completions wire schema, UTF-8-byte reservation and provider identity have
their own static fingerprints; declaration harness version 4 records the initial
transport. After the local schema-capability probe failed, harness version 5
additionally places the exact wire schema in compatible system instructions; Q&A prompt
version 2 records its corresponding wrapper change. Original schema validation and gold
expectations are unchanged. Compatible models receive neither OpenAI reasoning/cache
extensions nor OpenAI pricing merely because their names match. Text and base64 image
payloads count against the existing bounded input allowance. A provider must actually
support structured output, image inputs when needed, and trustworthy token usage; being
listed does not certify those capabilities. Local correctness still requires the
unchanged domain/evidence checks.

The administrator selects provider, optional compatible API base URL, key and model in
Einstellungen. Changing provider or endpoint clears the saved credential unless a fresh
key is supplied; model discovery cannot forward the prior destination's key. Keys retain
tenant-bound encryption and are never returned. The existing organization
authorization/RLS boundary covers the same settings routes.

Self-hosting intentionally permits private/loopback HTTP endpoints. This authorizes the
selected API to receive documents and its explicitly entered key; administrators must
choose a trusted service. Link-local metadata, multicast, unspecified and other reserved
addresses are rejected. Hosted custom endpoints require HTTPS and public addresses.
Every connection resolves, validates all DNS results and pins a validated IP while
preserving Host/TLS SNI; redirects and environment proxies are disabled. Responses,
model pagination and timeouts are bounded. There is no fallback to a public provider
when a local endpoint fails.

`store=false` on OpenAI does not establish zero data retention. Other providers'
retention and region guarantees must be checked against their contracts. Anthropic uses
the documented supported `2023-06-01` API version and a workspace-scoped key; no
workspace selector or ZDR claim is introduced. Further Anthropic-specific work and live
validation are deferred by the owner's local-Qwen-first instruction.

## Evidence and acceptance status

Offline wire/security tests and the settings browser path exercise deterministic
contracts; they do not establish model quality. The actual local Qwen3.6-35B-A3B import
was tested once through the ordinary application/worker before any review edits. It
failed inventory schema validation after the normal two bounded attempts: both returned
HTTP 200 from the selected local Chat Completions endpoint, with matching
configured/returned model identity, 908 input tokens and 836 output tokens in total. No
downstream stages or proposals were reached and nothing was applied. The unchanged
recorded evaluator preserves this failure; no model is marked evaluated. The first run
remains frozen under harness 4. A separate plain-word capability probe returned non-JSON
despite a strict unrelated object schema, establishing that this endpoint does not
enforce response_format. The harness-5 compatibility repair makes the exact static
schema visible in system instructions, retains the original local validation and emits
only the existing bounded allowlisted diagnostic paths/codes. The changed harness-5
import was tested once with the existing provider timeout set to 180 seconds only in the
test worker. Inventory passed in 4.734 seconds; core extraction failed validation after
a 144.121-second request and its normal 123.663-second schema-only repair. Safe
diagnostic paths/codes reported unsupported core fields and invalid normalized values.
All three HTTP 200 requests went only to the configured local endpoint; configured and
returned model IDs matched. The recorded evaluator retained the untouched failure: 5,500
input and 10,170 output tokens, with no downstream stages or proposals. Nothing was
applied and the dependent property-Q&A check was not invoked. The cluster remained
unchanged and idle after the run. The original harness-4 capture is immutable and the
same- scorer comparison records inventory improvement without claiming acceptance.

Source defaults remain 60 seconds; the observed 144-second core request exceeds that
deadline, so default timing is not validated for this local service. The independent
40-second heartbeat renewed the normal 120-second lease; token/repair limits were
unchanged. Provider selection cannot change this operator deployment prerequisite. The
180-second setting avoided a timeout but did not establish model correctness.

The local price is unknown, not a claim of free billing. A small text probe produced
valid JSON, which does not prove server-enforced schemas. The synthetic image probe was
rejected (observed HTTP 404); actual visual consumption remains unverified. No cluster
reconfiguration occurred.

The working OpenAI format-2 baseline `lindenallee-12-luna-prereview-0ff1c6e6.json`
compares successfully: the same scorer reports `OBSERVED_REGRESSION` with partial
coverage and 16 regressed assertions for the failed local harness-5 run. The local
harness-4-to-5 comparison shows inventory improvement between two failed runs; it does
not establish equivalence to working OpenAI. The separate legacy
`lindenallee-12-luna-prereview-da9b1f60.json` uses an incompatible scorer version and is
correctly refused without conversion. External provider requests are prohibited by the
owner's current instruction. Issue #66 remains open: non-OpenAI eval/user acceptance has
not passed.

The offline harness-6 delta adds a compact compatible-only reminder for schemas
containing `FactPayload`: existing prefix/field pairs, tagged-object requirements and
unrelated structural examples. It uses one instruction composer for requests,
reservation estimates and fingerprints. Original prompts, validators and gold stay
unchanged. The safe diagnostic allowlist now recognizes `dict_type`; private values and
unknown paths remain masked. Red/green wire tests pass. QA instructions are unchanged by
this delta.

The one actual harness-6 import also failed core validation after its normal repair.
Inventory passed in 4.567 seconds; core and repair returned HTTP 200 in 92.164 and
91.740 seconds. Each validation event reports 12 errors; the eight retained entries are
unsupported subject/field pairs at `facts[5]` through `facts[12]`, with the remaining
entries truncated. No normalized-value error appears in that retained prefix; nothing
can be concluded about the unseen entries or exact rejected subject/field values. Total
reported usage is 5,946 input and 6,964 output tokens; USD zero remains an unknown-price
placeholder. The same working OpenAI format-2 baseline again reports 16 regressed
assertions with partial coverage. No candidate, apply, image recheck or property Q&A was
reached. The cluster stayed unchanged and idle, and all three requests went only to the
selected local endpoint. No model has passed non-OpenAI acceptance or been marked
evaluated.

## Later

- Broader provider-specific price/capability registries: add only with documented
  tariffs and actual evaluation evidence; unknown models remain visibly unpriced.
- Additional live Anthropic/provider matrices: resume after the owner's local-first
  milestone, without treating compatible-Qwen results as certification.
- Broader context/vision model tuning: only when representative imports expose a
  failure within the existing caps. No generic agent runtime is needed now.

## Sources

- [Owner decision](https://github.com/itskeKs/verwalino/issues/66)
- [Ollama OpenAI compatibility](https://docs.ollama.com/api/openai-compatibility)
- [vLLM structured output](https://docs.vllm.ai/en/v0.33.0/features/structured_outputs/)
- [Anthropic structured output](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)
- [Anthropic model listing](https://platform.claude.com/docs/en/api/models/list)
- [Anthropic retention](https://platform.claude.com/docs/en/manage-claude/api-and-data-retention)
- [Anthropic pricing](https://platform.claude.com/docs/en/about-claude/pricing)
- [HTTPX SNI extension](https://www.python-httpx.org/advanced/extensions/#sni_hostname)
- [opencode ACP](https://dev.opencode.ai/docs/cli/acp/)
