# Physical source identity across visual derivatives

Status: migration 0120 implemented and locally qualified. Partial-page financial adoption remains disabled.

A retained visual derivative has different bytes from its source. Uploading those bytes again must not make an already consumed source available for a second financial effect. Migration 0120 recognizes the exact provenance already retained by the trusted renderer, without changing existing canonical hashes, claims, financial amounts or disclosure grants.

The private identity resolver preserves the submitted hash and inclusive page range, then follows every recorded output-to-source relationship. Whole-derivative identity maps to the exact parent interval. Nested derivatives translate coordinates at each hop; sibling outputs are compared through common ancestors. Multiple proven origins for identical output bytes are retained conservatively. Disjoint ranges remain distinct. Invalid coordinates, more than 64 identities or more than 256 examined provenance records produce a generic conflict. An organization/hash index bounds each lookup, and the sorted result is stable for preview fingerprints.

The permanent-claim overlap check expands both the requested source and candidate roots. Its candidate filter includes matching ancestor hashes and all roots whose hashes are themselves known renderer outputs. Missing claims still fall back to retained allocation roots. Live source grants never filter this private conflict search. Runtime roles cannot call the resolver or read its evidence directly; financial previews expose only the existing generic blocker.

Both adoption and correction previews include the resolved identity in their private fingerprint. New renderer provenance can therefore invalidate a previously reviewed preview. Renderer creation and financial admission use the same organization financial lock, preventing a new relationship from appearing between admission and commit.

Direct cost confirmation and invoice release use the same physical-consumption guard, including proven legacy sources. A legacy correction may continue only its exact paired invoice-version and cost-successor chain, within the same property and accounting scope. The bounded chain must identify one legacy root. This exemption does not cover another root sharing the same bytes. Released descendant hashes remain consumed even when correction bytes change. Existing confirmed-cost successor links become immutable once set, so clearing an ancestor cannot erase descendant consumption. A draft cannot change property during confirmation and retain an exemption calculated from its old context. Existing released-row status transitions remain available, and reviewed allocations still require their complete atomic correction command.

The migration takes authorization and financial locks before installing the guards. Downgrade restores the previous function definitions only when retained allocations and visual artifacts do not coexist; populated protection must be repaired forward. Historical migration tests discard synthetic evidence inside savepoints before exercising older boundaries.

## Qualification

- Real rendering, exact derivative re-upload with different invoice fields, and rejection without additional financial costs.
- Nested coordinate translation, overlapping and disjoint sibling ranges, multiple provenance alternatives, invalid geometry, cycles, both traversal budgets, tenant separation and private-role denial.
- Preview staleness after new provenance, common-ancestor claim matching, exact root exclusion and missing-claim fallback.
- Raw confirmation and release guards, legacy-only claims, two ordinary legacy correction generations with identical and changed bytes, immutable successor pointers, and rejection of an unrelated copy of the latest financial source.
- Unclassified preparation-mode property change and confirmation in one statement; the unchanged-property continuation remains valid.
- Migration roundtrip and populated rollback refusal, existing allocation/correction tests and ordinary receipt-flow regression.

This foundation does not accept B3 or M-B. The visual financial adapter still needs coordinated source seals, page claims, duplicate and exception semantics, retained-source corrections, API/UI work and end-to-end qualification before it can be enabled.

Final evidence: 99 source-alias, permanent-claim, adoption, correction and ordinary receipt-flow tests passed (`/tmp/hv-allocation-source-aliases-final.log`). All 21 selected historical migration checks passed, with 224 unrelated tests deselected (`/tmp/hv-allocation-source-aliases-migrations.log`). The isolated fake deployment probe passed at head 0120 (`/tmp/hv-allocation-source-aliases-deploy-probe-v2.log`); it does not perform a deployment. No frontend behavior changed, so the qualified 0118 browser/UI evidence is reused for those unchanged flows.

Earlier failing runs are not acceptance evidence: synthetic fixture parameter types and cost status were corrected, historical rollback fixtures now remove retained synthetic renderer evidence, and the first fake probe inherited an ambient provider setting; its accepted rerun explicitly excluded that setting. Independent review identified and drove the legacy-only confirmation guard, released-row transition compatibility, NEW-property check and immutable successor protection.
