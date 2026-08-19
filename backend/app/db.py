from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


DDL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS knowledge_items (
  id TEXT PRIMARY KEY,
  source_type TEXT NOT NULL,
  source_value TEXT NOT NULL,
  title TEXT,
  raw_content TEXT,
  source_name TEXT NOT NULL,
  active_parse_result_id TEXT,
  capture_source TEXT,
  captured_at TEXT,
  capture_category TEXT,
  capture_tags_json TEXT NOT NULL DEFAULT '[]',
  user_tags_json TEXT NOT NULL DEFAULT '[]',
  ai_tags_json TEXT NOT NULL DEFAULT '[]',
  cleaning_level TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (source_type, source_value),
  CHECK (source_type IN ('url', 'pdf', 'markdown', 'text')),
  CHECK (capture_source IS NULL OR capture_source IN ('manual', 'screenshot_ocr')),
  CHECK (cleaning_level IS NULL OR cleaning_level IN ('basic', 'enhanced'))
);

CREATE INDEX IF NOT EXISTS idx_knowledge_items_source_type
  ON knowledge_items(source_type);
CREATE INDEX IF NOT EXISTS idx_knowledge_items_created_at
  ON knowledge_items(created_at);
CREATE INDEX IF NOT EXISTS idx_knowledge_items_title
  ON knowledge_items(title);

CREATE TABLE IF NOT EXISTS pool_entries (
  id TEXT PRIMARY KEY,
  knowledge_item_id TEXT NOT NULL UNIQUE,
  current_status TEXT NOT NULL,
  is_deleted INTEGER NOT NULL DEFAULT 0,
  added_at TEXT NOT NULL,
  last_summarized_at TEXT,
  last_summary_status TEXT,
  last_failed_category TEXT,
  last_failed_message TEXT,
  was_resummarized INTEGER NOT NULL DEFAULT 0,
  display_updated_at TEXT NOT NULL,
  FOREIGN KEY (knowledge_item_id) REFERENCES knowledge_items(id),
  CHECK (current_status IN ('pending', 'running', 'succeeded', 'failed')),
  CHECK (is_deleted IN (0, 1)),
  CHECK (was_resummarized IN (0, 1))
);

CREATE INDEX IF NOT EXISTS idx_pool_entries_current_status
  ON pool_entries(current_status);
CREATE INDEX IF NOT EXISTS idx_pool_entries_is_deleted
  ON pool_entries(is_deleted);
CREATE INDEX IF NOT EXISTS idx_pool_entries_display_updated_at
  ON pool_entries(display_updated_at);

CREATE TABLE IF NOT EXISTS run_records (
  id TEXT PRIMARY KEY,
  task_type TEXT NOT NULL,
  status TEXT NOT NULL,
  stage TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  total_items INTEGER NOT NULL DEFAULT 0,
  succeeded_items INTEGER NOT NULL DEFAULT 0,
  failed_items INTEGER NOT NULL DEFAULT 0,
  skipped_items INTEGER NOT NULL DEFAULT 0,
  current_item_id TEXT,
  current_item_label TEXT,
  error_category TEXT,
  error_message TEXT,
  report_week_key TEXT,
  linked_report_version_id TEXT,
  executor_type TEXT NOT NULL DEFAULT 'llm',
  executor_version TEXT,
  model_name TEXT,
  reasoning_effort TEXT,
  cancel_requested INTEGER NOT NULL DEFAULT 0,
  CHECK (task_type IN ('summary', 'report')),
  CHECK (status IN ('pending', 'running', 'completed', 'failed', 'cancelled')),
  CHECK (cancel_requested IN (0, 1)),
  CHECK (report_week_key IS NULL OR report_week_key GLOB '[0-9][0-9][0-9][0-9]-W[0-9][0-9]')
);

CREATE INDEX IF NOT EXISTS idx_run_records_task_type_started_at
  ON run_records(task_type, started_at);
CREATE INDEX IF NOT EXISTS idx_run_records_status
  ON run_records(status);
CREATE INDEX IF NOT EXISTS idx_run_records_report_week_key
  ON run_records(report_week_key);

