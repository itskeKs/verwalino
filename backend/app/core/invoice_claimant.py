"""Which worker process of an installation runs which model work (issues #814 and #501).

Two kinds of work reach the organization's provider: reading and extracting an invoice, and reading and extracting an
unstructured document of the inbox. Each has exactly one process per installation that does it: no waiting item is
claimed by two processes, and none waits for a process that does not exist. This module is the single rule that turns
the worker kind (and, for invoices, the operator's one switch) into "does this work here". The worker loops and the
Compose contract tests read it, so a stack file cannot say one thing and a worker do another.

Invoices (``HV_INVOICE_EXTRACTION_ENABLED`` is the one operator switch, there is no second key):

* A stack with a dedicated invoice worker (``docker-compose.prod.yml``: ``invoice-worker``) switches the value off on
  its declaration worker, which then claims no invoice.
* A stack without one (the portable stack, staging and the hosted server) leaves it on for the declaration worker,
  the one process that already reaches the organization's provider; that worker claims invoices in its loop.
* The inbox worker (the workflow runner) is never one: its default stages leave invoices out and Compose switches the
  value off there as well.
* The API process reads the value only to know whether the installation processes invoices at all (the settings card,
  the confirmation text, the queue admission), so it is on exactly where one worker claims.

The documents of the inbox (owner decision of 2026-11-22 on #501): in every stack the declaration worker runs the
inbox's extraction stage, the one that reads an unstructured original with the organization's models, and only it. It
needs no switch of its own: the declaration worker is the process that holds the organization's session secret and the
route to its provider in every stack (production included, where the invoice worker is a second such process), and
the settings card names the inbox in every installation. The inbox worker keeps file inspection, the malware gate,
the delivery of the documents and the rest of what it does, and has neither the secret nor a route to a provider, so
it never runs this stage.
"""

# The worker kinds (``HV_WORKER_KIND``, the module ``docker/worker-entrypoint.sh`` starts) whose loop claims invoices.
INVOICE_CLAIMING_KINDS = frozenset({"invoice_extraction", "declaration_import"})

# The worker kinds whose loop runs the extraction stage of the inbox, including the models.
INBOX_EXTRACTION_KINDS = frozenset({"declaration_import"})


def claims_invoices(worker_kind: str, *, enabled: bool) -> bool:
    """Whether a worker process of this kind claims waiting invoices, given its own ``HV_INVOICE_EXTRACTION_ENABLED``."""
    return enabled and worker_kind in INVOICE_CLAIMING_KINDS


def runs_inbox_extraction(worker_kind: str) -> bool:
    """Whether a worker process of this kind runs the inbox's extraction stage (its structured parse and its models)."""
    return worker_kind in INBOX_EXTRACTION_KINDS
