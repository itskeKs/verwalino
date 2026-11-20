from __future__ import annotations

import asyncio
import io
import threading
from collections.abc import Callable

import pikepdf
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas


class DeclarationProcessingGate:
    """Hold a synchronous OCR operation until an async test releases it."""

    def __init__(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._started = asyncio.Event()
        self.release = threading.Event()
        self.released_in_time = False
        self._heartbeat_requested = False

    def hold(self) -> None:
        self._loop.call_soon_threadsafe(self._started.set)
        self.released_in_time = self.release.wait(timeout=5)

    async def wait_until_started(self) -> None:
        await asyncio.wait_for(self._started.wait(), timeout=10)

    async def release_after_renewal(self, _seconds: float) -> None:
        await self.wait_until_started()
        if not self._heartbeat_requested:
            self._heartbeat_requested = True
            return
        # The real heartbeat loop only sleeps again after committing a renewal.
        self.release.set()
        await asyncio.Future()


def make_text_pdf(*pages: str) -> bytes:
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    for text in pages:
        y = A4[1] - 50
        for line in text.splitlines():
            pdf.drawString(50, y, line)
            y -= 16
        pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def mutate_pdf(data: bytes, mutate: Callable[[pikepdf.Pdf], None]) -> bytes:
    """Return a synthetic PDF with a deliberately unsafe object graph."""
    output = io.BytesIO()
    with pikepdf.open(io.BytesIO(data), attempt_recovery=False) as pdf:
        mutate(pdf)
        pdf.save(output)
    return output.getvalue()


def lindenallee_reading_blocks():
    """What the reading model transcribes from the fictional Lindenallee scan (docs/acceptance/data): its units
    1 and 2 with 450/1,000 each and the garage G1 with 100/1,000, the units as a table, one cell per row."""
    from app.declarations.schemas import ReadingBlock

    return [
        ReadingBlock(kind="TEXT", text="WEG Lindenallee 12"),
        ReadingBlock(kind="TEXT", text="Lindenallee 12, 01234 Musterstadt"),
        ReadingBlock(kind="TEXT", text="Miteigentumsanteile insgesamt 1.000"),
        ReadingBlock(kind="TABLE", rows=[
            ["1 Wohnung / Erdgeschoss 450 / 1.000"],
            ["2 Wohnung / Obergeschoss 450 / 1.000"],
            ["G1 Garage / Hof 100 / 1.000"],
        ]),
    ]


class SlowReadingProvider:
    """A fake reading provider that holds the gate while it reads, like a slow model call."""

    def __init__(self, gate: DeclarationProcessingGate, delegate) -> None:
        self._gate, self._delegate = gate, delegate
        self.estimate_reservation = delegate.estimate_reservation

    async def read_page(self, page, *, model: str, max_output_tokens: int):
        await asyncio.to_thread(self._gate.hold)
        return await self._delegate.read_page(page, model=model, max_output_tokens=max_output_tokens)


class ModelRecording:
    """Wraps an extraction provider with the model it was built for and records which model each pass was asked
    for, and the documents it received."""

    def __init__(self, delegate, model: str) -> None:
        self._delegate = delegate
        self._primary_model = model
        self._client = None  # nothing to close, as for a provider the worker would build
        self.records: list[tuple[str, str]] = []
        # Per call: the method, the model and the (document key, page) pairs of the documents it was given.
        self.calls: list[tuple[str, str, tuple[tuple[str, int], ...]]] = []
        self.documents: list = []

    def estimate_reservation(self, *args, **options):
        return self._delegate.estimate_reservation(*args, **options)

    def __getattr__(self, name: str):
        method = getattr(self._delegate, name)

        async def call(*args, **options):
            self.records.append((name, self._primary_model))
            pages: tuple[tuple[str, int], ...] = ()
            if args and isinstance(args[0], (list, tuple)):
                self.documents.extend(args[0])
                pages = tuple((page.document_key, page.pdf_page) for document in args[0] for page in getattr(document, "pages", ()))
            self.calls.append((name, self._primary_model, pages))
            return await method(*args, **options)

        return call