CREATE TABLE IF NOT EXISTS item_result_snapshots (
  id TEXT PRIMARY KEY,
  knowledge_item_id TEXT NOT NULL,
  summary_run_id TEXT NOT NULL,
  generated_category TEXT,
  generated_tags TEXT,
  final_category TEXT,
  final_tags TEXT,
  summary_text TEXT NOT NULL,
  viewpoint_text TEXT,
  controversy_text TEXT,
  content_quality_score REAL NOT NULL DEFAULT 0,
  quality_meta TEXT,
  relation_meta TEXT,
  qdrant_point_id TEXT NOT NULL UNIQUE,
  markdown_path TEXT,
  created_at TEXT NOT NULL,
  edited_at TEXT NOT NULL,
  FOREIGN KEY (knowledge_item_id) REFERENCES knowledge_items(id),
  FOREIGN KEY (summary_run_id) REFERENCES run_records(id),
  UNIQUE (knowledge_item_id, summary_run_id)
);

CREATE INDEX IF NOT EXISTS idx_item_result_snapshots_summary_run_id
  ON item_result_snapshots(summary_run_id);
CREATE INDEX IF NOT EXISTS idx_item_result_snapshots_final_category
  ON item_result_snapshots(final_category);

CREATE TABLE IF NOT EXISTS document_chunks (
  id TEXT PRIMARY KEY,
  knowledge_item_id TEXT NOT NULL,
  parent_chunk_id TEXT,
  chunk_level TEXT NOT NULL,
  section_title TEXT,
  content TEXT NOT NULL,
  position INTEGER NOT NULL,
  token_estimate INTEGER NOT NULL DEFAULT 0,
  embedding_provider TEXT,
  embedding_model TEXT,
  vector_point_id TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY (knowledge_item_id) REFERENCES knowledge_items(id) ON DELETE CASCADE,
  FOREIGN KEY (parent_chunk_id) REFERENCES document_chunks(id) ON DELETE CASCADE,
  CHECK (chunk_level IN ('parent', 'child'))
);

CREATE INDEX IF NOT EXISTS idx_document_chunks_knowledge_item_position
  ON document_chunks(knowledge_item_id, position);
CREATE INDEX IF NOT EXISTS idx_document_chunks_knowledge_item_level
  ON document_chunks(knowledge_item_id, chunk_level);
