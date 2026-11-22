# ADR 0015: The import writes the importing administrator into actor-bound insert columns

- Status: accepted for working data (tasks); [#694](https://github.com/itskeKs/verwalino/issues/694) and [#716](https://github.com/itskeKs/verwalino/issues/716) reuse it only under the conditions below
- Date: 2026-11-19
- Issue: [#691](https://github.com/itskeKs/verwalino/issues/691) (the [owner comment](https://github.com/itskeKs/verwalino/issues/691#issuecomment-6915993273) makes the first of #691, #694 and #716 decide the mechanism)

## Context

The import inserts the archived rows as the importing administrator, under the ordinary
runtime row-level security and integrity guards (no policy is relaxed for it). Some insert
rules bind a column to the signed-in user: `task_insert` requires
`created_by = app.current_user_id`, and `task_parent_guard` accepts an assignee only if that
user is an active member. Other cases of the same shape are `person.created_by`, the retry
records of #689, the preparer of a dunning context (#694) and the reviewer of a scope binding
(#716).

After an import the archived users are inactive placeholders without credentials, except the
one whose sign-in address the importing administrator carries. So the archived actor of a row
is usually not the signed-in user, and never an active assignee. An organization export that
holds a task created in the UI (even by the administrator, beside the living source) was
refused with `IMPORT_INTEGRITY_REFUSAL` for entity `task`.

## Decision

1. The importer is the actor of the insert. For a row whose insert rule binds an actor column to
   the signed-in user, the import writes the importing administrator into that column
   (`organization_import_service.py`, as it already did for `person.created_by`). The policy
   holds as written; no policy, grant, migration, `SECURITY DEFINER` function or role switch is
   involved, and normal writes are untouched.
2. A column of the same row that a guard ties to an active user follows it. A task's
   `assigned_user_id` is the importing administrator too; every other field and every link to
   its property, unit or lease is restored unchanged.
3. The original actor is not recorded anywhere new: no column, field or audit payload. It
   stays in the archive file and in the source organization while that exists; the import
   receipt maps each new row to its source row.
4. A row that only records a request (retry records nobody reads) is not written with the
   importer as actor but left out of the import (`NOT_RESTORED_ENTITIES`, #689).

## Consequences

A restored task has the importing administrator as creator and assignee and equals the source
in title, note, due date, status and links. What is lost is who created it and who it was
assigned to; the task list names the importing administrator as the person responsible until
someone reassigns the task. Nothing changes in the export or in the source organization.

## Where the rule does not carry over by itself

Each issue that reuses the rule checks these and says so in its PR body; this ADR does not
decide them.

- The column must record who made a row of working data. A column that attests an act the
  importer did not perform (a reviewer, a preparer: #716, #694) would state something untrue
  about that act; whether the importer may stand there, or another way is needed, is that
  issue's decision. [ADR 0016](0016-import-names-the-restored-dunning-context-for-its-preparer.md)
  decides it for the preparer of a dunning context (#694) and the reviewer of a scope binding
  (#716): the archived preparer or reviewer stays.
- One acting user must satisfy every rule on the row and on its chain. A rule that needs two
  different users (for example a preparer and a separate approver) cannot be met by writing
  the importer into both columns.
- Losing the original actor must be acceptable for the row's purpose. Retained evidence that
  names its actor is not working data.
- Only the actor column changes. The importing administrator still needs every other condition
  of the policy (role, source access, grants).
