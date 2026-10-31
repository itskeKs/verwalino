---
description: Backend layers, implementation order and resource weight
paths:
  - "backend/app/**"
---

# Backend architecture

- Dependency direction: `api` → `services` → `repositories` → `models`. `engine` imports only
  `engine`, `core` only `core`; `declarations`, `workers` and `evals` sit beside `services` and
  use the same inner layers. Only `main.py` imports `api`. Known exceptions are debt tracked in
  #80, not licence: raw `text()` SQL in 14 api modules and `select()` in 7, the SDK client built
  in `api/property_knowledge.py`, the cycle `repositories` ↔ `declarations`, and
  `workers/invoice_extraction.py` importing a service.
- Routes are thin: validate, resolve the organization context, call a service or repository,
  map the result. New data access goes into a repository.
- Red first, then inside out: write the failing `features/` scenario and the unit tests, then
  implement migration → model → repository → service → route and schema →
  `frontend/src/api/types.ts` and `endpoints.ts` → page, each layer's own test green before the
  next; the scenario turns green last.
- Weight: one 12 GB VPS is meant to serve 30–50 organizations; the app container has 768 MiB and
  one CPU (`WEB_CONCURRENCY=2`). No new heavy runtime dependency (ML frameworks, headless
  browsers, JVMs) and no new process without an issue that decides it. Long or CPU-heavy work
  (OCR, batch PDF rendering, imports, model calls) runs from a database queue in a worker that
  Compose starts (`docker-compose.prod.yml`: `worker`, `invoice-worker`), bounded by size, page
  and time limits, never in the request path.
- The self-host compose (`infra/docker-compose.yml`) starts no worker yet, and a worker no
  Compose service starts is not operated: wire it or say so in `docs/feature-status.md`.
- Money is `Decimal`/`NUMERIC`, never float; time ranges follow `docs/adr/0006-valid-to-convention.md`;
  the stack decision is `docs/adr/0002-backend-stack-fastapi.md`.
