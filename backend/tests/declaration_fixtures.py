from __future__ import annotations

import io
from collections.abc import Callable

import pikepdf
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas


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
