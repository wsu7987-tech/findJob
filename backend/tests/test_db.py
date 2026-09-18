from __future__ import annotations

import sqlite3

import pytest

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


def test_database_initializes_workflow_child_relation_and_create_idempotency(
    app_paths: dict[str, str],
) -> None:
    database = Database(app_paths["sqlite_path"])
    database.initialize()

    with database.connect() as connection:
        workflow_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(fj_workflow_runs)")
        }
        child_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(fj_workflow_children)")
        }
        child_indexes = {
            row[1] for row in connection.execute("PRAGMA index_list(fj_workflow_children)")
        }
        workflow_indexes = {
            row[1]
            for row in connection.execute("PRAGMA index_list(fj_workflow_runs)")
        }
        event_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(fj_workflow_child_events)")
        }

    assert "idempotency_key" in workflow_columns
    assert {
        "control_state",
        "waiting_reason",
        "control_cause",
        "state_version",
        "transition_id",
    } <= workflow_columns
    assert {
        "id",
        "workflow_run_id",
        "child_type",
        "child_ref",
        "sequence",
        "status",
        "control_state",
        "waiting_reason",
        "control_cause",
        "capabilities_json",
        "result_summary_json",
        "started_at",
        "completed_at",
        "created_at",
        "updated_at",
        "state_version",
        "child_state_version",
        "transition_id",
    } <= child_columns
    assert {
        "event_id",
        "transition_id",
        "child_relation_id",
        "child_type",
        "child_ref",
        "child_status",
        "state_version",
        "consumed_at",
    } <= event_columns
    assert "idx_fj_workflow_children_identity" in child_indexes
    assert "idx_fj_workflow_runs_idempotency_key" in workflow_indexes


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


