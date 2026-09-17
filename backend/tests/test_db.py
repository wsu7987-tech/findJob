from __future__ import annotations

import sqlite3

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


def test_database_initializes_run_executor_columns(app_paths: dict[str, str]) -> None:
    database = Database(app_paths["sqlite_path"])
    database.initialize()

    with database.connect() as connection:
        columns = connection.execute("PRAGMA table_info(run_records)").fetchall()

    assert {column[1] for column in columns} >= {
        "executor_type",
        "executor_version",
        "model_name",
        "reasoning_effort",
    }


def test_database_initializes_boss_capture_history_tables(app_paths: dict[str, str]) -> None:
    database = Database(app_paths["sqlite_path"])
    database.initialize()

    with database.connect() as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        boss_job_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(fj_boss_jobs)").fetchall()
        }

    assert {
        "fj_boss_capture_batches",
        "fj_boss_jobs",
        "fj_boss_capture_batch_jobs",
        "fj_job_filter_strategies",
        "fj_job_recommendation_strategies",
    } <= tables
    assert {"company_stage", "company_industry", "welfare"} <= boss_job_columns


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


def test_database_initializes_boss_chat_debounce_columns(
    app_paths: dict[str, str],
) -> None:
    database = Database(app_paths["sqlite_path"])
    database.initialize()

    with database.connect() as connection:
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(fj_chat_reply_tasks)").fetchall()
        }
        indexes = {
            row[1]
            for row in connection.execute("PRAGMA index_list(fj_chat_reply_tasks)").fetchall()
        }

    assert {
        "generation_due_at",
        "input_message_ids_json",
        "decision",
        "facts_used_json",
        "warnings_json",
        "requires_user_input",
        "decision_reason",
        "job_action_key",
    } <= columns
    assert "idx_fj_chat_reply_tasks_due" in indexes
    assert "idx_fj_chat_reply_tasks_active_action_key" in indexes

    with database.connect() as connection:
        chat_session_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(fj_chat_sessions)").fetchall()
    }

    assert {"history_has_more", "history_next_cursor"} <= chat_session_columns

    with database.connect() as connection:
        send_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(fj_chat_send_actions)").fetchall()
        }
        send_indexes = {
            row[1]
            for row in connection.execute("PRAGMA index_list(fj_chat_send_actions)").fetchall()
        }

    assert {
        "leader_tab_id",
        "leader_epoch",
        "dispatch_deadline_at",
        "platform_message_id",
        "client_mid",
    } <= send_columns
    assert "idx_fj_chat_send_actions_dispatch_deadline" in send_indexes


