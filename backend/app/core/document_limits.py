"""Shared file-content safety cap, independent of deployment and plan selection."""

from decimal import Decimal, ROUND_HALF_UP

MAX_UPLOAD_BYTES = 25 * 1024**2

# The memory of the inbox's model stage. The declaration worker runs it in a container of ``CONTAINER_MEMORY_BYTES`` (the
# ``mem_limit`` of that service in every Compose file; a contract test reads the three files). The stage starts children for
# untrusted bytes (the local parse of a PDF, the decode of a PNG or JPEG) and gives each an address-space limit, so that a
# document that needs more is stopped by the child's own bound and not by the container, whose kernel would end whichever
# process it chooses, the worker included. ``WORKER_RESERVE_BYTES`` is what the worker process itself holds while a child
# runs: measured on 2026-11-22 (resident memory, macOS, offline fake providers, the largest originals the path accepts) it
# is about 120 MiB loaded and 185 MiB while it holds a 25 MiB original twice at the peak of the image child (the figures
# are in PR #832); the rest is room for the allocator and the temporary files of a render. A child gets what remains.
CONTAINER_MEMORY_BYTES = 768 * 1024**2
WORKER_RESERVE_BYTES = 256 * 1024**2
CHILD_MEMORY_BYTES = CONTAINER_MEMORY_BYTES - WORKER_RESERVE_BYTES


def format_upload_limit(value: int) -> str:
    """Match the frontend's German MiB/GiB display, including half-up rounding."""
    unit, divisor = ("GiB", 1024**3) if value >= 1024**3 else ("MiB", 1024**2)
    amount = (Decimal(value) / divisor).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    number = f"{amount:,.2f}".rstrip("0").rstrip(".").translate(str.maketrans(",.", ".,"))
    return f"{number} {unit}"
