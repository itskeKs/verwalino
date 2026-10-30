"""One logging configuration for the API, Uvicorn and worker processes."""

import logging

from app.core.request_id import request_id


class RequestFormatter(logging.Formatter):
    def __init__(self):
        super().__init__("%(asctime)s %(levelname)s %(name)s request_id=%(request_id)s %(message)s")

    def format(self, record: logging.LogRecord) -> str:
        if not hasattr(record, "request_id"):
            record.request_id = request_id.get()
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
    for name in ("", "uvicorn", "uvicorn.error", "uvicorn.access"):
        for handler in logging.getLogger(name).handlers:
            handler.setFormatter(RequestFormatter())