def test_database_initializes_pipeline_owner_schema_for_independent_capture(
    app_paths: dict[str, str],
) -> None:
    database = Database(app_paths["sqlite_path"])
    database.initialize()
    database.initialize()

    now = "2026-09-19T00:00:00Z"
    with database.connect() as connection:
        connection.execute(
            """
            INSERT INTO fj_smart_captures (
              id, source, status, search_config_json, execution_config_json,
              created_at, updated_at
            ) VALUES ('sc-independent', 'boss_capture', 'pending', '{}', '{}', ?, ?)
            """,
            (now, now),
        )
        connection.execute(
            """
            INSERT INTO fj_smart_captures (
              id, source, status, search_config_json, execution_config_json,
              created_at, updated_at
            ) VALUES ('sc-independent-2', 'boss_capture', 'pending', '{}', '{}', ?, ?)
            """,
            (now, now),
        )
        connection.execute(
            """
            INSERT INTO fj_boss_capture_batches (
              id, smart_capture_id, capture_source, keyword, city, created_at, updated_at
            ) VALUES ('batch-independent', 'sc-independent', 'smart', 'python', '上海', ?, ?)
            """,
            (now, now),
        )
        connection.execute(
            """
            INSERT INTO fj_boss_jobs (
              id, dedupe_key, source_job_id, title, company_name,
              first_collected_at, last_collected_at, latest_batch_id
            ) VALUES ('job-independent', 'dedupe-independent', 'source-independent',
                      '后端工程师', 'FineJob', ?, ?, 'batch-independent')
            """,
            (now, now),
        )
        connection.execute(
            """
            INSERT INTO fj_workflow_tasks (
              id, smart_capture_id, task_type, status, created_at, updated_at
            ) VALUES ('task-independent', 'sc-independent', 'deep_job_search_analysis',
                      'pending', ?, ?)
            """,
            (now, now),
        )
        connection.execute(
            """
            INSERT INTO fj_workflow_job_discoveries (
              id, smart_capture_id, task_id, job_id, search_keyword, city, discovered_at
            ) VALUES ('discovery-independent', 'sc-independent', 'task-independent',
                      'job-independent', 'python', '上海', ?)
            """,
            (now,),
        )
        connection.execute(
            """
            INSERT INTO fj_workflow_search_combinations (
              id, smart_capture_id, keyword, city, identity_json
            ) VALUES ('combination-independent', 'sc-independent', 'python', '上海',
                      '{"keyword":"python","city":"上海"}')
            """
        )
        connection.execute(
            """
            INSERT INTO fj_workflow_context_snapshots (
              id, smart_capture_id, channel, snapshot_json, created_at
            ) VALUES ('context-independent', 'sc-independent', 'candidate_analysis', '{}', ?)
            """,
            (now,),
        )
        connection.execute(
            """
            INSERT INTO fj_workflow_analysis_handoffs (
              smart_capture_id, analysis_batch_id, codex_session_ref, claimed_at
            ) VALUES ('sc-independent', 'analysis-independent', 'codex-independent', ?)
            """,
            (now,),
        )
        connection.execute(
            """
            INSERT INTO fj_workflow_prefetch_batches (
              id, smart_capture_id, source_analysis_batch_id, created_at, updated_at
            ) VALUES ('prefetch-independent', 'sc-independent', 'analysis-independent', ?, ?)
            """,
            (now, now),
        )
        connection.execute(
            """
            INSERT INTO fj_workflow_prefetch_items (
              id, smart_capture_id, prefetch_batch_id, job_id, created_at, updated_at
            ) VALUES ('prefetch-item-independent', 'sc-independent', 'prefetch-independent',
                      'job-independent', ?, ?)
            """,
            (now, now),
        )
        connection.execute(
            """
            INSERT INTO fj_workflow_candidate_reservations (
              id, smart_capture_id, job_id, owner_type, owner_id, created_at
            ) VALUES ('reservation-independent', 'sc-independent', 'job-independent',
                      'formal_analysis', 'analysis-independent', ?)
            """,
            (now,),
        )
        connection.execute(
            """
            INSERT INTO fj_workflow_evaluation_feedback (
              id, smart_capture_id, workflow_task_id, sentiment, created_at
            ) VALUES ('feedback-independent', 'sc-independent', 'task-independent', 'expected', ?)
            """,
            (now,),
        )
        connection.execute(
            """
            INSERT INTO fj_codex_sessions (
              id, smart_capture_id, analysis_batch_id, handoff_attempt_id, created_at, updated_at
            ) VALUES ('codex-independent', 'sc-independent', 'analysis-independent', 'attempt-1', ?, ?)
            """,
            (now, now),
        )
        codex_foreign_keys = connection.execute(
            "PRAGMA foreign_key_list(fj_codex_sessions)"
        ).fetchall()
        codex_indexes = {
            row["name"]
            for row in connection.execute(
                "PRAGMA index_list(fj_codex_sessions)"
            ).fetchall()
        }
        assert any(
            row["from"] == "smart_capture_id" and row["table"] == "fj_smart_captures"
            for row in codex_foreign_keys
        )
        assert "idx_fj_codex_sessions_updated_at" in codex_indexes

        owner_tables = [
            "fj_workflow_tasks",
            "fj_workflow_job_discoveries",
            "fj_workflow_search_combinations",
            "fj_workflow_context_snapshots",
            "fj_workflow_analysis_handoffs",
            "fj_workflow_prefetch_batches",
            "fj_workflow_prefetch_items",
            "fj_workflow_candidate_reservations",
            "fj_workflow_evaluation_feedback",
        ]
        for table_name in owner_tables:
            workflow_column = next(
                row
                for row in connection.execute(
                    f"PRAGMA table_info({table_name})"
                ).fetchall()
                if row["name"] == "workflow_run_id"
            )
            assert workflow_column["notnull"] == 0
            foreign_keys = connection.execute(
                f"PRAGMA foreign_key_list({table_name})"
            ).fetchall()
            assert {
                (row["from"], row["table"])
                for row in foreign_keys
            } >= {
                ("smart_capture_id", "fj_smart_captures"),
                ("workflow_run_id", "fj_workflow_runs"),
            }

        assert connection.execute(
            "SELECT COUNT(*) FROM fj_workflow_runs"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT workflow_run_id FROM fj_workflow_context_snapshots "
            "WHERE id = 'context-independent'"
        ).fetchone()[0] is None
        assert {
            "idx_fj_workflow_context_snapshots_smart_channel",
            "idx_fj_workflow_analysis_handoffs_smart_batch",
            "idx_fj_workflow_prefetch_batches_smart_source",
            "idx_fj_workflow_candidate_reservations_active",
        } <= {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            ).fetchall()
        }

        expected_index_columns = {
            "idx_fj_workflow_job_discoveries_smart_identity": (
                "smart_capture_id",
                "task_id",
                "job_id",
            ),
            "idx_fj_workflow_search_combinations_smart_identity": (
                "smart_capture_id",
                "identity_json",
            ),
            "idx_fj_workflow_context_snapshots_smart_channel": (
                "smart_capture_id",
                "channel",
            ),
            "idx_fj_workflow_analysis_handoffs_smart_batch": (
                "smart_capture_id",
                "analysis_batch_id",
            ),
            "idx_fj_workflow_prefetch_batches_smart_source": (
                "smart_capture_id",
                "source_analysis_batch_id",
            ),
            "idx_fj_workflow_prefetch_items_batch_job": (
                "prefetch_batch_id",
                "job_id",
            ),
            "idx_fj_workflow_candidate_reservations_owner_job": (
                "smart_capture_id",
                "owner_type",
                "owner_id",
                "job_id",
            ),
        }
        for index_name, expected_columns in expected_index_columns.items():
            index_columns = tuple(
                row["name"]
                for row in connection.execute(
                    f"PRAGMA index_info({index_name})"
                ).fetchall()
            )
            assert index_columns == expected_columns

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO fj_workflow_context_snapshots (
                  id, smart_capture_id, channel, created_at
                ) VALUES ('context-independent-duplicate', 'sc-independent',
                          'candidate_analysis', ?)
                """,
                (now,),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO fj_workflow_candidate_reservations (
                  id, smart_capture_id, job_id, owner_type, owner_id, created_at
                ) VALUES ('reservation-independent-duplicate', 'sc-independent-2',
                          'job-independent', 'formal_analysis', 'analysis-2', ?)
                """,
                (now,),
            )


