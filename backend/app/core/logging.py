"""One logging configuration for the API, Uvicorn and worker processes."""

import logging
import copy

from app.core.request_id import REPORTED_ERROR_ATTRIBUTE, request_id
from app.core.scrub import MAX_SCRUB_CHARS, scrub_text


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
                record.exc_text = super().formatException(record.exc_info)
                record.exc_info = None
            if record.exc_text:
                text = record.exc_text.rstrip("\n")
                truncated = len(text) > MAX_SCRUB_CHARS
                if truncated:
                    # Keep complete trailing lines so clipping cannot strip a
                    # DETAIL/password prefix from a retained private value.
                    cutoff = text.find("\n", len(text) - MAX_SCRUB_CHARS)
                    if cutoff == -1:
                        # An overlong final line keeps its type/secret labels;
                        # scrub_text bounds and filters it from the beginning.
                        cutoff = text.rfind("\n")
                    text = text[cutoff + 1:]
                record.exc_text = ("[truncated]\n" if truncated else "") + scrub_text(text, error_context=True)
            if record.stack_info:
                record.stack_info = scrub_text(record.stack_info, error_context=True)
        return super().format(record)


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
    server_logger = logging.getLogger("uvicorn.error")
    if not any(isinstance(item, _ReportedRequestErrorFilter) for item in server_logger.filters):
        server_logger.addFilter(_ReportedRequestErrorFilter())
    for name in ("weasyprint", "fontTools"):
        logging.getLogger(name).setLevel(logging.WARNING)
    for name in ("", "uvicorn", "uvicorn.error", "uvicorn.access"):
        for handler in logging.getLogger(name).handlers:
            handler.setFormatter(RequestFormatter())
