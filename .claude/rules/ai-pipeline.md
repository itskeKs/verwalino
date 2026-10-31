---
description: Provider boundary, usage accounting and bounded OCR for AI-assisted imports
paths:
  - "backend/app/declarations/**"
  - "backend/app/workers/**"
  - "backend/app/evals/**"
  - "backend/app/core/runtime_budget.py"
  - "backend/app/core/invoice_ocr.py"
  - "backend/app/core/pdf_extraction.py"
  - "backend/app/core/visual_extract.py"
  - "backend/app/core/intake_extraction.py"
  - "backend/app/core/extraction.py"
---

# AI pipeline and OCR

- Every model call goes through a provider protocol: `DeclarationExtractionProvider` in
  `backend/app/declarations/provider.py` for imports, `KnowledgeAnswerProvider` in
  `declarations/knowledge_provider.py` for property questions. Do not import the OpenAI SDK
  elsewhere; `api/property_knowledge.py` and `workers/declaration_provider_smoke.py` are
  existing debt (#80).
- Reservation, usage and cost accounting live in `declarations/pipeline.py` (`add_usage`,
  `settle_reservation`, `charge_reservation`) with the per-run cap `declaration_max_cost_usd`;
  `core/runtime_budget.py` holds the QA policy checks. The pipeline fails closed when a cap or
  policy check fails. A per-organization budget is #22: keep the accounting attributable to the
  organization so it becomes possible.
- Tests use `declarations/fake_provider.py` and `fake_knowledge_provider.py`; no live key, no
  network. Live evaluations are opt-in gates (`HV_DECLARATION_REAL_PDF_MANIFEST`,
  `HV_DECLARATION_RUNTIME_IMAGE`) and never run in CI.
- A change to prompts, schemas or the harness bumps the matching version: the settings
  `declaration_prompt_version`, `declaration_schema_version`, `declaration_harness_version`
  (`core/config.py`, defaults mirrored in `.env.example` and `docker-compose.prod.yml`) or the
  knowledge constants `KNOWLEDGE_QA_PROMPT_VERSION` and `KNOWLEDGE_ANSWER_SCHEMA_VERSION`; extend
  `backend/tests/fixtures/declaration_eval_cases.json` for extraction changes. Evidence, review
  and approval records stay reproducible from stored inputs.
- OCR is Tesseract (`deu`) with poppler and qpdf, run as bounded subprocesses (DPI, pixels,
  pages, temp bytes, timeout) in `declarations/preflight.py`, `workers/declaration_import.py`,
  `core/invoice_ocr.py`, `core/pdf_extraction.py`, `core/visual_extract.py` and
  `core/intake_extraction.py`. Keep every new path bounded the same way.
- Documents and extracted text leave the host only to the configured provider inside the
  declaration-worker network boundary (`DEPLOY.md`); respect `HV_DATA_REGION`. Observability
  records (`declarations/observability.py`) and logs carry identifiers, versions, hashes and
  usage, never document text.
- A provider-neutral runtime (#66) and OCR as a separate service (#65) are open owner decisions:
  keep the boundary clean so either becomes possible, and do not pre-empt them.
