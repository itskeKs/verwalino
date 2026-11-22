"""Which worker process of an installation claims waiting invoices (issue #814).

One installation has exactly one process that claims its invoice queue: no waiting invoice is claimed by two
processes, and none waits for a process that does not exist. Which one it is, is a fact of the deployment: each service
states it through its own value of ``HV_INVOICE_EXTRACTION_ENABLED`` (the one operator switch, no second key), and this
module is the single rule that turns the worker kind and that value into "claims invoices here". The worker loops and
the Compose contract tests read it, so a stack file cannot say one thing and a worker do another.

* A stack with a dedicated invoice worker (``docker-compose.prod.yml``: ``invoice-worker``) switches the value off on
  its declaration worker, which then claims no invoice.
* A stack without one (the portable stack, staging and the hosted server) leaves it on for the declaration worker,
  the one process that already reaches the organization's provider; that worker claims invoices in its loop.
* The inbox worker (the workflow runner) is never one: its default stages leave invoices out and Compose switches the
  value off there as well.
* The API process reads the value only to know whether the installation processes invoices at all (the settings card,
  the confirmation text, the queue admission), so it is on exactly where one worker claims.
"""

# The worker kinds (``HV_WORKER_KIND``, the module ``docker/worker-entrypoint.sh`` starts) whose loop claims invoices.
INVOICE_CLAIMING_KINDS = frozenset({"invoice_extraction", "declaration_import"})


def claims_invoices(worker_kind: str, *, enabled: bool) -> bool:
    """Whether a worker process of this kind claims waiting invoices, given its own ``HV_INVOICE_EXTRACTION_ENABLED``."""
    return enabled and worker_kind in INVOICE_CLAIMING_KINDS
