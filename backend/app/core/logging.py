"""One logging configuration for the API, Uvicorn and worker processes."""

import logging
import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import tempfile
from uuid import UUID, uuid4

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


class DiagnosticsHandler(logging.Handler):
    """Transient per-host JSONL records; never stores messages or LogRecords.

    Each PID owns a latest-500 file and latest-50 organization reserves. The
    reserves let a quiet organization survive traffic from a busy neighbor.
    """

    ORGANIZATION_RESERVE = 50
    _WORKER_FILE = re.compile(r'([1-9][0-9]*)(?:\.[0-9a-f-]{36})?\.jsonl')

    def __init__(self):
        super().__init__(logging.WARNING)

    @staticmethod
    def _directory() -> Path:
        return Path(os.environ.get('HV_DIAGNOSTICS_DIR', '/tmp/verwalino-diagnostics'))

    @staticmethod
    def _read(path: Path) -> list[dict]:
        from app.diagnostics import DEFAULT_LOG_LINES, diagnostic_log_lines
        from app.core.scrub import MAX_SCRUB_CHARS

        entries = []
        try:
            with path.open(encoding='utf-8') as source:
                for _ in range(DEFAULT_LOG_LINES):
                    raw = source.readline(MAX_SCRUB_CHARS + 1)
                    if not raw:
                        break
                    try:
                        entry = json.loads(raw)
                        owner = str(UUID(entry['organization_id']))
                        event_id = str(UUID(entry['event_id']))
                        lines = diagnostic_log_lines([entry['line']])
                        if lines:
                            entries.append({'event_id': event_id, 'organization_id': owner, 'line': lines[0]})
                    except (ValueError, KeyError, TypeError, AttributeError):
                        continue
        except (OSError, UnicodeError):
            pass
        return entries

    def _append(self, path: Path, entry: dict, limit: int) -> None:
        entries = self._read(path)
        if len(entries) < limit:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
            with os.fdopen(descriptor, 'w', encoding='utf-8') as output:
                output.write(json.dumps(entry) + '\n')
            return
        # Atomic compaction gives concurrent readers a complete old/new file.
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False, encoding='utf-8') as output:
                temporary = Path(output.name)
                for row in [*entries[-(limit - 1):], entry]:
                    output.write(json.dumps(row) + '\n')
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    @staticmethod
    def _alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            pass
        return True

    def _store(self, organization: str, line: str) -> None:
        from app.diagnostics import DEFAULT_LOG_LINES

        directory = self._directory()
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if directory.is_symlink() or directory.stat().st_mode & 0o077:
            return
        pid = os.getpid()
        # The two copies share an event ID; identical public fields may still
        # describe distinct events within the same millisecond.
        entry = {'event_id': str(uuid4()), 'organization_id': organization, 'line': line}
        self._append(directory / f'{pid}.jsonl', entry, DEFAULT_LOG_LINES)
        self._append(directory / f'{pid}.{organization}.jsonl', entry, self.ORGANIZATION_RESERVE)

    def emit(self, record: logging.LogRecord) -> None:
        from app.core.error_tracking import request_state
        from app.diagnostics import diagnostic_log_lines

        state = request_state.get() or {}
        organization = state.get("organization_id")
        if organization is None:
            return
        organization = str(UUID(str(organization)))
        stamp = datetime.fromtimestamp(record.created, timezone.utc).strftime("%Y-%m-%d %H:%M:%S,%f")[:-3]
        identity = getattr(record, "request_id", request_id.get())
        exception = type(record.exc_info[1]).__name__ + ":" if record.exc_info and record.exc_info[1] else ""
        # Formatting message/traceback would retain free text unnecessarily.
        lines = diagnostic_log_lines([f"{stamp} {record.levelname} app request_id={identity} {exception}"])
        try:
            for line in lines:
                self._store(organization, line)
        except OSError:
            # Diagnostics must never replace the failure being reported or
            # fall back to logging the original record/message on I/O errors.
            pass

    def for_organization(self, organization) -> list[str]:
        from app.diagnostics import DEFAULT_LOG_LINES

        records = {}
        try:
            for path in self._directory().glob('*.jsonl'):
                match = self._WORKER_FILE.fullmatch(path.name)
                if not match or path.is_symlink():
                    continue
                if not self._alive(int(match[1])):
                    path.unlink(missing_ok=True)
                    continue
                for entry in self._read(path):
                    records[entry['event_id']] = entry
        except OSError:
            pass
        ordered = sorted(records.values(), key=lambda entry: entry['line'][:23])
        own = [entry for entry in ordered if entry['organization_id'] == str(organization)]
        retained = {entry['event_id'] for entry in ordered[-DEFAULT_LOG_LINES:] + own[-self.ORGANIZATION_RESERVE:]}
        return [entry['line'] for entry in own if entry['event_id'] in retained]


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
