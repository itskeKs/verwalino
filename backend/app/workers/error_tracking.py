"""Opt-in error tracking for worker processes: at most one scrubbed event per job."""

import atexit
from contextlib import contextmanager
from contextvars import ContextVar

import sentry_sdk

from app.core.config import settings
from app.core.error_tracking import configure_error_tracking, release_id
from app.core.request_id import correlation_context

# Codes a worker records when its isolated processor crashed or returned unusable output.
PROCESSOR_FAILURE_CODES = frozenset({"processor_failed", "inspector_failed"})

# Set by tracked_job; the first failure of a job is the cause, later ones its consequences.
_job_state: ContextVar[dict | None] = ContextVar("tracked_job_state", default=None)


class JobFailed(Exception):
    """A job the worker recorded as failed without an exception of its own."""


def configure_worker_error_tracking():
    """Initialize the shared tracker; without a DSN nothing is initialized."""
    client = configure_error_tracking(settings, release=release_id())
    if client is not None:
        # The SDK's own exit hook is disabled; deliver a queued event on shutdown.
        atexit.register(client.close)
    return client


def _without_free_text(event, hint):
    # Parsers and providers can attach document fragments to exception messages and
    # libraries log arbitrary text; a job event keeps the exception type and stack only.
    for exception in event.get("exception", {}).get("values", []):
        exception["value"] = ""
    event.pop("breadcrumbs", None)
    return event


@contextmanager
def tracked_job(worker: str, job_id):
    """Correlate a claimed job and report the exception that fails it, once.

    An exception leaving the block is reported and re-raised. A failure the job
    records itself goes through report_job_failure or report_recorded_failure. Only
    the first report of a job is sent. Tags are the worker name and the durable
    job ID, plus the recorded failure code; never job data.
    """
    token = _job_state.set({"reported": False})
    try:
        with correlation_context(job_id), sentry_sdk.new_scope() as scope:
            scope.set_tag("worker", worker)
            scope.set_tag("job_id", str(job_id))
            scope.add_event_processor(_without_free_text)
            try:
                yield
            except Exception as error:
                report_job_failure(error)
                raise
    finally:
        _job_state.reset(token)


def report_job_failure(error: Exception, *, code: str | None = None) -> None:
    """Report the failure that ends the current job; a no-op outside tracked_job."""
    state = _job_state.get()
    if state is None or state["reported"]:
        return
    state["reported"] = True
    if code is not None:
        sentry_sdk.get_current_scope().set_tag("error_code", code)
    sentry_sdk.capture_exception(error)


def report_recorded_failure(code: str | None, error: Exception | None = None) -> None:
    """Report a job recorded with a processor failure code, with its exception if any."""
    if code in PROCESSOR_FAILURE_CODES:
        report_job_failure(error if error is not None else JobFailed(code), code=code)
