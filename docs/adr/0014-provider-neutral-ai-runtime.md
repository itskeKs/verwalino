# ADR 0014: Provider-neutral AI transports

- Status: accepted bounded transport/settings decision
- Date: 2026-11-08
- Issue: [#66](https://github.com/itskeKs/verwalino/issues/66)
- Scope: [owner decision](https://github.com/itskeKs/verwalino/issues/66#issuecomment-6722415190); evaluation follow-up [#399](https://github.com/itskeKs/verwalino/issues/399)

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

The compatible Chat Completions contract has separate static fingerprints and
harness version 7. It includes the strict wire schema and generated structural
examples in system instructions, with the same envelope used for conservative
UTF-8-byte reservations and fingerprinting. Original prompts, Pydantic validators
and gold expectations remain unchanged. The global OpenAI harness default remains
version 3; run headers and pass metadata use the selected adapter's version.
Property Q&A retains its separate versioned contract.

Compatible models receive neither OpenAI reasoning/cache extensions nor OpenAI
pricing merely because their names match. Text and base64 image payloads count
against the existing bounded input allowance. A provider must actually support the
required structured outputs, image inputs when needed, and trustworthy token
usage; being listed does not certify those capabilities. Local correctness still
requires the unchanged domain/evidence checks.

The administrator selects provider, optional compatible API base URL, key and model in
Einstellungen. A nullable administrator request timeout is persisted with that
endpoint. Unset compatible endpoints use 180 seconds; unset OpenAI and Anthropic
retain the existing function-specific defaults. An explicit whole-second value
from 1 to 3600 applies to extraction and Q&A. The form resets it when the provider
or endpoint changes. This requires no operator environment override or restart. For Q&A it is an upper
bound: the actual persisted reservation expiry also bounds the whole async provider
wait, with a ten-second finalization margin. Sampling the database clock before
query completion includes setup/commit elapsed time. Strict attestation remains
unchanged; the application never extends the reviewed policy to fit a longer
endpoint timeout. Timeout finalization retains conservative accounting.
Changing provider or endpoint clears the saved credential unless a fresh
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

## Evaluation boundary

Offline wire/security tests and synthetic browser checks establish transport,
settings and display behavior. They do not certify model quality. Actual import
observations, comparisons, untouched captures and remaining model acceptance live
in [#399](https://github.com/itskeKs/verwalino/issues/399), including visual-input
capability, default timing and property Q&A. No model is promoted from mocks or a
provider listing, and no automatic fallback or response remapping is introduced.

The existing import read response separates durable pass-reported tokens from
conservative ledger totals. Complete reports, partial reports, reservation-only
counters and unknown usage have explicit display states; terminal success alone
never establishes measured usage. Reported schema-failure usage remains visible.
Elapsed time and nullable estimated USD cost are retained. Historical price classification uses that
run's stored provider class and model, never the organization's current settings.
A compatible endpoint remains unpriced even if its model reuses an OpenAI name.
The UI says `kein Preis bekannt` and does not turn the ledger's zero placeholder
into a measured/free price. This presentation does not change reservation,
billing or evaluation accounting.

## Later

- Broader provider-specific price/capability registries: add only with documented
  tariffs and actual evaluation evidence; unknown models remain visibly unpriced.
- Additional live Anthropic/provider matrices: resume after the owner's local-first
  milestone, without treating compatible-Qwen results as certification.
- Model/vision investigation and additional acceptance: tracked in #399; no further
  model tuning or generations belong to this transport milestone.

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
