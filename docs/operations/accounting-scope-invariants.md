# B1 accounting scope and authorization invariants

Implementation in progress. These invariants guide the staged schema/service and
policy/UI changes; the presence of tables alone does not enable scoped access.

1. Every governed bank account, payment, cost, settlement and plan has exactly
   one canonical, typed `accounting_scope_binding`. The same table serves old and
   new records. There is no nullable new-column versus legacy-sidecar fallback.
   Binding uniqueness, tenant/property FKs and payment-to-account scope FKs are
   database constraints. Bindings are append-only; financial originals keep their
   existing bytes, amounts and immutable columns. A non-null original mandate
   must match the canonical scope. An absent legacy mandate can be classified by
   the reviewed binding without rewriting the original; absence is not contrary
   evidence. New payments derive the exact mandate from their bound bank scope.
2. A financial grant matches an exact scope ID. No property/type/mandate search
   establishes permission to an existing financial resource. WEG, rental and SEV
   scopes on one physical property do not inherit each other's access. Unit sets
   and accounting-effective dates are explicit. These dates constrain the financial
   period (payment value date, cost service period, settlement/plan period), not
   today's access date. Historical reads and preparation of a prior-year correction
   remain possible with a current live grant; grant expiration controls access.
   Future-period preparation is likewise allowed only inside the selected scope's
   accounting dates. Shared master-data visibility is a separate
   relation, never a financial authorization fallback.
3. Automatic migration accepts only proven simple property/type/mandate histories.
   Mixed types, contradictory mandates and incompatible deposit/account use are
   recorded as migration exceptions. They receive no guessed money binding.
   Existing role mappings are review proposals, not active team grants.
4. Existing organizations start in a compatibility state pending explicit scoped
   team activation. New enforced organizations need an atomic administrator
   bootstrap path before governed-resource creation. Activation checks bindings
   and reviewed grants. Once enforced, the organization can pause but cannot
   return to broad legacy authorization. Rollback pauses writers and preserves
   restricted reads. Organization admin setup authority must remain distinct from
   a financial approve/send grant.
5. Requests set server-authenticated actor identity, not a cached grant list. The
   database policy queries current memberships, active exact-scope grants and
   expiration/revocation. Read-only roles cannot acquire mutation capability from
   a grant. Unknown resource paths fail closed; no org-wide staff fallback.
6. Governed-resource creation and its canonical binding commit together. The
   chosen scope is validated for the actor, property, mandate, units and accounting
   type. A new record cannot be temporarily visible without its binding. Ordinary
   source intake needs a persistent creator-only binding until explicit assignment.
7. Financial effects hold the organization ACL shared transaction lock; grant
   revocation/activation uses the exclusive lock. Queued work stores initiator,
   selected scope and ACL revision. Workers recheck every source/target scope and
   current grants under the shared lock immediately before durable effects;
   revoked work pauses for reassignment. Extraction outside a transaction never
   carries authorization forward to a later commit unchecked.
8. Pages use deterministic cursors and bounded database queries. Adding page
   endpoints does not change existing list response shapes without migrating
   their consumers. Details, searches, source downloads, exports, bulk actions
   and background jobs share the same access boundary.

The independent architecture review identified dual-binding ambiguity, implicit
scope inheritance, bootstrap deadlock and cached revocation as risks. The rules
above incorporate those findings. V05/V06 remain release decisions; no scoped
team rollout or professional authorization claim follows from this design note.
