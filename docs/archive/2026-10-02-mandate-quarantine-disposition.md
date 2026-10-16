# Administrative review of mandate quarantine

Status: implemented and verified; baseline 7722617, migration 0079.

Expose a bounded administrator-only inventory of the private 0078 observations:
opaque original identity, observation timestamp/digest and disposition status.
Do not include titles, filenames, storage locations, original uploader or bytes.
Default50/max200 UUID keyset pages use signed actor/tenant-bound cursors.

An administrator may append one immutable disposition confirming continued
quarantine, with a closed reason code, current actor, server timestamp and the
exact observed digest. This is an administrative decision about uncertainty, not
source admission or historical authentication. It leaves every existing access
restriction and original intact. PAUSED permits this safe administrative action.
Only an app_rw session with current tenant/admin authority can call the bounded
commands. The private ledger has no runtime table privileges. Requests require a
UUID key; identical current-authorized retries replay the original receipt, changed
bodies/keys cannot replace prior decisions. One transaction records receipt/audit.

Acceptance: tenant and current-admin boundary including direct worker SQL;
no content-bearing metadata; deterministic paging and cursor tamper denial;
immutable original and disposition, stale-digest conflict, lost-response retry,
parallel same-key replay, no activation/access change. Populated downgrade refuses
to erase decisions. UI hides stale failed loads and preserves retry keys.


Evidence: two initial endpoint404 RED tests, then nine focused API/RLS checks
passed. Expanded20 cases cover exact tenant/cursor binding, page limits, stale
snapshot rejection, three parallel same-key requests producing one receipt/audit,
changed body/key conflicts, direct worker/clerk/wrong-tenant SQL denial, live role
revocation/deactivation before replay, private immutable receipts and populated
downgrade refusal. The 0078 historical-cutoff rehearsal now removes the empty0079
ledger inside its rolled-back disposable transaction before testing0078.

Seven final UI tests cover retained retry keys, explicit confirmation, clerk denial,
failed-load recovery and server disposition after a lost response/reload. Independent
review identified one stale local receipt-state risk; the UI now derives the server
receipt before its locally saved response. Final lint/build and shell/fake-Docker
checks passed (existing636KB bundle warning). Chromium verified a response dropped
after the real database commit, identical key/result on retry, reload persistence,
failed refresh hiding stale observations and successful recovery. Desktop/mobile
screenshots were inspected; no page errors. Test servers stopped. No deployment.

Operator entry: Users → Bestand und Freigaben vorbereiten → Mandatsquarantäne
prüfen. Review the opaque observation and select a closed disposition reason;
explicit confirmation records continued quarantine permanently. A successful
review never enables original access. Lost external originals remain unusable;
new independently supplied uploads use the existing scoped mandate journey.
Dunning legacy handling, team coverage, role proposals and reviewed activation
remain subsequent B1 work. Activation remains false.
