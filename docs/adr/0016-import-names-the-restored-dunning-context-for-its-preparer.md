# ADR 0016: The import names the dunning context it restores, so its guard accepts the archived preparer

- Status: accepted for the preparer of a dunning context; [#716](https://github.com/itskeKs/verwalino/issues/716) may reuse it under the checks below
- Date: 2026-11-20
- Issue: [#694](https://github.com/itskeKs/verwalino/issues/694); first bullet of "Where the rule does not carry over by itself" in [ADR 0015](0015-import-writes-the-importer-into-actor-bound-columns.md)

## Context

`dunning_scope_context.created_by` records who prepared a notice, and `protect_dunning_context`
(0039) requires it to be the signed-in user. A clerk's notice was refused at import (sqlstate 42501):
its preparer is an inactive placeholder. Writing the importing administrator there, as ADR 0015
does for a task creator, would state that they prepared a notice they did not.

## Decision

1. The preparer stays the archived preparer: `created_by` of a restored context is the account the
   import maps the archived preparer to (the inactive placeholder of a clerk or second administrator),
   never the importer. No column and no second place records a preparer.
2. Migration 0195 gives the condition `created_by = signed-in user` of `protect_dunning_context()`
   one alternative: the import is restoring this very row. The import says so in the
   transaction-local setting `app.organization_import_dunning_context` (JSON `notice_id` and
   `created_by`), set around that one insert and reset after it, like
   `app.organization_import_registrations` (0147). Only `organization_import_service.py` sets it;
   a test fails when the name appears elsewhere under `backend/app`.

## What a guard must still check

The setting is a promise of the code, not of a user. A guard that accepts it checks that:
- it names this row and this actor, not merely that an import is running;
- the actor is an account of the same organization that cannot act: inactive with the unusable
  password `!` the import writes (a colleague deactivated in the user list keeps their password);
- every other condition holds for the signed-in importing administrator: organization, selected
  scope, live `prepare` grant, context before its notice; UPDATE and DELETE stay refused.

## What it does not cover

- Scope grants, reviewed bindings ([#716](https://github.com/itskeKs/verwalino/issues/716)), attachments and contact data domains still take only the importer's own account.
- A placeholder is recognised by that convention (inactive, password `!`), not by a dedicated column.
- Code that set the setting could attribute a context to a placeholder, never to an active user or
  another organization; the one-writer test is the guard.
