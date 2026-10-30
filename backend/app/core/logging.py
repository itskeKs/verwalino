"""One logging configuration for the API, Uvicorn and worker processes."""

import logging
import copy
from collections import deque
from datetime import datetime, timezone

from app.core.request_id import REPORTED_ERROR_ATTRIBUTE, request_id
from app.core.scrub import scrub_text


class _ReportedRequestErrorFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # Preserve Uvicorn's connection-abort behavior, but omit the duplicate
        # traceback for an exception our correlated handler already logged.
        error = record.exc_info[1] if record.exc_info else None
        return getattr(error, REPORTED_ERROR_ATTRIBUTE, False) is not True


class RequestFormatter(logging.Formatter):
    def __init__(self):
        super().__init__("%(asctime)s %(levelname)s %(name)s request_id=%(request_id)s %(message)s")

    def format(self, record: logging.LogRecord) -> str:
        record = copy.copy(record)
        if not hasattr(record, "request_id"):
            record.request_id = request_id.get()
        # Uvicorn formats access records synchronously on the event loop.
        # Only error diagnostics are scrubbed, never access records.
        if record.levelno >= logging.ERROR and record.name != "uvicorn.access":
            record.msg = scrub_text(record.getMessage(), error_context=True)
            record.args = ()
            if record.exc_info:
                record.exc_text = scrub_text(super().formatException(record.exc_info), error_context=True)
                record.exc_info = None
            elif record.exc_text:
                record.exc_text = scrub_text(record.exc_text, error_context=True)
            if record.stack_info:
                record.stack_info = scrub_text(record.stack_info, error_context=True)
        return super().format(record)


class DiagnosticsHandler(logging.Handler):
    """Transient structural records only; never retains a LogRecord or its message."""

    def __init__(self):
        super().__init__(logging.WARNING)
        from app.diagnostics import DEFAULT_LOG_LINES
        self.records = deque(maxlen=DEFAULT_LOG_LINES)

    def emit(self, record: logging.LogRecord) -> None:
        from app.core.error_tracking import request_state
        from app.diagnostics import diagnostic_log_lines

        state = request_state.get() or {}
        organization = state.get("organization_id")
        if organization is None:
            return
        stamp = datetime.fromtimestamp(record.created, timezone.utc).strftime("%Y-%m-%d %H:%M:%S,%f")[:-3]
        identity = getattr(record, "request_id", request_id.get())
        exception = type(record.exc_info[1]).__name__ + ":" if record.exc_info and record.exc_info[1] else ""
        # Formatting message/traceback would retain free text unnecessarily.
        lines = diagnostic_log_lines([f"{stamp} {record.levelname} app request_id={identity} {exception}"])
        for line in lines:
            self.records.append((str(organization), line))

    def for_organization(self, organization) -> list[str]:
        self.acquire()
        try:
            return [line for owner, line in self.records if owner == str(organization)]
        finally:
            self.release()


def recent_diagnostics(organization) -> list[str]:
    for handler in logging.getLogger().handlers:
        if isinstance(handler, DiagnosticsHandler):
            return handler.for_organization(organization)
    return []


def configure_logging() -> None:
    """Keep existing handlers, including Uvicorn's non-propagating handlers."""
    factory = logging.getLogRecordFactory()
    if not getattr(factory, "_request_id_factory", False):
        def correlated_record(*args, **kwargs):
            record = factory(*args, **kwargs)
            record.request_id = request_id.get()
            return record
        correlated_record._request_id_factory = True
        logging.setLogRecordFactory(correlated_record)
    logging.basicConfig(level=logging.INFO)
    root = logging.getLogger()
    if not any(isinstance(handler, DiagnosticsHandler) for handler in root.handlers):
        root.addHandler(DiagnosticsHandler())
    server_logger = logging.getLogger("uvicorn.error")
    if not any(isinstance(item, _ReportedRequestErrorFilter) for item in server_logger.filters):
        server_logger.addFilter(_ReportedRequestErrorFilter())
    for name in ("weasyprint", "fontTools"):
        logging.getLogger(name).setLevel(logging.WARNING)
    for name in ("", "uvicorn", "uvicorn.error", "uvicorn.access"):
        for handler in logging.getLogger(name).handlers:
            handler.setFormatter(RequestFormatter())
