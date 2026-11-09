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
