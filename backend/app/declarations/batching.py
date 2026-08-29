"""Deterministic, bounded page batching for declaration extraction."""

from __future__ import annotations

from dataclasses import dataclass
from collections import deque
from collections.abc import Callable
from typing import Iterable, Sequence

from app.declarations.provider import DocumentInput, PageInput


@dataclass(frozen=True, slots=True)
class BatchedPage:
    """An immutable provider page, optionally a deterministic text segment."""

    document_key: str
    pdf_page: int
    text: str
    quality: float
    image_data_url: str | None = None
    segment_no: int = 1
    segment_count: int = 1

    def as_page_input(self) -> PageInput:
        return PageInput(
            document_key=self.document_key,
            pdf_page=self.pdf_page,
            text=self.text,
            quality=self.quality,
            image_data_url=self.image_data_url,
        )


@dataclass(frozen=True, slots=True)
class PageBatch:
    pages: tuple[BatchedPage, ...]
    character_count: int

    def as_documents(self) -> tuple[DocumentInput, ...]:
        """Group consecutive pages by document without changing their order."""
        documents: list[DocumentInput] = []
        current_key: str | None = None
        current: list[PageInput] = []
        for page in self.pages:
            if current_key is not None and page.document_key != current_key:
                documents.append(DocumentInput(document_key=current_key, pages=tuple(current)))
                current = []
            current_key = page.document_key
            current.append(page.as_page_input())
        if current_key is not None:
            documents.append(DocumentInput(document_key=current_key, pages=tuple(current)))
        return tuple(documents)


class PageBatcher:
    """Produce stable bounded batches with one safe boundary-page overlap."""

    def __init__(
        self,
        max_pages: int,
        max_chars: int,
        *,
        fits_batch: Callable[[tuple[BatchedPage, ...]], bool] | None = None,
        overlap: bool = True,
    ) -> None:
        if max_pages < 1 or max_chars < 1:
            raise ValueError("batch limits must be positive")
        if not isinstance(overlap, bool):
            raise ValueError("overlap must be boolean")
        self._max_pages = max_pages
        self._max_chars = max_chars
        self._fits_batch = fits_batch
        self._overlap = overlap

    def batch(self, pages: Sequence[PageInput]) -> tuple[PageBatch, ...]:
        expanded = tuple(page for item in pages for page in self._segments(item))
        if not expanded:
            return ()
        result: list[PageBatch] = []
        pending = deque(expanded)
        previous: BatchedPage | None = None
        while pending:
            current: list[BatchedPage] = []
            chars = 0
            has_overlap = (
                self._overlap
                and previous is not None
                and len(previous.text) <= self._max_chars
                and self._fits((previous,))
            )
            if has_overlap:
                assert previous is not None
                current.append(previous)
                chars = len(previous.text)
            while pending:
                page = pending[0]
                candidate = tuple([*current, page])
                if (
                    len(current) >= self._max_pages
                    or chars + len(page.text) > self._max_chars
                    or not self._fits(candidate)
                ):
                    break
                current.append(pending.popleft())
                chars += len(page.text)
            # A boundary page that consumes the cap cannot be repeated.  This
            # still makes forward progress because oversized pages are segmented.
            if len(current) == (1 if has_overlap else 0) and pending:
                if has_overlap:
                    current = []
                    chars = 0
                page = pending.popleft()
                if not self._fits((page,)):
                    raise ValueError("page cannot be represented within batch envelope")
                current.append(page)
                chars = len(page.text)
            if not current:  # defensive: segmentation guarantees this cannot occur
                raise ValueError("page cannot be represented within character cap")
            result.append(PageBatch(pages=tuple(current), character_count=chars))
            previous = current[-1]
        return tuple(result)

    def batches(self, pages: Sequence[PageInput]) -> tuple[PageBatch, ...]:
        return self.batch(pages)

    def _segments(self, page: PageInput) -> Iterable[BatchedPage]:
        whole = BatchedPage(
            document_key=page.document_key,
            pdf_page=page.pdf_page,
            text=page.text,
            quality=page.quality,
            image_data_url=page.image_data_url,
        )
        if len(page.text) <= self._max_chars and self._fits((whole,)):
            yield whole
            return
        if not page.text:
            raise ValueError("page cannot be represented within batch envelope")
        # The final marker's number and count cannot have more digits than the
        # source has characters.  Probe with that worst-case marker while
        # consuming the source once.  Replacing it with the real marker can
        # only shorten the exact singleton envelope, so count discovery never
        # requires retrying all prior chunks.
        digit_width = len(str(len(page.text)))
        largest_number = "9" * digit_width
        probe_marker = f"[segment {largest_number}/{largest_number}]\n"
        maximum = self._max_chars - len(probe_marker)
        if maximum < 1:
            raise ValueError("page cannot be represented within batch envelope")

        source_slices: list[tuple[int, int]] = []
        offset = 0
        probe_number = int(largest_number)
        while offset < len(page.text):
            size = self._largest_fitting_segment(
                page,
                probe_marker,
                offset,
                min(maximum, len(page.text) - offset),
                probe_number,
                probe_number,
            )
            if size < 1:
                raise ValueError("page cannot be represented within batch envelope")
            source_slices.append((offset, offset + size))
            offset += size

        count = len(source_slices)
        for index, (start, end) in enumerate(source_slices, start=1):
            marker = f"[segment {index}/{count}]\n"
            segment = BatchedPage(
                document_key=page.document_key,
                pdf_page=page.pdf_page,
                text=marker + page.text[start:end],
                quality=page.quality,
                # A vision image is attached to exactly one segment of its
                # logical page. Recheck batching disables boundary overlap,
                # so this cannot duplicate image input across SDK requests.
                image_data_url=page.image_data_url if index == 1 else None,
                segment_no=index,
                segment_count=count,
            )
            if len(segment.text) > self._max_chars or not self._fits((segment,)):
                raise ValueError("page cannot be represented within batch envelope")
            yield segment

    def _largest_fitting_segment(
        self,
        page: PageInput,
        marker: str,
        offset: int,
        maximum: int,
        segment_no: int,
        segment_count: int,
    ) -> int:
        low, high, best = 1, maximum, 0
        while low <= high:
            size = (low + high) // 2
            candidate = BatchedPage(
                document_key=page.document_key,
                pdf_page=page.pdf_page,
                text=marker + page.text[offset : offset + size],
                quality=page.quality,
                image_data_url=None,
                segment_no=segment_no,
                segment_count=segment_count,
            )

            if self._fits((candidate,)):
                best = size
                low = size + 1
            else:
                high = size - 1
        return best

    def _fits(self, pages: tuple[BatchedPage, ...]) -> bool:
        return self._fits_batch(pages) if self._fits_batch is not None else True