def test_database_pipeline_owner_migration_backfills_linked_rows_and_is_repeatable(
    app_paths: dict[str, str],
) -> None:
    database = Database(app_paths["sqlite_path"])
    database.initialize()
    now = "2026-09-19T00:00:00Z"

    with database.connect() as connection:
        connection.execute(
            """
            INSERT INTO fj_workflow_runs (
              id, workflow_type, status, created_at, updated_at
            ) VALUES ('run-linked', 'deep_job_search', 'running', ?, ?)
            """,
            (now, now),
        )
        connection.execute(
            """
            INSERT INTO fj_smart_captures (
              id, source, workflow_run_id, status, search_config_json,
              execution_config_json, created_at, updated_at
            ) VALUES ('sc-linked', 'task_cockpit', 'run-linked', 'running', '{}', '{}', ?, ?)
            """,
            (now, now),
        )
        connection.execute(
            """
            INSERT INTO fj_workflow_runs (
              id, workflow_type, status, created_at, updated_at
            ) VALUES ('run-unbackfillable', 'deep_job_search', 'running', ?, ?)
            """,
            (now, now),
        )
        connection.execute(
            """
            INSERT INTO fj_workflow_context_snapshots (
              id, workflow_run_id, channel, created_at
            ) VALUES ('context-linked', 'run-linked', 'candidate_analysis', ?)
            """,
            (now,),
        )

    with database.connect() as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        for index in connection.execute(
            "PRAGMA index_list(fj_workflow_context_snapshots)"
        ).fetchall():
            index_name = index["name"]
            if not index_name.startswith("sqlite_autoindex_"):
                connection.execute(f'DROP INDEX "{index_name}"')
        connection.execute(
            "ALTER TABLE fj_workflow_context_snapshots "
            "RENAME TO fj_workflow_context_snapshots__legacy_fixture"
        )
        connection.execute(
            """
            CREATE TABLE fj_workflow_context_snapshots (
              id TEXT PRIMARY KEY,
              workflow_run_id TEXT NOT NULL,
              channel TEXT NOT NULL,
              snapshot_json TEXT NOT NULL DEFAULT '{}',
              context_characters INTEGER NOT NULL DEFAULT 0,
              estimated_tokens INTEGER NOT NULL DEFAULT 0,
              soft_budget_characters INTEGER NOT NULL DEFAULT 0,
              hard_budget_characters INTEGER NOT NULL DEFAULT 1000000,
              status TEXT NOT NULL DEFAULT 'ready',
              blocker_reason TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL,
              FOREIGN KEY (workflow_run_id) REFERENCES fj_workflow_runs(id) ON DELETE CASCADE,
              UNIQUE (workflow_run_id, channel),
              CHECK (status IN ('ready', 'blocked'))
            )
            """
        )
        connection.execute(
            """
            INSERT INTO fj_workflow_context_snapshots (
              id, workflow_run_id, channel, created_at
            ) VALUES ('context-linked', 'run-linked', 'candidate_analysis', ?)
            """,
            (now,),
        )
        connection.execute(
            """
            INSERT INTO fj_workflow_context_snapshots (
              id, workflow_run_id, channel, created_at
            ) VALUES ('context-unbackfillable', 'run-unbackfillable', 'candidate_analysis', ?)
            """,
            (now,),
        )
        connection.execute(
            "DROP TABLE fj_workflow_context_snapshots__legacy_fixture"
        )
        connection.execute("PRAGMA foreign_keys = ON")

    database.initialize()
    database.initialize()

    with database.connect() as connection:
        context = connection.execute(
            """
            SELECT smart_capture_id, workflow_run_id
            FROM fj_workflow_context_snapshots
            WHERE id = 'context-linked'
            """
        ).fetchone()
        unbackfillable = connection.execute(
            """
            SELECT smart_capture_id, workflow_run_id
            FROM fj_workflow_context_snapshots
            WHERE id = 'context-unbackfillable'
            """
        ).fetchone()
        foreign_key_errors = connection.execute(
            "PRAGMA foreign_key_check"
        ).fetchall()
        legacy_tables = connection.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type = 'table' AND name LIKE '%pipeline_owner_legacy%'
            """
        ).fetchall()
        assert context["smart_capture_id"] == "sc-linked"
        assert context["workflow_run_id"] == "run-linked"
        assert unbackfillable["smart_capture_id"] is None
        assert unbackfillable["workflow_run_id"] == "run-unbackfillable"
        assert foreign_key_errors == []
        assert legacy_tables == []


def test_database_rebuilds_partial_pipeline_owner_columns_when_owner_fk_is_missing(
    app_paths: dict[str, str],
) -> None:
    database = Database(app_paths["sqlite_path"])
    database.initialize()
    now = "2026-09-19T00:00:00Z"

    with database.connect() as connection:
        connection.execute(
            """
            INSERT INTO fj_workflow_runs (
              id, workflow_type, status, created_at, updated_at
            ) VALUES ('run-partial-owner', 'deep_job_search', 'running', ?, ?)
            """,
            (now, now),
        )
        connection.execute("PRAGMA foreign_keys = OFF")
        for index in connection.execute(
            "PRAGMA index_list(fj_workflow_context_snapshots)"
        ).fetchall():
            index_name = index["name"]
            if not index_name.startswith("sqlite_autoindex_"):
                connection.execute(f'DROP INDEX "{index_name}"')
        connection.execute(
            "ALTER TABLE fj_workflow_context_snapshots "
            "RENAME TO fj_workflow_context_snapshots__partial_owner"
        )
        connection.execute(
            """
            CREATE TABLE fj_workflow_context_snapshots (
              id TEXT PRIMARY KEY,
              workflow_run_id TEXT,
              smart_capture_id TEXT,
              channel TEXT NOT NULL,
              snapshot_json TEXT NOT NULL DEFAULT '{}',
              context_characters INTEGER NOT NULL DEFAULT 0,
              estimated_tokens INTEGER NOT NULL DEFAULT 0,
              soft_budget_characters INTEGER NOT NULL DEFAULT 0,
              hard_budget_characters INTEGER NOT NULL DEFAULT 1000000,
              status TEXT NOT NULL DEFAULT 'ready',
              blocker_reason TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL,
              FOREIGN KEY (workflow_run_id) REFERENCES fj_workflow_runs(id),
              CHECK (workflow_run_id IS NOT NULL OR smart_capture_id IS NOT NULL),
              CHECK (status IN ('ready', 'blocked'))
            )
            """
        )
        connection.execute(
            """
            INSERT INTO fj_workflow_context_snapshots (
              id, workflow_run_id, channel, created_at
            ) VALUES ('context-partial-owner', 'run-partial-owner', 'candidate_analysis', ?)
            """,
            (now,),
        )
        connection.execute("DROP TABLE fj_workflow_context_snapshots__partial_owner")
        connection.execute("PRAGMA foreign_keys = ON")

    database.initialize()

    with database.connect() as connection:
        foreign_keys = connection.execute(
            "PRAGMA foreign_key_list(fj_workflow_context_snapshots)"
        ).fetchall()
        context = connection.execute(
            """
            SELECT smart_capture_id, workflow_run_id
            FROM fj_workflow_context_snapshots
            WHERE id = 'context-partial-owner'
            """
        ).fetchone()

    assert {
        (row["from"], row["table"])
        for row in foreign_keys
    } >= {
        ("smart_capture_id", "fj_smart_captures"),
        ("workflow_run_id", "fj_workflow_runs"),
    }
    assert context["smart_capture_id"] is None
    assert context["workflow_run_id"] == "run-partial-owner"
