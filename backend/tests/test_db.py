from __future__ import annotations

from backend.app.db import Database


def test_database_connection_keeps_foreign_keys_without_memory_journal(
    app_paths: dict[str, str],
) -> None:
    database = Database(app_paths["sqlite_path"])
    database.initialize()

    with database.connect() as connection:
        foreign_keys = connection.execute("PRAGMA foreign_keys;").fetchone()[0]
        journal_mode = connection.execute("PRAGMA journal_mode;").fetchone()[0]

    assert foreign_keys == 1
    assert journal_mode != "memory"


def test_database_initializes_document_chunks_table(
    app_paths: dict[str, str],
) -> None:
    database = Database(app_paths["sqlite_path"])
    database.initialize()

    with database.connect() as connection:
        table_row = connection.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table' AND name = 'document_chunks'
            """
        ).fetchone()
        columns = connection.execute(
            "PRAGMA table_info(document_chunks)"
        ).fetchall()

    assert table_row is not None
    assert {column[1] for column in columns} >= {
        "id",
        "knowledge_item_id",
        "parent_chunk_id",
        "chunk_level",
        "section_title",
        "content",
        "position",
        "token_estimate",
        "embedding_provider",
        "embedding_model",
        "vector_point_id",
        "created_at",
    }


def test_database_initializes_document_parse_results_schema(
    app_paths: dict[str, str],
) -> None:
    database = Database(app_paths["sqlite_path"])
    database.initialize()

    with database.connect() as connection:
        table_row = connection.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table' AND name = 'document_parse_results'
            """
        ).fetchone()
        columns = connection.execute("PRAGMA table_info(knowledge_items)").fetchall()

    assert table_row is not None
    assert "active_parse_result_id" in {column[1] for column in columns}


def test_database_initializes_retrieval_index_versions_schema(
    app_paths: dict[str, str],
) -> None:
    database = Database(app_paths["sqlite_path"])
    database.initialize()

    with database.connect() as connection:
        table_row = connection.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table' AND name = 'retrieval_index_versions'
            """
        ).fetchone()
        columns = connection.execute("PRAGMA table_info(retrieval_index_versions)").fetchall()

    assert table_row is not None
    assert {column[1] for column in columns} >= {
        "id",
        "index_scope",
        "version_tag",
        "collection_name",
        "embedding_provider",
        "embedding_model",
        "status",
        "created_at",
        "activated_at",
    }
