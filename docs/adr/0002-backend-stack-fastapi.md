# ADR-0002: Backend stack — Python/FastAPI (recommendation)

Status: **Proposed** (confirmation by the human pending — no code/commit yet)
Date: 2026-06-01 · Context: M0 · Reference: [../architecture.md](../architecture.md) Section 6 · SDD §16

## Context

SDD §16 leaves the backend open: Python/FastAPI vs. Node.js/NestJS. The frontend is fixed to React/TypeScript. What determines value and is riskiest is correct, audit-proof settlement with exact decimal arithmetic (roadmap: M3 is the largest/riskiest chunk). Also relevant: RLS/migration handling (I1/I2), XRechnung/ZUGFeRD parsing (M5), HTML→PDF (SDD §16), testability.

## Decision

**Python/FastAPI** for the backend, with **SQLAlchemy + Alembic** for ORM/migrations. The frontend remains React/TypeScript (SDD requirement).

## Rationale (domain-specific)

- **Monetary exactness (I4):** `decimal.Decimal` is a first-class standard type; `NUMERIC ↔ Decimal` is lossless. In Node, `number` is an IEEE-754 float → a decimal library must be enforced permanently and one must ensure that no `number` slips through anywhere for money (more error-prone for a correctness-critical financial product).
- **RLS/migrations (I1/I2):** Alembic versions RLS policies and triggers as explicit, reviewable DDL steps — central to enforcing the invariants.
- **E-invoice (M5):** more mature Python ecosystem for XML/EN-16931 and PDF/A-3 extraction (ZUGFeRD).
- **Testability:** pytest + Hypothesis (property-based testing) fits distribution/rounding logic particularly well.

## Alternative considered

**Node.js/NestJS:** attractive mainly because of *one* language across frontend and backend (shared types/DTOs). Rejected because Python's advantage in money/decimal safety and RLS/migrations outweighs the single-language advantage in this domain. PDF (Playwright/Puppeteer) would be marginally more convenient in Node, but can be integrated regardless of language.

## Consequences

**Positive:** least friction for the correctness-critical settlement; clean RLS/migration handling; strong testing and e-invoice ecosystem.

**Negative:** two languages in the stack (TS frontend, Python backend) → context switching, no shared types out of the box. Knowingly accepted.

**Non-negotiable, regardless of stack:** (1) money as a decimal type, never float; (2) RLS + `FORCE`, app role without `BYPASSRLS`; (3) transaction-local organization context; (4) RLS policies/triggers as explicitly versioned migrations.
