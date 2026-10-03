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
