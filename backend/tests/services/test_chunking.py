from __future__ import annotations

from backend.app.services.chunking import build_document_chunks


def test_build_document_chunks_creates_parent_and_child_rows() -> None:
    text = (
        "## Section A\n"
        "Alpha details line one.\n\n"
        "Alpha details line two.\n\n"
        "## Section B\n"
        "Beta details line one.\n\n"
        "Beta details line two."
    )

    parent_chunks, child_chunks = build_document_chunks(
        knowledge_item_id="ki-1",
        raw_content=text,
    )

    assert len(parent_chunks) == 2
    assert len(child_chunks) >= 2
    assert all(chunk.chunk_level == "parent" for chunk in parent_chunks)
    assert all(chunk.chunk_level == "child" for chunk in child_chunks)
    assert {chunk.parent_chunk_id for chunk in child_chunks} == {
        chunk.id for chunk in parent_chunks
    }


def test_build_document_chunks_falls_back_without_headings() -> None:
    text = ("Paragraph one. " * 80) + "\n\n" + ("Paragraph two. " * 80)

    parent_chunks, child_chunks = build_document_chunks(
        knowledge_item_id="ki-2",
        raw_content=text,
    )

    assert len(parent_chunks) >= 1
    assert len(child_chunks) >= len(parent_chunks)
    assert all(chunk.content.strip() for chunk in parent_chunks + child_chunks)
