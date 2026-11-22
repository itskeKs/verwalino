# ADR 0016: The import names the row it restores, so its guard accepts the archived preparer or reviewer

- Status: accepted for the preparer of a dunning context and for the reviewer of a scope binding; the contact data domain setup ([#817](https://github.com/itskeKs/verwalino/issues/817)) may reuse it under the checks below
- Date: 2026-11-20 (reviewed scope bindings: 2026-11-22)
- Issues: [#694](https://github.com/itskeKs/verwalino/issues/694) (dunning context), [#716](https://github.com/itskeKs/verwalino/issues/716) (scope binding); first bullet of "Where the rule does not carry over by itself" in [ADR 0015](0015-import-writes-the-importer-into-actor-bound-columns.md)

## Context

`dunning_scope_context.created_by` records who prepared a notice, and `protect_dunning_context`
(0039) requires it to be the signed-in user. A clerk's notice was refused at import (sqlstate 42501):
its preparer is an inactive placeholder. Writing the importing administrator there, as ADR 0015
does for a task creator, would state that they prepared a notice they did not.

`accounting_scope_binding.reviewed_by` records who reviewed a classification, and the insert policy
`scope_insert` (0032) requires it to be the signed-in user. A clerk who books a payment on a
classified bank account is its reviewer (the trigger `bind_new_payment_to_account` records the
clerk), and a second administrator may classify an account. An archive that holds such a binding was
refused at import, regardless of row order (sqlstate 42501, entity `accounting_scope_binding`). The
importing administrator did not review the classification either.

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
3. The reviewer of a scope binding stays the archived reviewer in the same way: `reviewed_by` of a
   restored binding is the account the import maps the archived reviewer to (the inactive
   placeholder of a clerk or second administrator), never the importer. Migration 0200 gives the
   condition `reviewed_by = signed-in user` of the policy `scope_insert` exactly one alternative:
   the function `import_restores_scope_binding(id, organization_id, reviewed_by)`. The import says
   so in the transaction-local setting `app.organization_import_scope_binding` (JSON `binding_id`
   and `reviewed_by`), set around that one insert and reset after it, and only
   `organization_import_service.py` sets it (the same test guards it). The check is a policy, not a
   trigger, so a function carries it; it answers false, and never raises, for a setting that is
   missing, empty, malformed or of another shape, so the policy refuses with its own error. The
   policy stays as written for every other write.

## What a guard must still check

The setting is a promise of the code, not of a user. A guard that accepts it checks that:
- it names this row and this actor, not merely that an import is running;
- the actor is an account of the same organization that cannot act: inactive with the unusable
  password `!` the import writes (a colleague deactivated in the user list keeps their password);
- every other condition holds for the signed-in importing administrator: for a dunning context the
  organization, selected scope, live `prepare` grant and a context before its notice; for a scope
  binding the organization, a live `prepare` grant on the scope, the origin `REVIEWED` and, in
  addition, that the signed-in user is an active administrator of the organization (the import is
  administrator-only; a clerk's `prepare` grant is not enough); UPDATE and DELETE stay refused.

## What it does not cover

- Scope grants, attachments and contact data domains still take only the importer's own account. The
  next reuse is the contact data domain setup (`protect_person_data_setup`: `created_by` of a domain
  and `granted_by` of a grant, [#817](https://github.com/itskeKs/verwalino/issues/817)); it is not
  covered here and needs its own guard under the checks above.
- A binding of the origin `PROVEN_LEGACY` names the user who reconciled it as `reviewed_by` (0040),
  but the policy takes only `REVIEWED`, so an archive with such a binding is still refused; that is
  unchanged.
- A placeholder is recognised by that convention (inactive, password `!`), not by a dedicated column.
- Code that set a setting could attribute a context or a binding to a placeholder, never to an active
  user or another organization, and a binding only from an administrator's session; the one-writer
  test is the guard.
