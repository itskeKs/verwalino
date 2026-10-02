# B2 quarantine inspection primitive

Status: locally verified primitive; not wired into upload routes or workers.
Migration head remains 0092. B2 acceptance is outstanding.

The new worker-facing `run_isolated_inspection` inspects exact original bytes in a
separate process before any preview, OCR or field extraction. Its result carries
SHA-256, byte count, normalized MIME, inspector version and verified page count
where applicable. Results are inspection facts, not source-access or confirmation
authority. Future intake completion must bind them to its fenced attempt and
immutable original hash before releasing quarantined work.

The bounded envelope is 25 MiB, 200 PDF pages, 20 million pixels at 200 dpi for
PDF pages or native image dimensions, one image frame, 64 XML levels, 100,000 XML
elements and one million XML text characters. XML DTDs/entities are rejected;
parsing does not resolve external resources. JPEG/PNG validation decodes pixels
inside the child after checking dimensions. PDF inspection checks structure and
page geometry, not field extraction or full rendering. Encrypted files are rejected.

ClamAV runs first. Only exit zero permits structural parsing; detections reject,
and missing scanner, error or timeout remain blocked. File/container limits use
`--alert-exceeds-max=yes` so skipped oversized content cannot be treated as clean;
see the [upstream ClamAV scanner manual](https://github.com/Cisco-Talos/clamav/blob/main/docs/man/clamscan.1.in).
Scanner output is discarded, avoiding document-derived operational logs.
The scanner gets a 60-second deadline and the entire group a 90-second deadline,
also armed inside the child so it survives a worker crash. The Linux child has
an inherited 1 GiB address-space cap. Environment forwarding is restricted to
PATH, locale and temporary-directory settings. Parent cleanup kills remaining
group descendants and validates the full result schema and exact byte identity.

This host has no `clamscan`. Tests with a scanner stub exercise only the protocol
and real structural parser; they are not malware detection evidence. Deployment
requires a supported real scanner with maintained signatures and a viability test
under the Linux resource cap; no scanner/software has been installed here. An
unavailable scanner must remain an actionable blocked item, never a bypass.

The primitive currently changes no existing invoice/declaration/mandate behavior.
Durable quarantine storage, batch/item contracts, jobs/outbox, retries, page children,
extraction revisions and inbox UI are separate remaining B2 implementation work.

Verification (2026-10-03): the initial test failed because the new module did not
exist. The first implementation passed 13 tests and failed the encrypted-PDF
error distinction; the wrapped parser exception now maps to `encrypted_pdf`.
Independent review identified JPEG scan-data validation and malformed IPC gaps;
both were repaired, including error/status consistency. The final focused suite
passed all 27 checks (`/tmp/hv-intake-inspection-status.log`). It covers scanner
outcome ordering, MIME/size limits, corrupt/encrypted/overlong PDF, bounded XML,
PNG/JPEG, truncated JPEG, secret-free child environment, explicit unavailable and
timeout outcomes, malformed IPC and real structural parsing with a scanner stub.
No existing upload route or database schema changed, so prior registry evidence
remains applicable. Real scanner operation remains the prerequisite stated above.