def test_database_recovers_from_interrupted_smart_capture_migration(
    app_paths: dict[str, str],
) -> None:
    database = Database(app_paths["sqlite_path"])
    app_paths["sqlite_path"].parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(app_paths["sqlite_path"]) as connection:
        connection.executescript(
            """
            CREATE TABLE fj_smart_captures (
              id TEXT PRIMARY KEY,
              source TEXT NOT NULL DEFAULT 'boss_capture',
              orchestration_run_id TEXT,
              capture_task_id TEXT,
              status TEXT NOT NULL DEFAULT 'queued',
              config_json TEXT NOT NULL DEFAULT '{}',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              completed_at TEXT,
              is_current INTEGER NOT NULL DEFAULT 0,
              workflow_run_id TEXT,
              current_batch_id TEXT,
              search_config_json TEXT,
              target_count INTEGER,
              stage TEXT,
              message TEXT,
              error_message TEXT
            );
            CREATE TABLE fj_smart_capture_current (
              slot INTEGER PRIMARY KEY,
              smart_capture_id TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE fj_smart_captures_legacy_status (
              id TEXT PRIMARY KEY,
              source TEXT NOT NULL DEFAULT 'boss_capture',
              orchestration_run_id TEXT,
              capture_task_id TEXT,
              status TEXT NOT NULL DEFAULT 'queued',
              config_json TEXT NOT NULL DEFAULT '{}',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              completed_at TEXT,
              is_current INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE fj_boss_capture_batches (
              id TEXT PRIMARY KEY,
              smart_capture_id TEXT,
              capture_source TEXT NOT NULL DEFAULT 'custom',
              keyword TEXT NOT NULL,
              city TEXT NOT NULL,
              pages INTEGER NOT NULL DEFAULT 1,
              auto_details INTEGER NOT NULL DEFAULT 0,
              status TEXT NOT NULL DEFAULT 'queued',
              source_url TEXT,
              jobs_collected INTEGER NOT NULL DEFAULT 0,
              details_completed INTEGER NOT NULL DEFAULT 0,
              details_failed INTEGER NOT NULL DEFAULT 0,
              stage TEXT NOT NULL DEFAULT 'queued',
              message TEXT NOT NULL DEFAULT '',
              error_message TEXT,
              progress_current INTEGER NOT NULL DEFAULT 0,
              progress_total INTEGER NOT NULL DEFAULT 0,
              control_status TEXT NOT NULL DEFAULT 'active',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              finished_at TEXT,
              FOREIGN KEY (smart_capture_id) REFERENCES fj_smart_captures(id) ON DELETE SET NULL
            );
            INSERT INTO fj_smart_captures (
              id, source, status, created_at, updated_at, current_batch_id
            ) VALUES ('old-current', 'boss_capture', 'running', '2026-09-18T00:00:00Z', '2026-09-18T00:00:01Z', 'batch-1');
            INSERT INTO fj_smart_capture_current (slot, smart_capture_id, updated_at)
            VALUES (1, 'old-current', '2026-09-18T00:00:01Z');
            INSERT INTO fj_smart_captures_legacy_status (
              id, source, status, created_at, updated_at, is_current
            ) VALUES ('legacy-paused', 'orchestration', 'paused', '2026-09-18T00:00:02Z', '2026-09-18T00:00:03Z', 1);
            INSERT INTO fj_boss_capture_batches (
              id, keyword, city, created_at, updated_at
            ) VALUES ('batch-1', 'python', '上海', '2026-09-18T00:00:00Z', '2026-09-18T00:00:01Z');
            """
        )

    database.initialize()
    database.initialize()

    with database.connect() as connection:
        tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        captures = {
            row["id"]: row
            for row in connection.execute(
                "SELECT id, status FROM fj_smart_captures"
            ).fetchall()
        }
        current = connection.execute(
            "SELECT smart_capture_id FROM fj_smart_capture_current WHERE slot = 1"
        ).fetchone()
        batch = connection.execute(
            "SELECT smart_capture_id FROM fj_boss_capture_batches WHERE id = 'batch-1'"
        ).fetchone()
        foreign_key = next(
            row
            for row in connection.execute(
                "PRAGMA foreign_key_list(fj_boss_capture_batches)"
            ).fetchall()
            if row["from"] == "smart_capture_id"
        )

    assert not any(name.startswith("fj_smart_captures_legacy_status") for name in tables)
    assert captures["old-current"]["status"] == "running"
    assert captures["legacy-paused"]["status"] == "paused"
    assert current["smart_capture_id"] == "old-current"
    assert batch["smart_capture_id"] == "old-current"
    assert foreign_key["table"] == "fj_smart_captures"


def test_database_initializes_job_action_state_schema(
    app_paths: dict[str, str],
) -> None:
    database = Database(app_paths["sqlite_path"])
    database.initialize()

    with database.connect() as connection:
        columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(fj_job_action_item_states)"
            ).fetchall()
        }
        indexes = {
            row[1]
            for row in connection.execute(
                "PRAGMA index_list(fj_job_action_item_states)"
            ).fetchall()
        }

    assert {
        "action_key",
        "job_id",
        "session_id",
        "action_type",
        "status",
        "snoozed_until",
        "created_at",
        "updated_at",
    } <= columns
    assert {
        "idx_fj_job_action_states_status_snooze",
        "idx_fj_job_action_states_job_updated",
    } <= indexes
