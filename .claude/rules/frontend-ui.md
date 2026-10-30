---
description: German UI formats, existing React components and browser-verified changes
paths:
  - "frontend/**"
---

# Frontend UI

- Write visible copy and errors in German. Money and date formats: follow
  DESIGN.md (Copy and formats); existing deviations are known debt tracked in #48.
- Reuse `frontend/src/components/ui/`, existing page patterns,
  `frontend/src/index.css` and `frontend/src/lib/utils.ts` before adding styles
  or components. [DESIGN.md](../../DESIGN.md) is the frontend design contract: tokens, layout,
  components, states, copy and formats.
- Report real outcomes; no placeholder success or "everything OK" for an empty
  result. Preserve useful inputs when a request fails.
- Check long German names, four-digit amounts, many rows and narrow screens.
  Keep labels, keyboard access and error feedback usable.
- Never blind: before the PR, run the stack (`scripts/dev-local.sh`: API on :18000, Vite on
  :13000; it migrates the shared test database to this branch's head and creates an admin, so
  not while a backend test run is active) and open every changed screen in a real browser at
  1280 px and 360 px with synthetic records you create there. Put before/after screenshots in
  the PR body, taken on the test database and never on production. A screen listed in
  DESIGN.md's screenshot table refreshes its files in `docs/assets/design/`.
- Put interaction regressions in the area's existing `*.test.tsx` file and run
  it with `cd frontend && npm test -- src/pages/X.test.tsx` (use the actual file).
