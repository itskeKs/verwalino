# Local intake runtime qualification

The intake foundation now has local evidence for durable receipt, inspection
gating, logical sources, extraction history, internal outbox delivery and review
display. This is partial B2 qualification, not a production release acceptance.

| Boundary | Evidence | Scope and limitation |
| --- | --- | --- |
| Receipt and reconnect | 0093/0094 backend and browser evidence in the quarantine/inbox milestone documents | One bounded request per file, exact replay and missing-file recovery; no external upload provider |
| Original access | Real runtime-role/RLS tests across intake, registry and extraction milestones | Clean inspection and current recorded-uploader/scope authority; no financial approval |
| Internal effects | 0097 competing claims, stale fences and transactional rollback tests | One whole-source candidate/effect; no external delivery |
| Located extraction | 0098 processor/worker tests and 0099 rendered proposal view | Original page numbers, explicit unlocated cases and standalone XML paths; injected clean inspection |
| Actual worker process death | `test_intake_process_recovery.py`, one test passed | SIGKILL before result commit and after completion; simulated lease-clock advance; one retained revision/effect and two closed attempts |
| Linux process boundary | Existing local image, current application source mounted read-only | One synthetic text PDF, successful located result and enforced timeout; scanner not present |
| Complete journey and capacity | Pending | Real scanner, selected shared production runtime, mixed supported corpus, measured workload fairness/capacity and restore remain unqualified |

The process-death fixture starts a separate worker with an explicit disposable
database and storage root, excludes ambient credentials and `USER`, and terminates
only that process group. An empty working directory prevents project dotenv
loading; the trusted application path is supplied explicitly. The first death leaves a running durable claim without
a proposal. After the test advances lease expiry, a second process reconciles
the attempt and commits one result. Killing it after commit does not requeue or
repeat the effect. This supplements transaction-rollback and fencing tests; it
does not demonstrate host/database/blob restore or an RPO/RTO.

The Linux probe used the existing `hausverwaltung:local` image with no dependency
installation, current application code, Python 3.12.13 and pdfplumber 0.11.10.
It ran without network access, under UID 10001, read-only root, dropped
capabilities, no-new-privileges, 256 process limit, one CPU, 768 MiB memory and
256 MiB temporary storage. A synthetic text invoice plus a forced timeout took
approximately 0.496 seconds; the child peak RSS was 38,672 KiB. This tiny probe
does not establish batch throughput or professional-capacity budgets. The local
macOS fixtures use a different Python runtime, as recorded in prior milestones.

Real ClamAV/signature qualification remains unavailable: neither the current
Dockerfile nor the existing local image supplies ClamAV, and the existing image
also lacks the newer OCR executables. The production worker still uses the
declaration entry point. Activating the shared intake stages requires a reviewed
runtime/scanner provision and measurements within the deployment budget. No
production configuration, provider, external system or deployment was changed.

Reviewed partitions, contextual assignment, duplicate resolution and cost
allocation are B3 work. They can be developed against the verified local source
and job contracts while the B2 runtime release gate remains explicitly open.