CREATE INDEX IF NOT EXISTS idx_document_chunks_parent_chunk_id
  ON document_chunks(parent_chunk_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_document_chunks_vector_point_id
  ON document_chunks(vector_point_id)
  WHERE vector_point_id IS NOT NULL;

CREATE VIRTUAL TABLE IF NOT EXISTS document_chunks_fts USING fts5(
  chunk_id UNINDEXED,
  knowledge_item_id UNINDEXED,
  parent_chunk_id UNINDEXED,
  title,
  section_title,
  content,
  lexical_terms,
  tokenize = 'unicode61'
);

CREATE TABLE IF NOT EXISTS retrieval_index_versions (
  id TEXT PRIMARY KEY,
  index_scope TEXT NOT NULL,
  version_tag TEXT NOT NULL,
  collection_name TEXT NOT NULL UNIQUE,
  embedding_provider TEXT NOT NULL,
  embedding_model TEXT NOT NULL,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  activated_at TEXT,
  last_rebuilt_at TEXT,
  last_rebuild_chunk_count INTEGER NOT NULL DEFAULT 0,
  CHECK (index_scope IN ('chunk')),
  CHECK (status IN ('candidate', 'active', 'retired')),
  UNIQUE (index_scope, embedding_provider, embedding_model, version_tag)
);

CREATE INDEX IF NOT EXISTS idx_retrieval_index_versions_scope_provider_model
  ON retrieval_index_versions(index_scope, embedding_provider, embedding_model);
CREATE INDEX IF NOT EXISTS idx_retrieval_index_versions_status
  ON retrieval_index_versions(status);

CREATE TABLE IF NOT EXISTS document_parse_results (
  id TEXT PRIMARY KEY,
  knowledge_item_id TEXT NOT NULL,
  parser_name TEXT NOT NULL,
  status TEXT NOT NULL,
  raw_text TEXT NOT NULL,
  markdown_text TEXT,
  preview_text TEXT NOT NULL,
  page_count INTEGER NOT NULL DEFAULT 0,
  char_count INTEGER NOT NULL DEFAULT 0,
  quality_score REAL NOT NULL DEFAULT 0,
  is_ocr INTEGER NOT NULL DEFAULT 0,
  warnings_json TEXT NOT NULL DEFAULT '[]',
  fallback_from TEXT,
  fallback_reason TEXT,
  created_at TEXT NOT NULL,
  saved_at TEXT,
  FOREIGN KEY (knowledge_item_id) REFERENCES knowledge_items(id) ON DELETE CASCADE,
  CHECK (status IN ('preview', 'saved')),
  CHECK (is_ocr IN (0, 1))
);

CREATE INDEX IF NOT EXISTS idx_document_parse_results_knowledge_item_created_at
  ON document_parse_results(knowledge_item_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_document_parse_results_saved_at
  ON document_parse_results(saved_at);

CREATE TABLE IF NOT EXISTS weekly_report_versions (
  id TEXT PRIMARY KEY,
  week_key TEXT NOT NULL,
  version INTEGER NOT NULL,
  report_run_id TEXT NOT NULL,
  markdown_content TEXT NOT NULL,
  snapshot_payload TEXT NOT NULL,
  markdown_path TEXT,
  item_count INTEGER NOT NULL DEFAULT 0,
  generated_at TEXT NOT NULL,
  FOREIGN KEY (report_run_id) REFERENCES run_records(id),
  UNIQUE (week_key, version),
  CHECK (version > 0),
  CHECK (week_key GLOB '[0-9][0-9][0-9][0-9]-W[0-9][0-9]')
);

CREATE INDEX IF NOT EXISTS idx_weekly_report_versions_week_key
  ON weekly_report_versions(week_key);
CREATE INDEX IF NOT EXISTS idx_weekly_report_versions_generated_at
  ON weekly_report_versions(generated_at);

CREATE TABLE IF NOT EXISTS summary_feedback (
  id TEXT PRIMARY KEY,
  result_snapshot_id TEXT NOT NULL UNIQUE,
  feedback_value TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (result_snapshot_id) REFERENCES item_result_snapshots(id) ON DELETE CASCADE,
  CHECK (feedback_value IN ('useful', 'useless'))
);

CREATE INDEX IF NOT EXISTS idx_summary_feedback_feedback_value
  ON summary_feedback(feedback_value);

CREATE TABLE IF NOT EXISTS qa_sessions (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  mode TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  last_question TEXT,
  CHECK (mode IN ('answer', 'knowledge_point', 'summary', 'source'))
);

CREATE INDEX IF NOT EXISTS idx_qa_sessions_updated_at
  ON qa_sessions(updated_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS qa_messages (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  question TEXT,
  rewritten_question TEXT,
  rewrite_meta_json TEXT NOT NULL DEFAULT '{}',
  answer_status TEXT,
  confidence REAL,
  verification_json TEXT NOT NULL DEFAULT '{}',
  retry_count INTEGER NOT NULL DEFAULT 0,
  applied_filters_json TEXT NOT NULL DEFAULT '{}',
  citations_json TEXT NOT NULL DEFAULT '[]',
  used_grounded_items_json TEXT NOT NULL DEFAULT '[]',
  suggested_queries_json TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL,
  FOREIGN KEY (session_id) REFERENCES qa_sessions(id) ON DELETE CASCADE,
  CHECK (role IN ('user', 'assistant')),
  CHECK (answer_status IS NULL OR answer_status IN ('grounded', 'insufficient_evidence', 'needs_clarification'))
);

CREATE INDEX IF NOT EXISTS idx_qa_messages_session_created_at
  ON qa_messages(session_id, created_at ASC, id ASC);

CREATE TABLE IF NOT EXISTS fj_resumes (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  file_path TEXT NOT NULL,
  file_hash TEXT NOT NULL,
  parser_name TEXT NOT NULL,
  raw_text TEXT NOT NULL,
  markdown_text TEXT,
  preview_text TEXT NOT NULL,
  page_count INTEGER NOT NULL DEFAULT 0,
  char_count INTEGER NOT NULL DEFAULT 0,
  quality_score REAL NOT NULL DEFAULT 0,
  is_ocr INTEGER NOT NULL DEFAULT 0,
  warnings_json TEXT NOT NULL DEFAULT '[]',
  fallback_from TEXT,
  fallback_reason TEXT,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  CHECK (status IN ('parsed', 'failed')),
  CHECK (is_ocr IN (0, 1))
);

CREATE INDEX IF NOT EXISTS idx_fj_resumes_created_at
  ON fj_resumes(created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_fj_resumes_file_hash
  ON fj_resumes(file_hash);

CREATE TABLE IF NOT EXISTS fj_resume_facts (
  id TEXT PRIMARY KEY,
  resume_id TEXT NOT NULL,
  fact_type TEXT NOT NULL,
  fact_key TEXT NOT NULL,
  fact_value TEXT NOT NULL,
  confidence REAL NOT NULL DEFAULT 0,
  source_text TEXT,
  user_confirmed INTEGER NOT NULL DEFAULT 0,
  sensitive INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (resume_id) REFERENCES fj_resumes(id) ON DELETE CASCADE,
  CHECK (user_confirmed IN (0, 1)),
  CHECK (sensitive IN (0, 1))
);

CREATE INDEX IF NOT EXISTS idx_fj_resume_facts_resume_id
  ON fj_resume_facts(resume_id);

CREATE TABLE IF NOT EXISTS fj_job_intents (
  id TEXT PRIMARY KEY,
  target_title TEXT NOT NULL DEFAULT '',
  cities_json TEXT NOT NULL DEFAULT '[]',
  keywords_json TEXT NOT NULL DEFAULT '[]',
  expanded_keywords_json TEXT NOT NULL DEFAULT '[]',
  excluded_keywords_json TEXT NOT NULL DEFAULT '[]',
  salary_min INTEGER,
  salary_max INTEGER,
  work_mode TEXT NOT NULL DEFAULT 'any',
  notes TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  CHECK (work_mode IN ('any', 'onsite', 'hybrid', 'remote'))
);

CREATE TABLE IF NOT EXISTS fj_platform_sessions (
  platform TEXT PRIMARY KEY,
  display_name TEXT NOT NULL,
  login_url TEXT NOT NULL DEFAULT '',
  browser_profile TEXT NOT NULL DEFAULT '',
  browser_channel TEXT NOT NULL DEFAULT 'chrome',
  profile_mode TEXT NOT NULL DEFAULT 'existing',
  profile_path TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'needs_login',
  status_detail TEXT NOT NULL DEFAULT '',
  last_checked_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  CHECK (platform IN ('boss')),
  CHECK (browser_channel IN ('chrome', 'msedge')),
  CHECK (profile_mode IN ('existing', 'managed')),
  CHECK (status IN ('ready', 'needs_login', 'invalid'))
);

CREATE TABLE IF NOT EXISTS fj_delivery_strategies (
  id TEXT PRIMARY KEY,
  automation_level TEXT NOT NULL DEFAULT 'assist',
  auto_greeting_enabled INTEGER NOT NULL DEFAULT 0,
  daily_greeting_limit INTEGER NOT NULL DEFAULT 20,
  hourly_greeting_limit INTEGER NOT NULL DEFAULT 5,
  min_match_score REAL NOT NULL DEFAULT 0.72,
  resume_submit_mode TEXT NOT NULL DEFAULT 'manual',
  contact_share_mode TEXT NOT NULL DEFAULT 'manual',
  interview_accept_mode TEXT NOT NULL DEFAULT 'manual',
  only_online_interview INTEGER NOT NULL DEFAULT 0,
  pause_on_risk INTEGER NOT NULL DEFAULT 1,
  notes TEXT NOT NULL DEFAULT '',
  confirmed_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  CHECK (automation_level IN ('assist', 'semi_auto', 'auto_greeting')),
  CHECK (resume_submit_mode IN ('manual', 'auto_on_invite')),
  CHECK (contact_share_mode IN ('manual', 'auto_after_match')),
  CHECK (interview_accept_mode IN ('manual', 'auto_in_selected_slots')),
  CHECK (auto_greeting_enabled IN (0, 1)),
  CHECK (only_online_interview IN (0, 1)),
  CHECK (pause_on_risk IN (0, 1))
);

CREATE TABLE IF NOT EXISTS fj_job_filter_strategies (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  search_keywords_json TEXT NOT NULL DEFAULT '[]',
  cities_json TEXT NOT NULL DEFAULT '[]',
  title_include_any_json TEXT NOT NULL DEFAULT '[]',
  title_include_all_json TEXT NOT NULL DEFAULT '[]',
  title_exclude_json TEXT NOT NULL DEFAULT '[]',
  company_include_json TEXT NOT NULL DEFAULT '[]',
  company_exclude_json TEXT NOT NULL DEFAULT '[]',
  company_scales_json TEXT NOT NULL DEFAULT '[]',
  company_industries_json TEXT NOT NULL DEFAULT '[]',
  company_stages_json TEXT NOT NULL DEFAULT '[]',
  degrees_json TEXT NOT NULL DEFAULT '[]',
  experiences_json TEXT NOT NULL DEFAULT '[]',
  job_types_json TEXT NOT NULL DEFAULT '[]',
  monthly_salary_min INTEGER,
  monthly_salary_max_at_least INTEGER,
  daily_salary_min INTEGER,
  skill_include_any_json TEXT NOT NULL DEFAULT '[]',
  skill_include_all_json TEXT NOT NULL DEFAULT '[]',
  skill_exclude_json TEXT NOT NULL DEFAULT '[]',
  boss_active_statuses_json TEXT NOT NULL DEFAULT '[]',
  unknown_value_policy TEXT NOT NULL DEFAULT 'review',
  notes TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  CHECK (enabled IN (0, 1)),
  CHECK (unknown_value_policy IN ('keep', 'review', 'exclude'))
);

CREATE INDEX IF NOT EXISTS idx_fj_job_filter_strategies_updated_at
  ON fj_job_filter_strategies(updated_at DESC);

CREATE TABLE IF NOT EXISTS fj_job_recommendation_strategies (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  filter_strategy_id TEXT,
  resume_id TEXT,
  evaluation_method TEXT NOT NULL DEFAULT 'hybrid',
  desired_responsibilities_json TEXT NOT NULL DEFAULT '[]',
  required_skills_json TEXT NOT NULL DEFAULT '[]',
  preferred_skills_json TEXT NOT NULL DEFAULT '[]',
  excluded_terms_json TEXT NOT NULL DEFAULT '[]',
  preferred_industries_json TEXT NOT NULL DEFAULT '[]',
  work_preferences TEXT NOT NULL DEFAULT '',
  risk_notes TEXT NOT NULL DEFAULT '',
  minimum_confidence REAL NOT NULL DEFAULT 0.7,
  insufficient_info_action TEXT NOT NULL DEFAULT 'review',
  notes TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (filter_strategy_id) REFERENCES fj_job_filter_strategies(id) ON DELETE SET NULL,
  FOREIGN KEY (resume_id) REFERENCES fj_resumes(id) ON DELETE SET NULL,
  CHECK (enabled IN (0, 1)),
  CHECK (evaluation_method IN ('rules', 'llm', 'hybrid')),
  CHECK (insufficient_info_action IN ('review', 'reject'))
);

CREATE INDEX IF NOT EXISTS idx_fj_job_recommendation_strategies_updated_at
  ON fj_job_recommendation_strategies(updated_at DESC);

CREATE TABLE IF NOT EXISTS fj_delivery_runs (
  id TEXT PRIMARY KEY,
  mode TEXT NOT NULL DEFAULT 'dry_run',
  status TEXT NOT NULL,
  stage TEXT NOT NULL,
  intent_snapshot_json TEXT NOT NULL DEFAULT '{}',
  strategy_snapshot_json TEXT NOT NULL DEFAULT '{}',
  searched_count INTEGER NOT NULL DEFAULT 0,
  skipped_count INTEGER NOT NULL DEFAULT 0,
  greeted_count INTEGER NOT NULL DEFAULT 0,
  error_count INTEGER NOT NULL DEFAULT 0,
  started_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  finished_at TEXT,
  error_message TEXT,
  CHECK (mode IN ('dry_run', 'live')),
  CHECK (status IN ('pending', 'running', 'completed', 'failed', 'paused', 'cancelled'))
);

CREATE INDEX IF NOT EXISTS idx_fj_delivery_runs_started_at
  ON fj_delivery_runs(started_at DESC);

CREATE TABLE IF NOT EXISTS fj_delivery_candidates (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  platform TEXT NOT NULL DEFAULT 'boss',
  keyword TEXT NOT NULL,
  city TEXT NOT NULL,
  job_url TEXT NOT NULL DEFAULT '',
  job_title TEXT NOT NULL DEFAULT '',
  company_name TEXT NOT NULL DEFAULT '',
  salary_text TEXT NOT NULL DEFAULT '',
  location_text TEXT NOT NULL DEFAULT '',
  experience_text TEXT NOT NULL DEFAULT '',
  education_text TEXT NOT NULL DEFAULT '',
  hr_active_text TEXT NOT NULL DEFAULT '',
  jd_text TEXT NOT NULL DEFAULT '',
  match_score REAL,
  decision TEXT NOT NULL DEFAULT 'pending',
  reason TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (run_id) REFERENCES fj_delivery_runs(id) ON DELETE CASCADE,
  CHECK (decision IN ('pending', 'would_greet', 'skipped', 'needs_review'))
);

CREATE INDEX IF NOT EXISTS idx_fj_delivery_candidates_run_id
  ON fj_delivery_candidates(run_id);

CREATE TABLE IF NOT EXISTS fj_boss_capture_batches (
  id TEXT PRIMARY KEY,
  keyword TEXT NOT NULL,
  city TEXT NOT NULL,
  pages INTEGER NOT NULL DEFAULT 1,
  auto_details INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'queued',
  source_url TEXT,
  jobs_collected INTEGER NOT NULL DEFAULT 0,
  details_completed INTEGER NOT NULL DEFAULT 0,
  details_failed INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  finished_at TEXT,
  CHECK (auto_details IN (0, 1)),
  CHECK (status IN ('queued', 'running', 'completed', 'failed'))
);

CREATE INDEX IF NOT EXISTS idx_fj_boss_capture_batches_created_at
  ON fj_boss_capture_batches(created_at DESC);

CREATE TABLE IF NOT EXISTS fj_boss_jobs (
  id TEXT PRIMARY KEY,
  dedupe_key TEXT NOT NULL UNIQUE,
  source_job_id TEXT NOT NULL DEFAULT '',
  encrypt_job_id TEXT NOT NULL DEFAULT '',
  title TEXT NOT NULL DEFAULT '',
  company_name TEXT NOT NULL DEFAULT '',
  company_scale TEXT NOT NULL DEFAULT '',
  company_stage TEXT NOT NULL DEFAULT '',
  company_industry TEXT NOT NULL DEFAULT '',
  welfare TEXT NOT NULL DEFAULT '',
  salary TEXT NOT NULL DEFAULT '',
  location TEXT NOT NULL DEFAULT '',
  experience TEXT NOT NULL DEFAULT '',
  degree TEXT NOT NULL DEFAULT '',
  boss_active_status TEXT NOT NULL DEFAULT '',
  job_link TEXT NOT NULL DEFAULT '',
  tags TEXT NOT NULL DEFAULT '',
  skills TEXT NOT NULL DEFAULT '',
  job_labels TEXT NOT NULL DEFAULT '',
  payload_json TEXT NOT NULL DEFAULT '{}',
  detail_json TEXT,
  detail_status TEXT NOT NULL DEFAULT 'not_collected',
  detail_error TEXT,
  detail_collected_at TEXT,
  first_collected_at TEXT NOT NULL,
  last_collected_at TEXT NOT NULL,
  collect_count INTEGER NOT NULL DEFAULT 1,
  latest_batch_id TEXT NOT NULL,
  FOREIGN KEY (latest_batch_id) REFERENCES fj_boss_capture_batches(id),
  CHECK (detail_status IN ('not_collected', 'queued', 'collecting', 'completed', 'failed')),
  CHECK (collect_count > 0)
);

CREATE INDEX IF NOT EXISTS idx_fj_boss_jobs_last_collected_at
  ON fj_boss_jobs(last_collected_at DESC);
CREATE INDEX IF NOT EXISTS idx_fj_boss_jobs_title
  ON fj_boss_jobs(title);
CREATE INDEX IF NOT EXISTS idx_fj_boss_jobs_company_name
  ON fj_boss_jobs(company_name);
CREATE INDEX IF NOT EXISTS idx_fj_boss_jobs_location
  ON fj_boss_jobs(location);

CREATE TABLE IF NOT EXISTS fj_boss_capture_batch_jobs (
  capture_id TEXT NOT NULL,
  job_id TEXT NOT NULL,
  collected_at TEXT NOT NULL,
  was_previously_collected INTEGER NOT NULL DEFAULT 0,
  snapshot_json TEXT NOT NULL DEFAULT '{}',
  PRIMARY KEY (capture_id, job_id),
  FOREIGN KEY (capture_id) REFERENCES fj_boss_capture_batches(id) ON DELETE CASCADE,
  FOREIGN KEY (job_id) REFERENCES fj_boss_jobs(id) ON DELETE CASCADE,
  CHECK (was_previously_collected IN (0, 1))
);

CREATE INDEX IF NOT EXISTS idx_fj_boss_capture_batch_jobs_job_id
  ON fj_boss_capture_batch_jobs(job_id, collected_at DESC);

CREATE TABLE IF NOT EXISTS fj_action_logs (
  id TEXT PRIMARY KEY,
  run_id TEXT,
  level TEXT NOT NULL DEFAULT 'info',
  action_type TEXT NOT NULL,
  message TEXT NOT NULL,
  detail_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  FOREIGN KEY (run_id) REFERENCES fj_delivery_runs(id) ON DELETE CASCADE,
  CHECK (level IN ('info', 'warning', 'error'))
);

CREATE INDEX IF NOT EXISTS idx_fj_action_logs_run_created_at
  ON fj_action_logs(run_id, created_at DESC);
"""


class Database:
    def __init__(self, sqlite_path: Path) -> None:
        self.sqlite_path = sqlite_path

    def initialize(self) -> None:
        self.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(DDL)
            self._ensure_knowledge_items_parse_column(connection)
            self._ensure_knowledge_items_capture_columns(connection)
            self._ensure_knowledge_items_tag_columns(connection)
            self._ensure_knowledge_items_cleaning_column(connection)
            self._ensure_retrieval_index_version_columns(connection)
            self._ensure_document_chunk_fts_index(connection)
            self._ensure_qa_message_trace_columns(connection)
            self._ensure_run_record_executor_columns(connection)
            self._ensure_fj_platform_session_columns(connection)
            self._ensure_fj_boss_job_columns(connection)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.sqlite_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON;")
        # `DELETE` journal mode fails in this workspace sandbox because SQLite
        # cannot remove the rollback journal file reliably. `TRUNCATE` keeps
        # file-backed rollback semantics while avoiding that unlink step.
        connection.execute("PRAGMA journal_mode = TRUNCATE;")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _ensure_knowledge_items_parse_column(
        self,
        connection: sqlite3.Connection,
    ) -> None:
        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(knowledge_items)")
        }
        if "active_parse_result_id" in columns:
            return
        connection.execute(
            "ALTER TABLE knowledge_items ADD COLUMN active_parse_result_id TEXT"
        )

    def _ensure_knowledge_items_capture_columns(
        self,
        connection: sqlite3.Connection,
    ) -> None:
        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(knowledge_items)")
        }
        if "capture_source" not in columns:
            connection.execute("ALTER TABLE knowledge_items ADD COLUMN capture_source TEXT")
        if "captured_at" not in columns:
            connection.execute("ALTER TABLE knowledge_items ADD COLUMN captured_at TEXT")
        if "capture_category" not in columns:
            connection.execute("ALTER TABLE knowledge_items ADD COLUMN capture_category TEXT")
        if "capture_tags_json" not in columns:
            connection.execute(
                "ALTER TABLE knowledge_items ADD COLUMN capture_tags_json TEXT NOT NULL DEFAULT '[]'"
            )

    def _ensure_knowledge_items_tag_columns(
        self,
        connection: sqlite3.Connection,
    ) -> None:
        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(knowledge_items)")
        }
        if "user_tags_json" not in columns:
            connection.execute(
                "ALTER TABLE knowledge_items ADD COLUMN user_tags_json TEXT NOT NULL DEFAULT '[]'"
            )
        if "ai_tags_json" not in columns:
            connection.execute(
                "ALTER TABLE knowledge_items ADD COLUMN ai_tags_json TEXT NOT NULL DEFAULT '[]'"
            )

    def _ensure_knowledge_items_cleaning_column(
        self,
        connection: sqlite3.Connection,
    ) -> None:
        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(knowledge_items)")
        }
        if "cleaning_level" in columns:
            return
        connection.execute("ALTER TABLE knowledge_items ADD COLUMN cleaning_level TEXT")

    def _ensure_document_chunk_fts_index(
        self,
        connection: sqlite3.Connection,
    ) -> None:
        fts_count = int(connection.execute("SELECT COUNT(*) FROM document_chunks_fts").fetchone()[0])
        if fts_count > 0:
            return
        chunk_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM document_chunks WHERE chunk_level = 'child'"
            ).fetchone()[0]
        )
        if chunk_count == 0:
            return
        from backend.app.services.chunk_store import rebuild_document_chunk_fts_index

        rebuild_document_chunk_fts_index(connection)

    def _ensure_retrieval_index_version_columns(
        self,
        connection: sqlite3.Connection,
    ) -> None:
        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(retrieval_index_versions)")
        }
        if "last_rebuilt_at" not in columns:
            connection.execute("ALTER TABLE retrieval_index_versions ADD COLUMN last_rebuilt_at TEXT")
        if "last_rebuild_chunk_count" not in columns:
            connection.execute(
                "ALTER TABLE retrieval_index_versions ADD COLUMN last_rebuild_chunk_count INTEGER NOT NULL DEFAULT 0"
            )

    def _ensure_qa_message_trace_columns(
        self,
        connection: sqlite3.Connection,
    ) -> None:
        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(qa_messages)")
        }
        if "rewrite_meta_json" not in columns:
            connection.execute(
                "ALTER TABLE qa_messages ADD COLUMN rewrite_meta_json TEXT NOT NULL DEFAULT '{}'"
            )
        if "verification_json" not in columns:
            connection.execute(
                "ALTER TABLE qa_messages ADD COLUMN verification_json TEXT NOT NULL DEFAULT '{}'"
            )
        if "retry_count" not in columns:
            connection.execute(
                "ALTER TABLE qa_messages ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0"
            )

    def _ensure_run_record_executor_columns(
        self,
        connection: sqlite3.Connection,
    ) -> None:
        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(run_records)")
        }
        migrations = {
            "executor_type": (
                "ALTER TABLE run_records ADD COLUMN executor_type TEXT NOT NULL DEFAULT 'llm'"
            ),
            "executor_version": "ALTER TABLE run_records ADD COLUMN executor_version TEXT",
            "model_name": "ALTER TABLE run_records ADD COLUMN model_name TEXT",
            "reasoning_effort": "ALTER TABLE run_records ADD COLUMN reasoning_effort TEXT",
        }
        for column, ddl in migrations.items():
            if column not in columns:
                connection.execute(ddl)

    def _ensure_fj_platform_session_columns(
        self,
        connection: sqlite3.Connection,
    ) -> None:
        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(fj_platform_sessions)")
        }
        if "browser_channel" not in columns:
            connection.execute(
                "ALTER TABLE fj_platform_sessions ADD COLUMN browser_channel TEXT NOT NULL DEFAULT 'chrome'"
            )
        if "profile_mode" not in columns:
            connection.execute(
                "ALTER TABLE fj_platform_sessions ADD COLUMN profile_mode TEXT NOT NULL DEFAULT 'existing'"
            )
        if "profile_path" not in columns:
            connection.execute(
                "ALTER TABLE fj_platform_sessions ADD COLUMN profile_path TEXT NOT NULL DEFAULT ''"
            )

        candidate_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(fj_delivery_candidates)")
        }
        for column, ddl in {
            "job_url": "ALTER TABLE fj_delivery_candidates ADD COLUMN job_url TEXT NOT NULL DEFAULT ''",
            "salary_text": "ALTER TABLE fj_delivery_candidates ADD COLUMN salary_text TEXT NOT NULL DEFAULT ''",
            "location_text": "ALTER TABLE fj_delivery_candidates ADD COLUMN location_text TEXT NOT NULL DEFAULT ''",
            "experience_text": "ALTER TABLE fj_delivery_candidates ADD COLUMN experience_text TEXT NOT NULL DEFAULT ''",
            "education_text": "ALTER TABLE fj_delivery_candidates ADD COLUMN education_text TEXT NOT NULL DEFAULT ''",
            "hr_active_text": "ALTER TABLE fj_delivery_candidates ADD COLUMN hr_active_text TEXT NOT NULL DEFAULT ''",
        }.items():
            if column not in candidate_columns:
                connection.execute(ddl)

    def _ensure_fj_boss_job_columns(
        self,
        connection: sqlite3.Connection,
    ) -> None:
        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(fj_boss_jobs)")
        }
        migrations = {
            "company_stage": (
                "ALTER TABLE fj_boss_jobs ADD COLUMN company_stage TEXT NOT NULL DEFAULT ''"
            ),
            "company_industry": (
                "ALTER TABLE fj_boss_jobs ADD COLUMN company_industry TEXT NOT NULL DEFAULT ''"
            ),
            "welfare": "ALTER TABLE fj_boss_jobs ADD COLUMN welfare TEXT NOT NULL DEFAULT ''",
        }
        for column, ddl in migrations.items():
            if column not in columns:
                connection.execute(ddl)

        # 旧岗位已经保留完整 payload；迁移时回填正式列，避免要求用户重新采集。
        rows = connection.execute(
            """
            SELECT id, payload_json, company_stage, company_industry, welfare
            FROM fj_boss_jobs
            WHERE company_stage = '' OR company_industry = '' OR welfare = ''
            """
        ).fetchall()
        for row in rows:
            try:
                payload = json.loads(row["payload_json"] or "{}")
            except json.JSONDecodeError:
                payload = {}
            connection.execute(
                """
                UPDATE fj_boss_jobs
                SET company_stage = ?, company_industry = ?, welfare = ?
                WHERE id = ?
                """,
                (
                    row["company_stage"] or str(payload.get("company_stage") or ""),
                    row["company_industry"] or str(payload.get("company_industry") or ""),
                    row["welfare"] or str(payload.get("welfare") or ""),
                    row["id"],
                ),
            )
