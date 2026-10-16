# Reviewed visual extracts for scoped disclosure

## Foundation and limits

The current allocation adapter publishes a whole original to one accounting scope. Reviewed logical page partitions do not authorize publication of the remaining original or its embedded files. A scoped extract must be a new derivative with exact provenance, explicit review of the resulting bytes and separate destination authority. It must never be represented as the original electronic invoice or preserve its digital signature claim.

The first accepted increment is an unconnected, bounded local PDF renderer in `backend/app/core/visual_extract.py`. It binds input SHA-256, previously inspected page count and one exact selected page range. Selected CropBoxes become pixels in a fresh PDF containing only images and generic page-provenance captions. Visible annotation appearances are rendered, but source PDF objects, hidden text, metadata, annotation actions, forms and attachments are not copied. The derivative deliberately loses searchable text and embedded electronic-invoice data; the original remains private and unchanged. Image/XML source support and visual redaction are outside this first renderer.

Run all document parsing/rendering in a killable child process with an environment allowlist. Bound source bytes/pages, selected pages, dimensions, aggregate pixels, output bytes and rendering time. Use one page image at a time. Failure must return no partial PDF. A successful render is neither malware clearance, financial evidence acceptance, publication nor an access grant.

The subsequent durable adapter must require exact current clean inspection, partition membership and original custody/export authority, retain source/derivative digests and renderer profile, and bind deliberate disclosure review to the exact derived bytes and destination. Publication must recheck all required destination grants. Downloading an extract must not disclose source storage paths, other page ranges, private review notes or grant original access. Existing whole-original adoption remains unchanged until scoped monetary/source identity and duplicate prevention are independently qualified.

## Renderer qualification

`backend/tests/test_visual_extract.py`: 31 passed (`/tmp/hv-visual-extract-protocol.log`). Actual Poppler 26.04.0 renders prove selected page pixels, CropBox offsets, all four right-angle rotations and visible annotation appearances. Fixtures verify hidden text, private metadata, embedded XML and source actions are absent; unsupported geometry, input bindings, pixel/output limits, deadlines, credential filtering, killed-child scratch cleanup and twelve malformed IPC variants fail closed.

An independent static review identified orphan child scratch on forced termination and permissive IPC validation. Both were repaired and the 31-case suite includes regression coverage; the bounded follow-up found no remaining blocker in those repairs. Initial test invocation without the disposable DB configuration failed the test harness guard. The initial file named `red` actually passed and is not red-phase evidence.

This is process isolation with resource limits, not an operating-system security sandbox. Linux address-space limits are implemented but the actual renderer qualification ran on macOS. Selected visible pages may themselves contain unrelated private material and still require deliberate disclosure review. No API, database record, access grant or financial effect is added by this increment. Full B3 and M-B remain incomplete.

## Private durable artifacts — migration 0117

The next accepted increment stores private visual artifacts. Generation requires live original custody and export authority, a current clean inspection, the current reviewed partition and exact membership of the selected logical document. The interactive transaction retains ACL and financial-input locks while the bounded child renderer runs. Stored bytes are read back and verified before a separate trusted review connection records a private attestation. Final insertion rechecks the basis and binds the attestation to the exact actor, command digest, backend process and transaction. Failures never create a completed artifact; separately committed attestations and unreferenced content-addressed bytes can remain private after an interrupted final insertion.

Artifacts and attestations are immutable and included in the access coverage manifest. Runtime roles have no direct table access. Private history uses signed bounded cursors; every download rechecks original authority and verifies all output bytes before returning headers. The renderer profile, source digest, selected range, inspection page count, output digest and storage identity remain pinned. Public API metadata excludes storage locators. Historical exact retries return the retained artifact after current authority checks even if the partition advanced; new commands require the current partition. No new access to the original is granted.

The source page now supports deliberate range selection, a preparation reason, uncertain-command retry and retained private history. Artifact export denial clears only artifact details; it does not falsely claim that source read access was revoked. Parent refresh reauthorizes retained history. An explicit label association was required by actual browser qualification.

Verification receipts:

- Six actual-render API cases passed in `/tmp/hv-visual-artifact-boundaries-fixed.log`: source binding, historical replay, changed command, concurrency, pagination, current membership, cross-tenant/custodian denial, revoked export, corrupt source/output, rollback and storage readback.
- Six actor/process/transaction/digest/source/range mismatch cases passed in `/tmp/hv-visual-artifact-proof-migration.log`; its separate migration fixture failed and is superseded by the next receipt.
- The final private-role, immutable retention, manifest and empty migration roundtrip case passed in `/tmp/hv-visual-artifact-migration-qualified.log`. Twenty-two updated historical migration cases passed in `/tmp/hv-visual-artifact-rollback-regression.log`; the fake deployment/head probe passed for 0117 without a deployment.
- Fifteen frontend cases, build and lint passed in `/tmp/hv-visual-artifact-{ui,build,lint}-final.log`. The existing bundle size warning remains (approximately 844 kB).
- Actual browser receipt `/tmp/hv-visual-artifact-browser.log` proves lost-201 exact retry after refresh, one durable artifact, verified downloaded hash, private history after reload and local denial handling. `/tmp/hv-visual-artifact-mobile.log` qualifies the mobile history after the sidebar transition; both final screenshots were inspected.

Independent review found a nullable raw history limit and a confusion between artifact-export denial and source-read denial. Both are fixed and have regression coverage; bounded follow-up found no remaining blocker in those branches. Earlier test setup failures involved missing/replaced export grants, retained private attestations during disposable database reset and a migration fixture holding its own transaction open. The fixture was repaired; production retention guards were preserved. The first browser attempt used the wrong Vite working directory; a later browser finding led to the explicit label fix.

This increment remains private preparation. It does not review disclosure, publish to a destination, accept a financial source, split monetary identity or adopt costs. Reviewed destination disclosure and the remaining B3/M-B requirements are still outstanding.
