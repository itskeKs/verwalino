"""Opt-in error tracking for worker processes: one scrubbed event per failed job."""

import atexit
from contextlib import contextmanager

import sentry_sdk

from app.core.config import settings
from app.core.error_tracking import configure_error_tracking, release_id
from app.core.request_id import correlation_context


def configure_worker_error_tracking():
    """Initialize the shared tracker; without a DSN nothing is initialized."""
    client = configure_error_tracking(settings, release=release_id())
    if client is not None:
        # The SDK's own exit hook is disabled; deliver a queued event on shutdown.
        atexit.register(client.close)
    return client


def _without_exception_text(event, hint):
    # Parsers and providers can attach document fragments to exception messages;
    # a job event keeps the exception type and stack only.
    for exception in event.get("exception", {}).get("values", []):
        exception["value"] = ""
    return event


@contextmanager
def tracked_job(worker: str, job_id):
    """Correlate a claimed job and report the exception that fails it, once.

    An exception leaving the block is reported and re-raised. A failure the job
    absorbs calls report_job_failure; the SDK drops a second report of the same
    exception. The tags are the worker name and the durable job ID, never data.
    """
    with correlation_context(job_id), sentry_sdk.new_scope() as scope:
        scope.set_tag("worker", worker)
        scope.set_tag("job_id", str(job_id))
        scope.add_event_processor(_without_exception_text)
        try:
            yield
        except Exception as error:
            sentry_sdk.capture_exception(error)
            raise


def report_job_failure(error: Exception) -> None:
    """Report a failure the job records itself; call inside tracked_job."""
    sentry_sdk.capture_exception(error)
