from __future__ import annotations

from backend.app.services.chunking import DocumentChunk
from backend.app.services.lexical import build_lexical_document
from backend.app.utils import utc_now


def insert_document_chunks(connection, chunks: list[DocumentChunk]) -> None:
    now = utc_now()
    connection.executemany(
        """
        INSERT INTO document_chunks (
          id,
          knowledge_item_id,
          parent_chunk_id,
          chunk_level,
          section_title,
          content,
          position,
          token_estimate,
          embedding_provider,
          embedding_model,
          vector_point_id,
          created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, ?)
        """,
        [
            (
                chunk.id,
                chunk.knowledge_item_id,
                chunk.parent_chunk_id,
                chunk.chunk_level,
                chunk.section_title,
                chunk.content,
                chunk.position,
                chunk.token_estimate,
                now,
            )
            for chunk in chunks
        ],
    )


def mark_chunk_indexed(
    connection,
    *,
    chunk_id: str,
    embedding_provider: str,
    embedding_model: str,
    vector_point_id: str,
) -> None:
    connection.execute(
        """
        UPDATE document_chunks
        SET embedding_provider = ?, embedding_model = ?, vector_point_id = ?
        WHERE id = ?
        """,
        (embedding_provider, embedding_model, vector_point_id, chunk_id),
    )


def list_document_chunks(connection, knowledge_item_id: str):
    return connection.execute(
        """
        SELECT *
        FROM document_chunks
        WHERE knowledge_item_id = ?
        ORDER BY
          CASE chunk_level WHEN 'parent' THEN 0 ELSE 1 END,
          position ASC,
          id ASC
        """,
        (knowledge_item_id,),
    ).fetchall()


def delete_document_chunks_for_item(connection, *, knowledge_item_id: str) -> None:
    delete_document_chunk_fts_for_item(connection, knowledge_item_id=knowledge_item_id)
    connection.execute(
        """
        DELETE FROM document_chunks
        WHERE knowledge_item_id = ?
        """,
        (knowledge_item_id,),
    )


def delete_document_chunk_fts_for_item(connection, *, knowledge_item_id: str) -> None:
    connection.execute(
        """
        DELETE FROM document_chunks_fts
        WHERE knowledge_item_id = ?
        """,
        (knowledge_item_id,),
    )


def rebuild_document_chunk_fts_for_item(connection, *, knowledge_item_id: str) -> None:
    delete_document_chunk_fts_for_item(connection, knowledge_item_id=knowledge_item_id)
    rows = connection.execute(
        """
        SELECT
          dc.id AS chunk_id,
          dc.knowledge_item_id,
          dc.parent_chunk_id,
          dc.section_title,
          dc.content,
          ki.title
        FROM document_chunks AS dc
        JOIN knowledge_items AS ki ON ki.id = dc.knowledge_item_id
        WHERE dc.knowledge_item_id = ? AND dc.chunk_level = 'child'
        ORDER BY dc.position ASC, dc.id ASC
        """,
        (knowledge_item_id,),
    ).fetchall()
    if not rows:
        return

    connection.executemany(
        """
        INSERT INTO document_chunks_fts (
          chunk_id,
          knowledge_item_id,
          parent_chunk_id,
          title,
          section_title,
          content,
          lexical_terms
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                str(row["chunk_id"]),
                str(row["knowledge_item_id"]),
                str(row["parent_chunk_id"] or ""),
                row["title"],
                row["section_title"],
                str(row["content"]),
                build_lexical_document(
                    row["title"],
                    row["section_title"],
                    str(row["content"]),
                ),
            )
            for row in rows
        ],
    )


def rebuild_document_chunk_fts_index(connection) -> None:
    connection.execute("DELETE FROM document_chunks_fts")
    knowledge_item_ids = [
        str(row["knowledge_item_id"])
        for row in connection.execute(
            """
            SELECT DISTINCT knowledge_item_id
            FROM document_chunks
            WHERE chunk_level = 'child'
            ORDER BY knowledge_item_id ASC
            """
        ).fetchall()
    ]
    for knowledge_item_id in knowledge_item_ids:
        rebuild_document_chunk_fts_for_item(connection, knowledge_item_id=knowledge_item_id)
