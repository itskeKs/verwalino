# ADR 0016: The import names the row it restores, so its guard accepts the archived preparer, reviewer, creator or grantor

- Status: accepted for the preparer of a dunning context, the reviewer of a scope binding, the creator of a contact data domain and the grantor of its grants, and the grantee, the revocation and the creation time of a contact data grant
- Date: 2026-11-20 (reviewed scope bindings: 2026-11-22; contact data domains and grants: 2026-11-23; their grantee, revocation and creation time: 2026-11-23)
- Issues: [#694](https://github.com/itskeKs/verwalino/issues/694) (dunning context), [#716](https://github.com/itskeKs/verwalino/issues/716) (scope binding), [#817](https://github.com/itskeKs/verwalino/issues/817) (contact data domain and grant, creator and grantor), [#851](https://github.com/itskeKs/verwalino/issues/851) (contact data grant, grantee, revocation and creation time); first bullet of "Where the rule does not carry over by itself" in [ADR 0015](0015-import-writes-the-importer-into-actor-bound-columns.md)

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

`person_data_domain.created_by` records which administrator prepared a contact's data domain and
`person_data_grant.granted_by` who granted it, and the trigger `protect_person_data_setup` (0068)
requires each to be the signed-in user. A domain or grant that a second administrator made was refused
at import (sqlstate 42501, "Domain actor mismatch", entity `person_data_domain`), and so was every
domain beside the living source organization, where no archived address is the importing
administrator's. The importing administrator neither prepared the domain nor granted it.

`person_data_grant` also records whom a grant is made to, whether it was revoked and when it was made. The same
trigger takes a new grant only unrevoked and only for an active member, and stamps `created_at` with the clock at every
insert. After an import the only active member is the importing administrator, so an ordinary grant to a clerk, a grant
that was revoked again and a grant whose expiry had passed were refused at `person_data_grant` (sqlstate 42501; the
expired grant through the table's `expires_at > created_at`, which the new stamp broke), also when the importing
administrator made them.

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
4. The creator of a contact data domain and the grantor of its grant stay the archived accounts in the
   same way: `created_by` of a restored domain and `granted_by` of a restored grant are the accounts the
   import maps the archived creator and grantor to (the inactive placeholder of a second administrator),
   never the importer. Migration 0201 gives the condition `created_by = signed-in user` of the trigger
   function `protect_person_data_setup()` one alternative, and the condition `granted_by = signed-in
   user` of its grant branch one alternative: the function `import_restores_person_data(kind, row,
   organization, actor)`. The import says so in one transaction-local setting per table,
   `app.organization_import_person_data_domain` (JSON `domain_id` and `created_by`) and
   `app.organization_import_person_data_grant` (JSON `grant_id` and `granted_by`), set around that one
   insert and reset after it; a table's guard reads only its own setting, and only
   `organization_import_service.py` sets either (the same test guards both). The check is a function
   the trigger calls, so that a setting that is malformed or of another shape answers false instead of
   raising a cast error before the trigger can refuse; the runtime role cannot call it, only the
   trigger function, which runs with its owner's rights, does. The trigger stays as written for every
   other write, UPDATE and DELETE included.
5. A contact data grant is restored as the archive has it: its grantee (the account the import maps the archived user
   to, usually an inactive placeholder), rights, reason, expiry and revocation unchanged, and the time it was made.
   Migration 0202 gives three conditions of the grant branch of `protect_person_data_setup()` one alternative each,
   each for the one row the import names, in the setting 0201 introduced: `app.organization_import_person_data_grant`
   now names the grant, its grantor and its grantee (JSON `grant_id`, `granted_by`, `user_id`) and is set for every
   grant, because a grant by the importing administrator may still be a grant to a placeholder, revoked or expired.
   One setting authorises one primary-key row. The function `import_restores_person_data` gets two more kinds:
   - `grantee` replaces "the grantee is an active member" for the named grantee, who must be a placeholder of the
     organization. The placeholder needs no membership row: the import inserts memberships in no fixed order relative
     to grants, and an account that cannot sign in holds no role either way.
   - `history` replaces "the insert is unrevoked" and "`created_at` is stamped with the clock": the grant the setting
     names keeps its `revoked_at` and `revoked_by` and its archived `created_at`. No account is involved; `revoked_by`
     is any account of the organization (the composite foreign key sees to that), because who revoked is history and
     revoking gives nobody a right. This is also where the expired grant was refused: the table's CHECK
     `expires_at > created_at` is right and stays, and holds again as it did in the source once the stamp stops
     moving `created_at`. A grant the import does not name is stamped as before.
   Both kinds check, like the other two, that the setting names this row and that the signed-in user is an active
   administrator of the organization; a missing, empty or malformed setting answers false and never raises. The
   access function `has_person_data_capability` is untouched (see "What a restored grant means").

## What a guard must still check

The setting is a promise of the code, not of a user. A guard that accepts it checks that:
- it names this row and this actor, not merely that an import is running;
- the actor is an account of the same organization that cannot act: inactive with the unusable
  password `!` the import writes (a colleague deactivated in the user list keeps their password);
- every other condition holds for the signed-in importing administrator: for a dunning context the
  organization, selected scope, live `prepare` grant and a context before its notice; for a scope
  binding the organization, a live `prepare` grant on the scope, the origin `REVIEWED` and, in
  addition, that the signed-in user is an active administrator of the organization (the import is
  administrator-only; a clerk's `prepare` grant is not enough); for a contact data domain the
  organization and an active administrator of it, for a grant in addition that its grantor is the signed-in
  user or the placeholder the setting names, and that its grantee is an active member or the placeholder the
  setting names; a revoked grant and the archived `created_at` only for the row the setting names; UPDATE and
  DELETE stay refused.

## What it does not cover

- Scope grants and attachments still take only the importer's own account; each needs its own guard
  under the checks above.
- A grant to a colleague who was deactivated in the user list and keeps their password (no placeholder) is refused, and
  so is a grant to an account of another organization. That is a case of a direct insert, not of an import: before it
  inserts any row the import sets every account the target organization already holds under an archived address, except
  the importing administrator's own, inactive with the unusable password `!` (`organization_import_service.py`,
  `test_relinked_nonadmin_account_is_inactive_and_requires_a_reset`). Such a colleague is a placeholder like any archived
  member, and the archived grants to them are restored to that account, which keeps its own name and address
  (`test_a_grant_to_a_colleague_the_target_already_holds_is_restored_to_that_deactivated_account`). Normal writes are as
  strict as before: a new grant needs an active member as grantee, is inserted unrevoked and is stamped with the clock.
- The restore does not reactivate anybody and writes no password: reactivating a placeholder is an administrator's
  act on the user page ([#851](https://github.com/itskeKs/verwalino/issues/851) leaves it out of scope).
- The trigger writes its own audit event for the restore insert (`person_data.domain_prepared` or
  `person_data.grant_prepared`) and names the signed-in importing administrator as actor of that act. For a grant
  restored as revoked it is the same `person_data.grant_prepared` event, never a `person_data.grant_revoked`. The
  archived events, which keep the archived actors (a revocation included), are restored beside it; no actor column of
  the row is affected.
- A binding of the origin `PROVEN_LEGACY` names the user who reconciled it as `reviewed_by` (0040),
  but the policy takes only `REVIEWED`, so an archive with such a binding is still refused; that is
  unchanged.
- A placeholder is recognised by that convention (inactive, password `!`), not by a dedicated column.
- Code that set a setting could attribute a context, a binding, a domain or a grant to a placeholder,
  never to an active user or another organization, and a binding, a domain or a grant only from an
  administrator's session; the one-writer test is the guard.

## What a restored grant means

The access function `has_person_data_capability` answers for the signed-in user: an active member with a membership
role, a live grant (`revoked_at IS NULL`, `expires_at` absent or in the future) that holds the capability. It is
unchanged, so the grants this ADR restores mean, per case:

- A grant to a placeholder gives no access while the account stays deactivated. The account is inactive, which the
  function reads as no role, and no session of it is accepted; its password is the unusable `!`. An administrator can
  reactivate it on the user page (`PATCH /api/users/{id}` with `is_active: true`, the placeholder has its archived
  membership). Reactivation sets no password and the unusable one still matches nothing, but the account is then
  reachable by whoever holds its sign-in address. An administrator's reset (`POST /api/users/{id}/reset-password`) gives it
  a password. So does the self-service reset ("Passwort vergessen?") where the installation can send mail (SMTP and
  `HV_PUBLIC_HOST` are configured, as on the hosted service, see
  [DEPLOY.md](../../DEPLOY.md#password-recovery-configuration)): it finds an active account by its address, mails the
  link to that address and lets the holder set a password without an administrator. While the account is deactivated the
  page finds no account and sends nothing, and without mail it never issues a link, so there the administrator's reset is
  the only way (tests `test_a_reactivated_placeholder_gets_a_password_from_the_administrators_reset`,
  `test_a_reactivated_placeholder_resets_its_own_password_by_mail_where_mail_is_configured` and
  `test_a_reactivated_placeholder_cannot_reset_its_own_password_without_mail`). A placeholder whose archived address was
  taken carries a synthetic address (`…@imported.archive.invalid`) that no mailbox receives, so only the administrator's
  reset reaches it. Reactivating a placeholder therefore gives the account back, with the rights of its restored live
  grants, to the holder of the archived address; nothing in the import decides who that is. The import reactivates nobody
  and sets no password.
- The rights are those `has_person_data_capability` answers: exactly the restored live grants. What they change for what
  the account reads depends on the organization's source-security mode. In the default mode `PREPARATION` the contact
  policies already admit every contact (`person_source_row_access` answers true, migration 0069), with or without a grant,
  as they did in the source; the grants decide once the organization enforces source security.
- A revoked grant gives no access, before and after a reactivation. An expired grant gives none either.
- The importing administrator gains no access to a contact through the grants of other users: the function asks
  for a grant of the signed-in user. Administrators see the grant rows and the domain (the read policies of both
  tables), as they did in the source.
