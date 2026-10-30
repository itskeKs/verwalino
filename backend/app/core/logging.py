"""One logging configuration for the API, Uvicorn and worker processes."""

import logging
import copy

from app.core.request_id import request_id
from app.core.scrub import scrub_text


class RequestFormatter(logging.Formatter):
    def __init__(self):
        super().__init__("%(asctime)s %(levelname)s %(name)s request_id=%(request_id)s %(message)s")

    def format(self, record: logging.LogRecord) -> str:
        if not hasattr(record, "request_id"):
            record.request_id = request_id.get()
        # Access records use INFO even for 5xx. Filter every formatted message,
        # without modifying the record seen by other handlers or the tracker.
        record = copy.copy(record)
        record.msg = scrub_text(record.getMessage())
        record.args = ()
        record.exc_text = None
        if record.stack_info:
            record.stack_info = scrub_text(record.stack_info)
        return super().format(record)

    def formatException(self, exc_info) -> str:
        return scrub_text(super().formatException(exc_info))


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
    for name in ("", "uvicorn", "uvicorn.error", "uvicorn.access"):
        for handler in logging.getLogger(name).handlers:
            handler.setFormatter(RequestFormatter())
