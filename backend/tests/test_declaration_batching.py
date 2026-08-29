import json

import pytest

from app.declarations.batching import PageBatcher
from app.declarations.provider import PageInput


def test_page_batcher_module_is_available() -> None:
    assert PageBatcher is not None


def test_batches_43_pages_with_only_boundary_overlap() -> None:
    pages = tuple(
        PageInput("doc", index, f"page {index}", 1.0) for index in range(1, 44)
    )
    batches = PageBatcher(max_pages=20, max_chars=80_000).batch(pages)
    assert [len(batch.pages) for batch in batches] == [20, 20, 5]
    flattened = [page.pdf_page for batch in batches for page in batch.pages]
    assert flattened == list(range(1, 21)) + list(range(20, 40)) + list(range(39, 44))
    assert all(batch.character_count <= 80_000 for batch in batches)


def test_oversized_page_is_segmented_without_losing_source_coordinates() -> None:
    batches = PageBatcher(max_pages=20, max_chars=20).batch(
        (PageInput("doc", 7, "x" * 50, 1.0),)
    )
    segments = [page for batch in batches for page in batch.pages]
    assert {page.document_key for page in segments} == {"doc"}
    assert {page.pdf_page for page in segments} == {7}
    assert all(len(page.text) <= 20 for page in segments)
    assert "".join(page.text.split("\n", 1)[1] for page in segments if "\n" in page.text) == "x" * 50


def test_batches_keep_stable_multi_document_order_under_character_cap() -> None:
    pages = (
        PageInput("a", 1, "a" * 8, 1.0),
        PageInput("a", 2, "b" * 8, 1.0),
        PageInput("b", 1, "c" * 8, 1.0),
    )
    batches = PageBatcher(max_pages=20, max_chars=16).batch(pages)
    assert [(page.document_key, page.pdf_page) for batch in batches for page in batch.pages] == [
        ("a", 1), ("a", 2), ("a", 2), ("b", 1),
    ]
    assert all(batch.character_count <= 16 for batch in batches)


def test_oversized_segmentation_uses_near_linear_envelope_probes() -> None:
    probes = 0

    def fits(pages) -> bool:
        nonlocal probes
        probes += 1
        return all(len(page.text) <= 120 for page in pages)

    batches = PageBatcher(max_pages=20, max_chars=120, fits_batch=fits).batch(
        (PageInput("doc", 1, "x" * 12_000, 1.0),)
    )
    segments = {
        (page.segment_no, page.segment_count): page
        for batch in batches
        for page in batch.pages
    }

    assert "".join(
        page.text.split("\n", 1)[1]
        for _key, page in sorted(segments.items())
    ) == "x" * 12_000
    # A chunk may take a binary search plus a small fixed number of batch
    # envelope probes.  This catches count-by-count O(k²) segmentation.
    assert probes <= len(segments) * 20 + 10


def test_oversized_escape_heavy_page_preserves_source_and_exact_envelope_cap() -> None:
    cap = 170

    def fits(pages) -> bool:
        envelope = json.dumps(
            [{"document_key": page.document_key, "page": page.pdf_page, "text": page.text} for page in pages],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return len(envelope) <= cap

    source = ('"\\') * 500
    batches = PageBatcher(max_pages=20, max_chars=cap, fits_batch=fits).batch(
        (PageInput("doc", 7, source, 1.0),)
    )
    segments = {
        (page.segment_no, page.segment_count): page
        for batch in batches
        for page in batch.pages
    }

    assert all(fits(batch.pages) for batch in batches)
    assert all(batch.character_count <= cap for batch in batches)
    assert "".join(
        page.text.split("\n", 1)[1]
        for _key, page in sorted(segments.items())
    ) == source


def test_oversized_page_fails_when_minimal_marked_segment_cannot_fit() -> None:
    with pytest.raises(ValueError, match="page cannot be represented"):
        PageBatcher(max_pages=20, max_chars=20, fits_batch=lambda _pages: False).batch(
            (PageInput("doc", 1, "x" * 30, 1.0),)
        )


def test_recheck_batches_have_no_overlap_and_keep_a_selected_image_once() -> None:
    image = "data:image/png;base64,iVBORw0KGgo="
    batches = PageBatcher(max_pages=1, max_chars=25, overlap=False).batch(
        (PageInput("doc", 1, "x" * 60, 1.0, image_data_url=image),)
    )

    pages = [page for batch in batches for page in batch.pages]
    assert [(page.document_key, page.pdf_page, page.segment_no) for page in pages] == [
        ("doc", 1, index) for index in range(1, len(pages) + 1)
    ]
    assert [page.image_data_url for page in pages].count(image) == 1
    assert pages[0].image_data_url == image
