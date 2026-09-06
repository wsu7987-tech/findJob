from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from uuid import uuid4


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

CREATE TABLE IF NOT EXISTS fj_candidate_profiles (
  id TEXT PRIMARY KEY,
  display_name TEXT NOT NULL DEFAULT '默认候选人',
  status TEXT NOT NULL DEFAULT 'draft',
  sources_version INTEGER NOT NULL DEFAULT 1,
  facts_version INTEGER NOT NULL DEFAULT 1,
  questions_version INTEGER NOT NULL DEFAULT 1,
  answers_version INTEGER NOT NULL DEFAULT 1,
  strategy_version INTEGER NOT NULL DEFAULT 1,
  context_version INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  CHECK (status IN ('draft', 'ready', 'stale', 'archived'))
);

CREATE TABLE IF NOT EXISTS fj_profile_sources (
  id TEXT PRIMARY KEY,
  profile_id TEXT NOT NULL,
  resume_family_id TEXT,
  source_type TEXT NOT NULL,
  title TEXT NOT NULL,
  file_path TEXT,
  raw_text TEXT NOT NULL DEFAULT '',
  recognized_text TEXT NOT NULL DEFAULT '',
  editable_text TEXT NOT NULL DEFAULT '',
  recognizer_name TEXT,
  status TEXT NOT NULL DEFAULT 'uploaded',
  active_analysis_run_id TEXT,
  enabled INTEGER NOT NULL DEFAULT 1,
  source_version INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (profile_id) REFERENCES fj_candidate_profiles(id) ON DELETE CASCADE,
  CHECK (source_type IN ('pdf', 'markdown', 'text', 'project')),
  CHECK (status IN ('uploaded', 'recognizing', 'analyzing', 'ready', 'review_required', 'failed', 'archived')),
  CHECK (enabled IN (0, 1))
);

CREATE INDEX IF NOT EXISTS idx_fj_profile_sources_profile
  ON fj_profile_sources(profile_id, enabled, updated_at DESC);

CREATE TABLE IF NOT EXISTS fj_resume_families (
  id TEXT PRIMARY KEY,
  profile_id TEXT NOT NULL,
  name TEXT NOT NULL,
  root_source_id TEXT,
  target_role_family TEXT NOT NULL DEFAULT '',
  base_version_id TEXT,
  default_version_id TEXT,
  content_version INTEGER NOT NULL DEFAULT 1,
  analysis_version INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'active',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (profile_id) REFERENCES fj_candidate_profiles(id) ON DELETE CASCADE,
  FOREIGN KEY (root_source_id) REFERENCES fj_profile_sources(id) ON DELETE SET NULL,
  CHECK (status IN ('active', 'stale', 'archived'))
);

CREATE INDEX IF NOT EXISTS idx_fj_resume_families_profile
  ON fj_resume_families(profile_id, status, updated_at DESC);

CREATE TABLE IF NOT EXISTS fj_profile_analysis_runs (
  id TEXT PRIMARY KEY,
  profile_id TEXT NOT NULL,
  source_ids_json TEXT NOT NULL DEFAULT '[]',
  input_versions_json TEXT NOT NULL DEFAULT '{}',
  ai_model TEXT,
  prompt_version TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  quality_json TEXT NOT NULL DEFAULT '{}',
  output_json TEXT,
  error_category TEXT,
  error_message TEXT,
  started_at TEXT,
  completed_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (profile_id) REFERENCES fj_candidate_profiles(id) ON DELETE CASCADE,
  CHECK (status IN ('pending', 'running', 'needs_confirmation', 'applied', 'failed', 'cancelled', 'stale'))
);

CREATE INDEX IF NOT EXISTS idx_fj_profile_analysis_runs_profile
  ON fj_profile_analysis_runs(profile_id, created_at DESC);

CREATE TABLE IF NOT EXISTS fj_profile_artifacts (
  id TEXT PRIMARY KEY,
  profile_id TEXT NOT NULL,
  context_scope_id TEXT,
  source_id TEXT,
  analysis_run_id TEXT,
  artifact_type TEXT NOT NULL,
  content TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 1,
  status TEXT NOT NULL DEFAULT 'draft',
  created_at TEXT NOT NULL,
  FOREIGN KEY (profile_id) REFERENCES fj_candidate_profiles(id) ON DELETE CASCADE,
  FOREIGN KEY (source_id) REFERENCES fj_profile_sources(id) ON DELETE CASCADE,
  FOREIGN KEY (analysis_run_id) REFERENCES fj_profile_analysis_runs(id) ON DELETE SET NULL,
  CHECK (artifact_type IN ('normalized_resume_markdown', 'candidate_context_full', 'candidate_context_search', 'candidate_context_evaluation', 'candidate_context_chat')),
  CHECK (status IN ('draft', 'official', 'stale'))
);

CREATE INDEX IF NOT EXISTS idx_fj_profile_artifacts_profile
  ON fj_profile_artifacts(profile_id, artifact_type, version DESC);

CREATE TABLE IF NOT EXISTS fj_resume_versions (
  id TEXT PRIMARY KEY,
  profile_id TEXT NOT NULL,
  resume_family_id TEXT,
  parent_version_id TEXT,
  name TEXT NOT NULL,
  role_family TEXT NOT NULL DEFAULT '',
  version_type TEXT NOT NULL DEFAULT 'base',
  target_job_id TEXT,
  derived_reason TEXT NOT NULL DEFAULT '',
  based_on_content_version INTEGER NOT NULL DEFAULT 1,
  campaign_id TEXT,
  source_id TEXT,
  content TEXT NOT NULL DEFAULT '',
  fact_ids_json TEXT NOT NULL DEFAULT '[]',
  is_default INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'draft',
  content_version INTEGER NOT NULL DEFAULT 1,
  confirmed_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (profile_id) REFERENCES fj_candidate_profiles(id) ON DELETE CASCADE,
  FOREIGN KEY (resume_family_id) REFERENCES fj_resume_families(id) ON DELETE CASCADE,
  FOREIGN KEY (parent_version_id) REFERENCES fj_resume_versions(id) ON DELETE SET NULL,
  FOREIGN KEY (source_id) REFERENCES fj_profile_sources(id) ON DELETE SET NULL,
  CHECK (is_default IN (0, 1)),
  CHECK (version_type IN ('base', 'jd_tailored', 'manual_variant', 'language_variant')),
  CHECK (status IN ('draft', 'confirmed', 'stale', 'archived'))
);

CREATE INDEX IF NOT EXISTS idx_fj_resume_versions_profile
  ON fj_resume_versions(profile_id, status, updated_at DESC);

CREATE TABLE IF NOT EXISTS fj_profile_facts (
  id TEXT PRIMARY KEY,
  profile_id TEXT NOT NULL,
  scope_type TEXT NOT NULL DEFAULT 'general',
  scope_id TEXT,
  domain TEXT NOT NULL,
  entity_type TEXT NOT NULL,
  entity_id TEXT NOT NULL,
  field_key TEXT NOT NULL,
  value_json TEXT NOT NULL,
  source_type TEXT NOT NULL,
  sort_order INTEGER NOT NULL DEFAULT 0,
  valid_from TEXT,
  valid_to TEXT,
  date_precision TEXT NOT NULL DEFAULT 'unknown',
  is_current INTEGER NOT NULL DEFAULT 0,
  confidence REAL NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'proposed',
  conflict_group_id TEXT,
  sensitivity TEXT NOT NULL DEFAULT 'normal',
  external_use TEXT NOT NULL DEFAULT 'prohibited',
  disclosure_policy_json TEXT NOT NULL DEFAULT '{}',
  valid_until TEXT,
  confirmed_by TEXT,
  analysis_operation_run_id TEXT,
  source_content_version INTEGER,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (profile_id) REFERENCES fj_candidate_profiles(id) ON DELETE CASCADE,
  CHECK (source_type IN ('document', 'user_answer', 'manual', 'ai_inference')),
  CHECK (scope_type IN ('general', 'resume_family')),
  CHECK (confirmed_by IS NULL OR confirmed_by IN ('ai_extraction', 'user')),
  CHECK (date_precision IN ('year', 'month', 'day', 'unknown')),
  CHECK (is_current IN (0, 1)),
  CHECK (confidence >= 0 AND confidence <= 1),
  CHECK (status IN ('proposed', 'confirmed', 'rejected', 'conflicted', 'stale')),
  CHECK (sensitivity IN ('normal', 'private', 'sensitive')),
  CHECK (external_use IN ('prohibited', 'summary_only', 'allowed'))
);

CREATE INDEX IF NOT EXISTS idx_fj_profile_facts_profile
  ON fj_profile_facts(profile_id, domain, entity_id, sort_order);

CREATE TABLE IF NOT EXISTS fj_profile_fact_evidence (
  id TEXT PRIMARY KEY,
  fact_id TEXT NOT NULL,
  source_type TEXT NOT NULL,
  source_id TEXT,
  source_excerpt TEXT NOT NULL DEFAULT '',
  extraction_method TEXT NOT NULL DEFAULT 'ai',
  confidence REAL NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  FOREIGN KEY (fact_id) REFERENCES fj_profile_facts(id) ON DELETE CASCADE,
  CHECK (source_type IN ('document', 'question_answer', 'manual')),
  CHECK (confidence >= 0 AND confidence <= 1)
);

CREATE INDEX IF NOT EXISTS idx_fj_profile_fact_evidence_fact
  ON fj_profile_fact_evidence(fact_id, created_at);

CREATE TABLE IF NOT EXISTS fj_profile_questions (
  id TEXT PRIMARY KEY,
  profile_id TEXT NOT NULL,
  scope_type TEXT NOT NULL DEFAULT 'general',
  scope_id TEXT,
  scope_key TEXT NOT NULL DEFAULT 'general',
  question_key TEXT NOT NULL,
  question_text TEXT NOT NULL,
  reason TEXT NOT NULL DEFAULT '',
  origin TEXT NOT NULL DEFAULT 'user',
  answer_type TEXT NOT NULL DEFAULT 'text',
  required_stage TEXT NOT NULL DEFAULT 'chat',
  priority TEXT NOT NULL DEFAULT 'medium',
  proposed_answer_json TEXT,
  final_answer_json TEXT,
  status TEXT NOT NULL DEFAULT 'pending',
  external_use TEXT NOT NULL DEFAULT 'prohibited',
  valid_until TEXT,
  source_id TEXT,
  job_id TEXT,
  writes_to_field TEXT,
  enabled INTEGER NOT NULL DEFAULT 1,
  confirmed_by TEXT,
  analysis_operation_run_id TEXT,
  source_content_version INTEGER,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (profile_id) REFERENCES fj_candidate_profiles(id) ON DELETE CASCADE,
  FOREIGN KEY (source_id) REFERENCES fj_profile_sources(id) ON DELETE SET NULL,
  UNIQUE (profile_id, scope_key, question_key),
  CHECK (scope_type IN ('general', 'resume_family')),
  CHECK (origin IN ('default', 'resume_analysis', 'jd_analysis', 'user')),
  CHECK (answer_type IN ('text', 'number', 'date', 'range', 'select', 'multi_select', 'boolean')),
  CHECK (required_stage IN ('search', 'greeting', 'application', 'chat', 'interview')),
  CHECK (priority IN ('high', 'medium', 'low')),
  CHECK (status IN ('pending', 'proposed_answer', 'answered', 'confirmed', 'declined', 'conflicted', 'stale')),
  CHECK (external_use IN ('prohibited', 'summary_only', 'allowed')),
  CHECK (enabled IN (0, 1))
);

CREATE INDEX IF NOT EXISTS idx_fj_profile_questions_profile
  ON fj_profile_questions(profile_id, enabled, priority, updated_at DESC);

CREATE TABLE IF NOT EXISTS fj_profile_answer_variants (
  id TEXT PRIMARY KEY,
  question_id TEXT NOT NULL,
  name TEXT NOT NULL,
  scope_type TEXT NOT NULL DEFAULT 'general',
  scope_id TEXT,
  answer_text TEXT NOT NULL,
  internal_note TEXT NOT NULL DEFAULT '',
  usage_condition TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'draft',
  generated_by TEXT NOT NULL DEFAULT 'user',
  based_on_job_version INTEGER,
  external_use TEXT NOT NULL DEFAULT 'prohibited',
  disclosure_policy_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (question_id) REFERENCES fj_profile_questions(id) ON DELETE CASCADE,
  CHECK (scope_type IN ('general', 'role_family', 'job')),
  CHECK (status IN ('draft', 'confirmed', 'rejected', 'stale')),
  CHECK (generated_by IN ('system', 'ai', 'user')),
  CHECK (external_use IN ('prohibited', 'summary_only', 'allowed'))
);

CREATE INDEX IF NOT EXISTS idx_fj_profile_answer_variants_question
  ON fj_profile_answer_variants(question_id, scope_type, status, updated_at DESC);

CREATE TABLE IF NOT EXISTS fj_profile_analysis_items (
  id TEXT PRIMARY KEY,
  analysis_run_id TEXT NOT NULL,
  item_type TEXT NOT NULL,
  source_refs_json TEXT NOT NULL DEFAULT '[]',
  payload_json TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  result_resource_type TEXT,
  result_resource_id TEXT,
  decision_note TEXT,
  decided_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (analysis_run_id) REFERENCES fj_profile_analysis_runs(id) ON DELETE CASCADE,
  CHECK (item_type IN ('fact', 'question', 'answer_variant', 'strategy', 'search_query', 'resume_version_suggestion')),
  CHECK (status IN ('pending', 'accepted', 'edited_and_accepted', 'rejected', 'deferred', 'apply_failed', 'applied'))
);

CREATE INDEX IF NOT EXISTS idx_fj_profile_analysis_items_run
  ON fj_profile_analysis_items(analysis_run_id, status, item_type);

CREATE TABLE IF NOT EXISTS fj_resume_analysis_runs (
  id TEXT PRIMARY KEY,
  profile_id TEXT NOT NULL,
  resume_family_id TEXT NOT NULL,
  source_ids_json TEXT NOT NULL DEFAULT '[]',
  operation_ids_json TEXT NOT NULL DEFAULT '[]',
  input_versions_json TEXT NOT NULL DEFAULT '{}',
  pipeline_mode TEXT NOT NULL DEFAULT 'chained',
  execution_path TEXT NOT NULL DEFAULT 'structured',
  ai_model TEXT,
  status TEXT NOT NULL DEFAULT 'queued',
  error_category TEXT,
  error_message TEXT,
  started_at TEXT,
  completed_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (profile_id) REFERENCES fj_candidate_profiles(id) ON DELETE CASCADE,
  FOREIGN KEY (resume_family_id) REFERENCES fj_resume_families(id) ON DELETE CASCADE,
  CHECK (pipeline_mode IN ('single', 'chained')),
  CHECK (execution_path IN ('structured', 'codex_workspace')),
  CHECK (status IN ('queued', 'running', 'completed', 'partial_failed', 'failed', 'cancelled'))
);

CREATE INDEX IF NOT EXISTS idx_fj_resume_analysis_runs_family
  ON fj_resume_analysis_runs(resume_family_id, created_at DESC);

CREATE TABLE IF NOT EXISTS fj_resume_analysis_operations (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  operation_id TEXT NOT NULL,
  sequence_no INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued',
  input_versions_json TEXT NOT NULL DEFAULT '{}',
  output_summary_json TEXT NOT NULL DEFAULT '{}',
  error_category TEXT,
  error_message TEXT,
  started_at TEXT,
  completed_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (run_id) REFERENCES fj_resume_analysis_runs(id) ON DELETE CASCADE,
  UNIQUE (run_id, operation_id),
  CHECK (operation_id IN ('clean_content', 'extract_facts', 'extract_qa', 'generate_filter_strategy', 'generate_recommendation_strategy', 'generate_search_keywords')),
  CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'blocked', 'cancelled', 'stale'))
);

CREATE INDEX IF NOT EXISTS idx_fj_resume_analysis_operations_run
  ON fj_resume_analysis_operations(run_id, sequence_no);

CREATE TABLE IF NOT EXISTS fj_resume_analysis_issues (
  id TEXT PRIMARY KEY,
  profile_id TEXT NOT NULL,
  resume_family_id TEXT NOT NULL,
  source_id TEXT,
  operation_run_id TEXT,
  issue_type TEXT NOT NULL,
  title TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  source_excerpt TEXT NOT NULL DEFAULT '',
  payload_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'pending',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  resolved_at TEXT,
  FOREIGN KEY (profile_id) REFERENCES fj_candidate_profiles(id) ON DELETE CASCADE,
  FOREIGN KEY (resume_family_id) REFERENCES fj_resume_families(id) ON DELETE CASCADE,
  FOREIGN KEY (source_id) REFERENCES fj_profile_sources(id) ON DELETE SET NULL,
  FOREIGN KEY (operation_run_id) REFERENCES fj_resume_analysis_operations(id) ON DELETE SET NULL,
  CHECK (issue_type IN ('uncertain_fact', 'conflict', 'missing_information', 'suggested_question')),
  CHECK (status IN ('pending', 'resolved', 'dismissed'))
);

CREATE INDEX IF NOT EXISTS idx_fj_resume_analysis_issues_family
  ON fj_resume_analysis_issues(resume_family_id, status, updated_at DESC);

CREATE TABLE IF NOT EXISTS fj_resume_strategies (
  id TEXT PRIMARY KEY,
  profile_id TEXT NOT NULL,
  resume_family_id TEXT NOT NULL,
  strategy_type TEXT NOT NULL,
  name TEXT NOT NULL,
  content_json TEXT NOT NULL DEFAULT '{}',
  version INTEGER NOT NULL DEFAULT 1,
  status TEXT NOT NULL DEFAULT 'current',
  generated_by TEXT NOT NULL DEFAULT 'ai',
  operation_run_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (profile_id) REFERENCES fj_candidate_profiles(id) ON DELETE CASCADE,
  FOREIGN KEY (resume_family_id) REFERENCES fj_resume_families(id) ON DELETE CASCADE,
  FOREIGN KEY (operation_run_id) REFERENCES fj_resume_analysis_operations(id) ON DELETE SET NULL,
  CHECK (strategy_type IN ('filter', 'recommendation')),
  CHECK (status IN ('current', 'stale', 'archived')),
  CHECK (generated_by IN ('ai', 'user'))
);

CREATE INDEX IF NOT EXISTS idx_fj_resume_strategies_family
  ON fj_resume_strategies(resume_family_id, strategy_type, status, version DESC);

CREATE TABLE IF NOT EXISTS fj_resume_search_keywords (
  id TEXT PRIMARY KEY,
  profile_id TEXT NOT NULL,
  resume_family_id TEXT NOT NULL,
  keyword TEXT NOT NULL,
  sort_order INTEGER NOT NULL DEFAULT 0,
  reason TEXT NOT NULL DEFAULT '',
  enabled INTEGER NOT NULL DEFAULT 1,
  version INTEGER NOT NULL DEFAULT 1,
  status TEXT NOT NULL DEFAULT 'current',
  operation_run_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (profile_id) REFERENCES fj_candidate_profiles(id) ON DELETE CASCADE,
  FOREIGN KEY (resume_family_id) REFERENCES fj_resume_families(id) ON DELETE CASCADE,
  FOREIGN KEY (operation_run_id) REFERENCES fj_resume_analysis_operations(id) ON DELETE SET NULL,
  CHECK (enabled IN (0, 1)),
  CHECK (status IN ('current', 'stale', 'archived'))
);

CREATE INDEX IF NOT EXISTS idx_fj_resume_search_keywords_family
  ON fj_resume_search_keywords(resume_family_id, enabled, sort_order, created_at);

CREATE TABLE IF NOT EXISTS fj_search_campaigns (
  id TEXT PRIMARY KEY,
  profile_id TEXT NOT NULL,
  name TEXT NOT NULL,
  target_titles_json TEXT NOT NULL DEFAULT '[]',
  role_families_json TEXT NOT NULL DEFAULT '[]',
  cities_json TEXT NOT NULL DEFAULT '[]',
  districts_json TEXT NOT NULL DEFAULT '[]',
  work_modes_json TEXT NOT NULL DEFAULT '[]',
  salary_json TEXT NOT NULL DEFAULT '{}',
  industries_json TEXT NOT NULL DEFAULT '[]',
  company_scales_json TEXT NOT NULL DEFAULT '[]',
  resume_version_id TEXT,
  filter_strategy_id TEXT,
  recommendation_strategy_id TEXT,
  delivery_strategy_id TEXT,
  excluded_terms_json TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL DEFAULT 'active',
  campaign_version INTEGER NOT NULL DEFAULT 1,
  confirmed_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (profile_id) REFERENCES fj_candidate_profiles(id) ON DELETE CASCADE,
  FOREIGN KEY (resume_version_id) REFERENCES fj_resume_versions(id) ON DELETE SET NULL,
  CHECK (status IN ('active', 'paused', 'archived'))
);

CREATE INDEX IF NOT EXISTS idx_fj_search_campaigns_profile
  ON fj_search_campaigns(profile_id, status, updated_at DESC);

CREATE TABLE IF NOT EXISTS fj_search_queries (
  id TEXT PRIMARY KEY,
  campaign_id TEXT NOT NULL,
  name TEXT NOT NULL,
  role_family TEXT NOT NULL DEFAULT '',
  platform TEXT NOT NULL DEFAULT 'boss',
  keyword TEXT NOT NULL,
  cities_json TEXT NOT NULL DEFAULT '[]',
  work_modes_json TEXT NOT NULL DEFAULT '[]',
  positive_terms_json TEXT NOT NULL DEFAULT '[]',
  excluded_terms_json TEXT NOT NULL DEFAULT '[]',
  priority INTEGER NOT NULL DEFAULT 0,
  reason TEXT NOT NULL DEFAULT '',
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (campaign_id) REFERENCES fj_search_campaigns(id) ON DELETE CASCADE,
  CHECK (enabled IN (0, 1))
);

CREATE INDEX IF NOT EXISTS idx_fj_search_queries_campaign
  ON fj_search_queries(campaign_id, enabled, priority DESC, created_at);

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
  force_contact_verification_enabled INTEGER NOT NULL DEFAULT 0,
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
  CHECK (force_contact_verification_enabled IN (0, 1)),
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
  cooldown_rules_json TEXT NOT NULL DEFAULT '{}',
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

CREATE TABLE IF NOT EXISTS fj_companies (
  id TEXT PRIMARY KEY,
  canonical_name TEXT NOT NULL,
  normalized_name TEXT NOT NULL UNIQUE,
  company_type TEXT NOT NULL DEFAULT 'unknown',
  classification_source TEXT NOT NULL DEFAULT 'capture',
  notes TEXT NOT NULL DEFAULT '',
  is_blacklisted INTEGER NOT NULL DEFAULT 0,
  blacklist_reason TEXT NOT NULL DEFAULT '',
  blacklisted_at TEXT,
  version INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  CHECK (company_type IN ('unknown', 'direct', 'outsourcing')),
  CHECK (classification_source IN ('capture', 'manual', 'mcp', 'migration')),
  CHECK (is_blacklisted IN (0, 1)),
  CHECK (version > 0)
);

CREATE INDEX IF NOT EXISTS idx_fj_companies_type_blacklisted
  ON fj_companies(company_type, is_blacklisted, updated_at DESC);

CREATE TABLE IF NOT EXISTS fj_company_aliases (
  id TEXT PRIMARY KEY,
  company_id TEXT NOT NULL,
  alias_name TEXT NOT NULL,
  normalized_alias TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL,
  FOREIGN KEY (company_id) REFERENCES fj_companies(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_fj_company_aliases_company
  ON fj_company_aliases(company_id, created_at DESC);

CREATE TABLE IF NOT EXISTS fj_boss_jobs (
  id TEXT PRIMARY KEY,
  dedupe_key TEXT NOT NULL UNIQUE,
  source_job_id TEXT NOT NULL DEFAULT '',
  encrypt_job_id TEXT NOT NULL DEFAULT '',
  title TEXT NOT NULL DEFAULT '',
  company_name TEXT NOT NULL DEFAULT '',
  company_id TEXT,
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
  search_keyword TEXT NOT NULL DEFAULT '',
  is_test INTEGER NOT NULL DEFAULT 0,
  payload_json TEXT NOT NULL DEFAULT '{}',
  detail_json TEXT,
  detail_status TEXT NOT NULL DEFAULT 'not_collected',
  detail_error TEXT,
  delivery_evaluation_json TEXT,
  detail_collected_at TEXT,
  first_collected_at TEXT NOT NULL,
  last_collected_at TEXT NOT NULL,
  collect_count INTEGER NOT NULL DEFAULT 1,
  latest_batch_id TEXT NOT NULL,
  FOREIGN KEY (latest_batch_id) REFERENCES fj_boss_capture_batches(id),
  FOREIGN KEY (company_id) REFERENCES fj_companies(id) ON DELETE SET NULL,
  CHECK (detail_status IN ('not_collected', 'queued', 'collecting', 'completed', 'failed')),
  CHECK (is_test IN (0, 1)),
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

CREATE TABLE IF NOT EXISTS fj_job_applications (
  id TEXT PRIMARY KEY,
  job_id TEXT NOT NULL UNIQUE,
  company_id TEXT,
  status TEXT DEFAULT 'pending_greeting',
  source TEXT NOT NULL DEFAULT 'manual',
  source_action_id TEXT,
  evidence_level TEXT NOT NULL DEFAULT 'confirmed',
  applied_at TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (job_id) REFERENCES fj_boss_jobs(id) ON DELETE CASCADE,
  FOREIGN KEY (company_id) REFERENCES fj_companies(id) ON DELETE SET NULL,
  CHECK (status IS NULL OR status IN (
    'pending_greeting', 'pending_application', 'communicating',
    'offer', 'rejected', 'closed'
  )),
  CHECK (source IN ('boss_action', 'manual', 'mcp', 'migration')),
  CHECK (evidence_level IN ('confirmed', 'inferred'))
);

CREATE INDEX IF NOT EXISTS idx_fj_job_applications_status_time
  ON fj_job_applications(status, applied_at DESC);
CREATE INDEX IF NOT EXISTS idx_fj_job_applications_company
  ON fj_job_applications(company_id, status, applied_at DESC);

CREATE TABLE IF NOT EXISTS fj_filter_exclusion_states (
  strategy_id TEXT PRIMARY KEY,
  strategy_version INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'stale',
  last_full_refreshed_at TEXT,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (strategy_id) REFERENCES fj_job_filter_strategies(id) ON DELETE CASCADE,
  CHECK (status IN ('ready', 'stale'))
);

CREATE TABLE IF NOT EXISTS fj_filter_exclusion_entries (
  id TEXT PRIMARY KEY,
  strategy_id TEXT NOT NULL,
  entity_type TEXT NOT NULL,
  entity_id TEXT NOT NULL,
  rule_type TEXT NOT NULL,
  source_event_at TEXT,
  excluded_until TEXT,
  reason TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (strategy_id) REFERENCES fj_job_filter_strategies(id) ON DELETE CASCADE,
  UNIQUE (strategy_id, entity_type, entity_id, rule_type),
  CHECK (entity_type IN ('company', 'job'))
);

CREATE INDEX IF NOT EXISTS idx_fj_filter_exclusion_entries_active
  ON fj_filter_exclusion_entries(strategy_id, entity_type, entity_id, excluded_until);

CREATE TABLE IF NOT EXISTS fj_job_evaluations (
  id TEXT PRIMARY KEY,
  job_id TEXT NOT NULL,
  evaluation_version TEXT NOT NULL DEFAULT '2.0',
  recommendation_strategy_id TEXT,
  filter_strategy_id TEXT,
  resume_id TEXT,
  source TEXT NOT NULL,
  decision TEXT NOT NULL,
  confidence REAL NOT NULL DEFAULT 0,
  evaluation_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  FOREIGN KEY (job_id) REFERENCES fj_boss_jobs(id) ON DELETE CASCADE,
  CHECK (source IN ('rules', 'llm')),
  CHECK (decision IN ('recommend', 'review', 'reject'))
);

CREATE INDEX IF NOT EXISTS idx_fj_job_evaluations_job_created_at
  ON fj_job_evaluations(job_id, created_at DESC);

CREATE TABLE IF NOT EXISTS fj_review_items (
  id TEXT PRIMARY KEY,
  job_id TEXT NOT NULL,
  evaluation_id TEXT NOT NULL,
  action_type TEXT NOT NULL DEFAULT 'start_conversation',
  task_type TEXT NOT NULL DEFAULT 'BOSS_DEFAULT_GREETING',
  status TEXT NOT NULL DEFAULT 'pending',
  ai_decision TEXT NOT NULL,
  draft_message TEXT NOT NULL DEFAULT '',
  final_message TEXT NOT NULL DEFAULT '',
  resolution_note TEXT NOT NULL DEFAULT '',
  auto_approved INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  resolved_at TEXT,
  FOREIGN KEY (job_id) REFERENCES fj_boss_jobs(id) ON DELETE CASCADE,
  FOREIGN KEY (evaluation_id) REFERENCES fj_job_evaluations(id) ON DELETE CASCADE,
  UNIQUE (evaluation_id, action_type),
  CHECK (action_type IN ('start_conversation')),
  CHECK (status IN ('pending', 'approved', 'rejected', 'dismissed')),
  CHECK (ai_decision IN ('recommend', 'review', 'reject')),
  CHECK (auto_approved IN (0, 1))
);

CREATE INDEX IF NOT EXISTS idx_fj_review_items_status_created_at
  ON fj_review_items(status, created_at DESC);

CREATE TABLE IF NOT EXISTS fj_automation_actions (
  id TEXT PRIMARY KEY,
  job_id TEXT NOT NULL,
  evaluation_id TEXT NOT NULL,
  review_item_id TEXT NOT NULL,
  action_type TEXT NOT NULL DEFAULT 'start_conversation',
  status TEXT NOT NULL DEFAULT 'queued',
  idempotency_key TEXT NOT NULL UNIQUE,
  payload_json TEXT NOT NULL DEFAULT '{}',
  last_error TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  completed_at TEXT,
  execution_state TEXT NOT NULL DEFAULT 'queued',
  execution_epoch INTEGER NOT NULL DEFAULT 0,
  last_status_code TEXT,
  result_json TEXT NOT NULL DEFAULT '{}',
  FOREIGN KEY (job_id) REFERENCES fj_boss_jobs(id) ON DELETE CASCADE,
  FOREIGN KEY (evaluation_id) REFERENCES fj_job_evaluations(id) ON DELETE CASCADE,
  FOREIGN KEY (review_item_id) REFERENCES fj_review_items(id) ON DELETE CASCADE,
  CHECK (action_type IN ('start_conversation', 'BOSS_DEFAULT_GREETING')),
  CHECK (status IN ('queued', 'running', 'leased', 'succeeded', 'failed', 'blocked', 'unknown', 'cancelled')),
  CHECK (execution_epoch >= 0)
);

CREATE INDEX IF NOT EXISTS idx_fj_automation_actions_status_created_at
  ON fj_automation_actions(status, created_at ASC);
CREATE TABLE IF NOT EXISTS fj_boss_executor_instances (
  id TEXT PRIMARY KEY,
  label TEXT NOT NULL DEFAULT '',
  token_hash TEXT NOT NULL UNIQUE,
  protocol_version TEXT NOT NULL,
  plugin_version TEXT NOT NULL,
  capabilities_json TEXT NOT NULL DEFAULT '[]',
  queue_state TEXT NOT NULL DEFAULT 'paused',
  risk_state TEXT NOT NULL DEFAULT 'none',
  browser_connected INTEGER NOT NULL DEFAULT 0,
  last_heartbeat_at TEXT,
  task_cooldown_max_seconds INTEGER NOT NULL DEFAULT 4,
  page_load_wait_max_seconds INTEGER NOT NULL DEFAULT 3,
  runtime_phase TEXT NOT NULL DEFAULT 'idle',
  runtime_detail TEXT NOT NULL DEFAULT '',
  runtime_until_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  CHECK (queue_state IN ('running', 'paused', 'risk_paused')),
  CHECK (browser_connected IN (0, 1))
);

CREATE INDEX IF NOT EXISTS idx_fj_boss_executor_heartbeat
  ON fj_boss_executor_instances(last_heartbeat_at DESC);

CREATE TABLE IF NOT EXISTS fj_boss_pairing_codes (
  id TEXT PRIMARY KEY,
  code_hash TEXT NOT NULL UNIQUE,
  expires_at TEXT NOT NULL,
  used_at TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fj_boss_navigation_tasks (
  id TEXT PRIMARY KEY,
  action_id TEXT,
  job_id TEXT NOT NULL,
  source_context TEXT NOT NULL,
  target_url TEXT NOT NULL,
  target_encrypt_job_id TEXT NOT NULL,
  browser_target_id TEXT,
  status TEXT NOT NULL DEFAULT 'queued',
  error_code TEXT,
  error_message TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  opened_at TEXT,
  CHECK (source_context IN ('capture', 'history', 'review', 'queue')),
  CHECK (status IN ('queued', 'opened', 'failed'))
);

CREATE INDEX IF NOT EXISTS idx_fj_boss_navigation_action
  ON fj_boss_navigation_tasks(action_id, created_at DESC);

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

-- 自动代聊运行开关。首版默认全部关闭，必须由桌面端显式启用。
CREATE TABLE IF NOT EXISTS fj_chat_runtime (
  id TEXT PRIMARY KEY,
  listen_enabled INTEGER NOT NULL DEFAULT 0,
  generation_enabled INTEGER NOT NULL DEFAULT 0,
  send_enabled INTEGER NOT NULL DEFAULT 0,
  trigger_mode TEXT NOT NULL DEFAULT 'interval',
  interval_minutes INTEGER NOT NULL DEFAULT 30,
  last_scheduled_at TEXT,
  leader_executor_id TEXT,
  leader_tab_id TEXT,
  leader_epoch INTEGER NOT NULL DEFAULT 0,
  leader_lease_expires_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  CHECK (listen_enabled IN (0, 1)),
  CHECK (generation_enabled IN (0, 1)),
  CHECK (send_enabled IN (0, 1)),
  CHECK (trigger_mode IN ('immediate', 'interval', 'manual')),
  CHECK (interval_minutes IN (0, 5, 10, 30, 60))
);

CREATE TABLE IF NOT EXISTS fj_chat_sessions (
  id TEXT PRIMARY KEY,
  platform TEXT NOT NULL DEFAULT 'boss',
  account_uid TEXT NOT NULL,
  peer_uid TEXT NOT NULL,
  encrypt_peer_uid TEXT NOT NULL DEFAULT '',
  security_id TEXT NOT NULL DEFAULT '',
  job_id TEXT,
  encrypt_job_id TEXT NOT NULL DEFAULT '',
  job_title TEXT NOT NULL DEFAULT '',
  peer_name TEXT NOT NULL DEFAULT '',
  peer_title TEXT NOT NULL DEFAULT '',
  company_name TEXT NOT NULL DEFAULT '',
  platform_latest_msg_id TEXT NOT NULL DEFAULT '',
  platform_latest_message_status INTEGER,
  platform_relation_type INTEGER,
  platform_chat_status INTEGER,
  platform_latest_message_text TEXT NOT NULL DEFAULT '',
  platform_latest_message_at TEXT,
  platform_latest_from_id TEXT NOT NULL DEFAULT '',
  platform_latest_to_id TEXT NOT NULL DEFAULT '',
  platform_synced_at TEXT,
  platform_list_index INTEGER NOT NULL DEFAULT 0,
  message_update_required INTEGER NOT NULL DEFAULT 0,
  history_has_more INTEGER NOT NULL DEFAULT 0,
  history_next_cursor TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'human_takeover',
  session_version INTEGER NOT NULL DEFAULT 0,
  latest_message_id TEXT,
  latest_inbound_message_id TEXT,
  last_message_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (job_id) REFERENCES fj_boss_jobs(id) ON DELETE SET NULL,
  UNIQUE (platform, account_uid, peer_uid, encrypt_job_id),
  CHECK (platform IN ('boss')),
  CHECK (status IN ('active', 'human_takeover', 'paused', 'unsupported'))
);

CREATE TABLE IF NOT EXISTS fj_chat_leaders (
  account_uid TEXT PRIMARY KEY,
  executor_id TEXT NOT NULL,
  tab_id TEXT NOT NULL,
  leader_epoch INTEGER NOT NULL,
  lease_expires_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (executor_id) REFERENCES fj_boss_executor_instances(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_fj_chat_sessions_updated_at
  ON fj_chat_sessions(updated_at DESC);

CREATE TABLE IF NOT EXISTS fj_chat_messages (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  platform_message_id TEXT NOT NULL,
  direction TEXT NOT NULL,
  message_type TEXT NOT NULL DEFAULT 'text',
  content TEXT NOT NULL DEFAULT '',
  sender_uid TEXT NOT NULL DEFAULT '',
  receiver_uid TEXT NOT NULL DEFAULT '',
  client_mid TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL,
  sent_at TEXT NOT NULL,
  observed_at TEXT NOT NULL,
  raw_meta_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  FOREIGN KEY (session_id) REFERENCES fj_chat_sessions(id) ON DELETE CASCADE,
  UNIQUE (session_id, platform_message_id),
  CHECK (direction IN ('inbound', 'outbound')),
  CHECK (message_type IN ('text', 'image', 'system', 'unknown')),
  CHECK (source IN ('websocket', 'manual', 'assistant'))
);

CREATE INDEX IF NOT EXISTS idx_fj_chat_messages_session_sent_at
  ON fj_chat_messages(session_id, sent_at ASC, id ASC);

CREATE TABLE IF NOT EXISTS fj_chat_reply_tasks (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  trigger_source TEXT NOT NULL,
  action_kind TEXT NOT NULL DEFAULT 'reply',
  insight_id TEXT,
  status TEXT NOT NULL DEFAULT 'pending_generation',
  based_on_message_id TEXT NOT NULL,
  based_on_session_version INTEGER NOT NULL,
  generation_due_at TEXT,
  input_message_ids_json TEXT NOT NULL DEFAULT '[]',
  decision TEXT NOT NULL DEFAULT 'reply',
  facts_used_json TEXT NOT NULL DEFAULT '[]',
  warnings_json TEXT NOT NULL DEFAULT '[]',
  requires_user_input INTEGER NOT NULL DEFAULT 0,
  decision_reason TEXT NOT NULL DEFAULT '',
  context_json TEXT NOT NULL DEFAULT '{}',
  draft_text TEXT NOT NULL DEFAULT '',
  final_text TEXT NOT NULL DEFAULT '',
  generation_model TEXT NOT NULL DEFAULT '',
  generation_error TEXT,
  generated_at TEXT,
  confirmed_at TEXT,
  cancelled_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (session_id) REFERENCES fj_chat_sessions(id) ON DELETE CASCADE,
  FOREIGN KEY (based_on_message_id) REFERENCES fj_chat_messages(id) ON DELETE CASCADE,
  FOREIGN KEY (insight_id) REFERENCES fj_conversation_insights(id) ON DELETE SET NULL,
  CHECK (trigger_source IN ('realtime', 'interval', 'manual')),
  CHECK (action_kind IN ('reply', 'followup', 'ask_rejection_reason')),
  CHECK (status IN ('pending_generation', 'generating', 'awaiting_review', 'confirmed', 'cancelled', 'stale', 'failed'))
);

CREATE INDEX IF NOT EXISTS idx_fj_chat_reply_tasks_status_created_at
  ON fj_chat_reply_tasks(status, created_at ASC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_fj_chat_reply_tasks_one_active
  ON fj_chat_reply_tasks(session_id)
  WHERE status IN ('pending_generation', 'generating', 'awaiting_review', 'confirmed');

CREATE TABLE IF NOT EXISTS fj_chat_send_actions (
  id TEXT PRIMARY KEY,
  reply_task_id TEXT NOT NULL UNIQUE,
  session_id TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued',
  text TEXT NOT NULL,
  execution_epoch INTEGER NOT NULL DEFAULT 0,
  lease_owner TEXT,
  lease_expires_at TEXT,
  leader_tab_id TEXT NOT NULL DEFAULT '',
  leader_epoch INTEGER NOT NULL DEFAULT 0,
  dispatch_deadline_at TEXT,
  attempt_count INTEGER NOT NULL DEFAULT 0,
  outcome TEXT,
  platform_message_id TEXT NOT NULL DEFAULT '',
  client_mid TEXT NOT NULL DEFAULT '',
  status_code TEXT NOT NULL DEFAULT '',
  error_message TEXT NOT NULL DEFAULT '',
  evidence_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  dispatched_at TEXT,
  completed_at TEXT,
  FOREIGN KEY (reply_task_id) REFERENCES fj_chat_reply_tasks(id) ON DELETE CASCADE,
  FOREIGN KEY (session_id) REFERENCES fj_chat_sessions(id) ON DELETE CASCADE,
  CHECK (status IN ('queued', 'leased', 'dispatching', 'accepted', 'failed', 'unknown', 'cancelled')),
  CHECK (outcome IS NULL OR outcome IN ('accepted', 'failed', 'unknown'))
);

CREATE INDEX IF NOT EXISTS idx_fj_chat_send_actions_status_created_at
  ON fj_chat_send_actions(status, created_at ASC);

CREATE TABLE IF NOT EXISTS fj_chat_events (
  id TEXT PRIMARY KEY,
  executor_id TEXT NOT NULL,
  event_id TEXT NOT NULL UNIQUE,
  event_type TEXT NOT NULL,
  account_uid TEXT NOT NULL DEFAULT '',
  leader_epoch INTEGER NOT NULL DEFAULT 0,
  payload_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  FOREIGN KEY (executor_id) REFERENCES fj_boss_executor_instances(id) ON DELETE CASCADE,
  CHECK (event_type IN ('message', 'socket_state', 'manual_takeover'))
);

CREATE INDEX IF NOT EXISTS idx_fj_chat_events_created_at
  ON fj_chat_events(created_at DESC);

-- 求职活动是后续统计与阶段投影共同使用的不可变事实流。
CREATE TABLE IF NOT EXISTS fj_job_activity_events (
  id TEXT PRIMARY KEY,
  job_id TEXT NOT NULL,
  company_id TEXT,
  chat_session_id TEXT,
  event_type TEXT NOT NULL,
  occurred_at TEXT NOT NULL,
  source TEXT NOT NULL,
  source_ref_type TEXT NOT NULL,
  source_ref_id TEXT NOT NULL,
  confidence REAL NOT NULL DEFAULT 1,
  evidence_level TEXT NOT NULL DEFAULT 'direct',
  payload_json TEXT NOT NULL DEFAULT '{}',
  dedupe_key TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL,
  FOREIGN KEY (job_id) REFERENCES fj_boss_jobs(id) ON DELETE CASCADE,
  FOREIGN KEY (company_id) REFERENCES fj_companies(id) ON DELETE SET NULL,
  FOREIGN KEY (chat_session_id) REFERENCES fj_chat_sessions(id) ON DELETE SET NULL,
  CHECK (event_type IN (
    'job_discovered', 'job_shortlisted',
    'candidate_initiated_contact', 'recruiter_initiated_contact', 'conversation_state_analyzed',
    'greeting_requested', 'greeting_sent', 'greeting_failed',
    'recruiter_replied', 'candidate_replied',
    'resume_requested', 'resume_submitted', 'resume_accepted', 'resume_viewed',
    'under_review',
    'interview_intent_detected', 'interview_invited', 'interview_scheduled',
    'rejected', 'job_closed', 'followup_recommended', 'no_response_detected',
    'offer_received', 'conversation_closed', 'manual_stage_changed'
  )),
  CHECK (confidence >= 0 AND confidence <= 1),
  CHECK (evidence_level IN ('direct', 'strong_inferred', 'weak_inferred'))
);

CREATE INDEX IF NOT EXISTS idx_fj_job_activity_job_time
  ON fj_job_activity_events(job_id, occurred_at DESC, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_fj_job_activity_type_time
  ON fj_job_activity_events(event_type, occurred_at DESC);

-- Pipeline 是 Activity 的可重放投影，第一阶段与旧 application 状态并行存在。
CREATE TABLE IF NOT EXISTS fj_job_pipeline_snapshots (
  job_id TEXT PRIMARY KEY,
  company_id TEXT,
  stage TEXT NOT NULL,
  stage_source TEXT NOT NULL,
  stage_event_id TEXT NOT NULL,
  stage_updated_at TEXT NOT NULL,
  waiting_on TEXT NOT NULL DEFAULT 'unknown',
  waiting_since_at TEXT,
  contact_origin TEXT NOT NULL DEFAULT 'unknown',
  rejection_reason_source TEXT NOT NULL DEFAULT 'unknown',
  rejection_reason_category TEXT NOT NULL DEFAULT 'unknown',
  rejection_reason_summary TEXT NOT NULL DEFAULT '',
  projection_version INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (job_id) REFERENCES fj_boss_jobs(id) ON DELETE CASCADE,
  FOREIGN KEY (company_id) REFERENCES fj_companies(id) ON DELETE SET NULL,
  FOREIGN KEY (stage_event_id) REFERENCES fj_job_activity_events(id) ON DELETE CASCADE,
  CHECK (stage IN (
    'discovered', 'shortlisted', 'greeted', 'communicating',
    'resume_requested', 'resume_submitted', 'resume_viewed', 'under_review',
    'interview_scheduling', 'interviewing',
    'offer', 'rejected', 'closed'
  )),
  CHECK (waiting_on IN ('candidate', 'recruiter', 'none', 'unknown')),
  CHECK (contact_origin IN (
    'finejob_auto', 'candidate_initiated', 'recruiter_initiated',
    'external_candidate_initiated', 'unknown'
  )),
  CHECK (rejection_reason_source IN ('recruiter_explicit', 'ai_inferred', 'unknown')),
  CHECK (rejection_reason_category IN (
    'experience', 'education', 'skills', 'industry_background', 'salary',
    'location', 'availability', 'position_filled', 'headcount_closed', 'fit',
    'other', 'unknown'
  ))
);

CREATE INDEX IF NOT EXISTS idx_fj_pipeline_stage_time
  ON fj_job_pipeline_snapshots(stage, stage_updated_at DESC);

-- Evidence 保存平台或协议层实际观察，action_ref_type 防止不同动作表的同名 ID 串联。
CREATE TABLE IF NOT EXISTS fj_execution_evidence (
  id TEXT PRIMARY KEY,
  action_ref_type TEXT NOT NULL,
  action_ref_id TEXT NOT NULL,
  evidence_type TEXT NOT NULL,
  source TEXT NOT NULL,
  source_ref_type TEXT NOT NULL,
  source_ref_id TEXT NOT NULL,
  observed_at TEXT NOT NULL,
  confidence REAL NOT NULL DEFAULT 1,
  evidence_level TEXT NOT NULL,
  payload_json TEXT NOT NULL DEFAULT '{}',
  dedupe_key TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL,
  CHECK (action_ref_type IN ('automation_action', 'chat_send_action')),
  CHECK (evidence_type IN (
    'outbound_message_observed', 'inbound_reply_observed',
    'conversation_created', 'greeting_state_changed',
    'page_state_confirmed', 'protocol_acknowledged', 'rejection_observed'
  )),
  CHECK (confidence >= 0 AND confidence <= 1),
  CHECK (evidence_level IN ('direct', 'strong_inferred', 'weak_inferred'))
);

CREATE INDEX IF NOT EXISTS idx_fj_execution_evidence_action
  ON fj_execution_evidence(action_ref_type, action_ref_id, observed_at DESC);

-- 每次 canonical meaning 变化均保留依据，raw action status 保持原样。
CREATE TABLE IF NOT EXISTS fj_execution_reconciliations (
  id TEXT PRIMARY KEY,
  action_ref_type TEXT NOT NULL,
  action_ref_id TEXT NOT NULL,
  previous_status TEXT NOT NULL,
  new_status TEXT NOT NULL,
  reconciled_at TEXT NOT NULL,
  reconciliation_reason TEXT NOT NULL,
  evidence_id TEXT NOT NULL,
  evidence_level TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY (evidence_id) REFERENCES fj_execution_evidence(id) ON DELETE CASCADE,
  UNIQUE (action_ref_type, action_ref_id, evidence_id, new_status),
  CHECK (action_ref_type IN ('automation_action', 'chat_send_action')),
  CHECK (evidence_level IN ('direct', 'strong_inferred', 'weak_inferred'))
);

CREATE INDEX IF NOT EXISTS idx_fj_execution_reconciliation_action
  ON fj_execution_reconciliations(action_ref_type, action_ref_id, reconciled_at DESC);

CREATE TABLE IF NOT EXISTS fj_conversation_insights (
  id TEXT PRIMARY KEY,
  run_id TEXT,
  session_id TEXT NOT NULL,
  job_id TEXT,
  status TEXT NOT NULL DEFAULT 'analyzed',
  insight_json TEXT NOT NULL DEFAULT '{}',
  model TEXT NOT NULL DEFAULT '',
  prompt_version TEXT NOT NULL DEFAULT '',
  analysis_version TEXT NOT NULL DEFAULT 'job-hunt-refresh-analysis-v1',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (run_id) REFERENCES fj_job_hunt_refresh_runs(id) ON DELETE CASCADE,
  FOREIGN KEY (session_id) REFERENCES fj_chat_sessions(id) ON DELETE CASCADE,
  FOREIGN KEY (job_id) REFERENCES fj_boss_jobs(id) ON DELETE SET NULL,
  UNIQUE (run_id, session_id, analysis_version),
  CHECK (status IN ('analyzed', 'skipped', 'failed'))
);

CREATE INDEX IF NOT EXISTS idx_fj_conversation_insights_session_time
  ON fj_conversation_insights(session_id, updated_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_fj_conversation_insights_single_current
  ON fj_conversation_insights(session_id, analysis_version)
  WHERE run_id IS NULL;

CREATE TABLE IF NOT EXISTS fj_chat_attention_states (
  session_id TEXT PRIMARY KEY,
  job_id TEXT,
  run_id TEXT,
  insight_id TEXT,
  attention_status TEXT NOT NULL DEFAULT 'unknown',
  display_label TEXT NOT NULL DEFAULT '待判断',
  recommended_action TEXT NOT NULL DEFAULT 'no_further_action',
  reason TEXT NOT NULL DEFAULT '',
  decision TEXT NOT NULL DEFAULT 'wait',
  reason_code TEXT NOT NULL DEFAULT '',
  recommended_at TEXT,
  priority INTEGER NOT NULL DEFAULT 0,
  evidence_message_ids_json TEXT NOT NULL DEFAULT '[]',
  source TEXT NOT NULL DEFAULT 'analysis',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (session_id) REFERENCES fj_chat_sessions(id) ON DELETE CASCADE,
  FOREIGN KEY (job_id) REFERENCES fj_boss_jobs(id) ON DELETE SET NULL,
  FOREIGN KEY (run_id) REFERENCES fj_job_hunt_refresh_runs(id) ON DELETE SET NULL,
  FOREIGN KEY (insight_id) REFERENCES fj_conversation_insights(id) ON DELETE SET NULL,
  CHECK (attention_status IN (
    'needs_reply', 'needs_resume', 'needs_followup', 'needs_rejection_reason',
    'needs_interview_confirm', 'needs_info', 'waiting', 'no_action', 'unknown'
  )),
  CHECK (recommended_action IN (
    'reply_recruiter', 'send_resume', 'follow_up', 'ask_rejection_reason',
    'confirm_interview', 'provide_information', 'wait_for_recruiter',
    'no_further_action'
  )),
  CHECK (decision IN ('follow', 'wait', 'do_not_follow'))
);

CREATE INDEX IF NOT EXISTS idx_fj_chat_attention_states_status
  ON fj_chat_attention_states(attention_status, updated_at DESC);

CREATE TABLE IF NOT EXISTS fj_job_hunt_refresh_analysis_items (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  session_id TEXT NOT NULL,
  job_id TEXT,
  status TEXT NOT NULL DEFAULT 'pending',
  result_json TEXT NOT NULL DEFAULT '{}',
  error_category TEXT,
  error_message TEXT,
  started_at TEXT,
  completed_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (run_id) REFERENCES fj_job_hunt_refresh_runs(id) ON DELETE CASCADE,
  FOREIGN KEY (session_id) REFERENCES fj_chat_sessions(id) ON DELETE CASCADE,
  FOREIGN KEY (job_id) REFERENCES fj_boss_jobs(id) ON DELETE SET NULL,
  UNIQUE (run_id, session_id),
  CHECK (status IN ('pending', 'running', 'analyzed', 'skipped', 'failed'))
);

CREATE INDEX IF NOT EXISTS idx_fj_job_hunt_refresh_analysis_items_run_status
  ON fj_job_hunt_refresh_analysis_items(run_id, status, created_at);

CREATE TABLE IF NOT EXISTS fj_job_hunt_refresh_analysis_contexts (
  run_id TEXT PRIMARY KEY,
  status TEXT NOT NULL DEFAULT 'prepared',
  context_json TEXT NOT NULL DEFAULT '{}',
  context_characters INTEGER NOT NULL DEFAULT 0,
  max_context_characters INTEGER NOT NULL DEFAULT 0,
  blocker_reason TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (run_id) REFERENCES fj_job_hunt_refresh_runs(id) ON DELETE CASCADE,
  CHECK (status IN ('prepared', 'blocked'))
);

CREATE TABLE IF NOT EXISTS fj_codex_sessions (
  id TEXT PRIMARY KEY,
  status TEXT NOT NULL DEFAULT 'stopped',
  started_at TEXT,
  exited_at TEXT,
  exit_reason TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  CHECK (status IN ('stopped', 'starting', 'running', 'interrupting', 'exited', 'failed'))
);

-- 范围快照保存平台列表刷新后确认的固定处理集合，Run 创建后不会重新计算。
CREATE TABLE IF NOT EXISTS fj_job_hunt_refresh_scopes (
  id TEXT PRIMARY KEY,
  selected_since_time TEXT NOT NULL,
  requested_source_mode TEXT NOT NULL DEFAULT 'auto',
  scope_source TEXT NOT NULL DEFAULT 'refresh',
  account_uid TEXT NOT NULL,
  source_url TEXT NOT NULL DEFAULT '',
  friend_list_synced_at TEXT NOT NULL,
  chat_list_synced_at TEXT,
  scope_generated_at TEXT NOT NULL,
  latest_local_message_at TEXT,
  session_ids_in_scope_json TEXT NOT NULL DEFAULT '[]',
  session_ids_json TEXT NOT NULL DEFAULT '[]',
  new_session_ids_json TEXT NOT NULL DEFAULT '[]',
  related_jobs_json TEXT NOT NULL DEFAULT '[]',
  jobs_to_collect_json TEXT NOT NULL DEFAULT '[]',
  jobs_missing_jd_json TEXT NOT NULL DEFAULT '[]',
  jobs_missing_evaluation_json TEXT NOT NULL DEFAULT '[]',
  unresolved_session_ids_json TEXT NOT NULL DEFAULT '[]',
  counts_json TEXT NOT NULL DEFAULT '{}',
  friend_list_result_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  CHECK (requested_source_mode IN ('auto', 'local', 'refresh')),
  CHECK (scope_source IN ('local', 'refresh'))
);

CREATE INDEX IF NOT EXISTS idx_fj_job_hunt_refresh_scopes_generated_at
  ON fj_job_hunt_refresh_scopes(scope_generated_at DESC);

-- 求职数据更新 Run 保存用户选择、执行进度和最终摘要，可跨页面与应用重启恢复。
CREATE TABLE IF NOT EXISTS fj_job_hunt_refresh_runs (
  id TEXT PRIMARY KEY,
  scope_id TEXT NOT NULL UNIQUE,
  scope_generated_at TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  selected_since_time TEXT NOT NULL,
  latest_local_message_at TEXT,
  workflow_options_json TEXT NOT NULL DEFAULT '{}',
  estimated_sessions INTEGER NOT NULL DEFAULT 0,
  estimated_update_sessions INTEGER NOT NULL DEFAULT 0,
  estimated_jobs INTEGER NOT NULL DEFAULT 0,
  estimated_refresh_jobs INTEGER NOT NULL DEFAULT 0,
  estimated_missing_jd INTEGER NOT NULL DEFAULT 0,
  estimated_missing_suggestions INTEGER NOT NULL DEFAULT 0,
  processed_sessions INTEGER NOT NULL DEFAULT 0,
  processed_jobs INTEGER NOT NULL DEFAULT 0,
  failed_sessions INTEGER NOT NULL DEFAULT 0,
  failed_jobs INTEGER NOT NULL DEFAULT 0,
  chat_list_status TEXT NOT NULL DEFAULT 'skipped',
  chat_list_retryable INTEGER NOT NULL DEFAULT 0,
  current_step TEXT NOT NULL DEFAULT 'waiting',
  trigger_source TEXT NOT NULL DEFAULT 'page',
  codex_session_ref TEXT,
  summary_json TEXT NOT NULL DEFAULT '{}',
  error_summary TEXT,
  prompt_submitted_at TEXT,
  started_at TEXT,
  completed_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (scope_id) REFERENCES fj_job_hunt_refresh_scopes(id),
  CHECK (status IN ('pending', 'running', 'completed', 'completed_with_errors', 'failed', 'cancelled')),
  CHECK (chat_list_status IN ('pending', 'running', 'succeeded', 'failed', 'skipped')),
  CHECK (chat_list_retryable IN (0, 1))
);

CREATE INDEX IF NOT EXISTS idx_fj_job_hunt_refresh_runs_created_at
  ON fj_job_hunt_refresh_runs(created_at DESC);

-- Item 按会话保存聊天同步和关联岗位采集状态，恢复时不会重跑 succeeded 项。
CREATE TABLE IF NOT EXISTS fj_job_hunt_refresh_items (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  item_type TEXT NOT NULL,
  entity_id TEXT NOT NULL,
  session_id TEXT NOT NULL,
  job_id TEXT,
  status TEXT NOT NULL DEFAULT 'pending',
  step TEXT NOT NULL,
  retryable INTEGER NOT NULL DEFAULT 1,
  operation_ref_type TEXT,
  operation_ref_id TEXT,
  result_json TEXT NOT NULL DEFAULT '{}',
  error_category TEXT,
  error_message TEXT,
  started_at TEXT,
  completed_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (run_id) REFERENCES fj_job_hunt_refresh_runs(id) ON DELETE CASCADE,
  FOREIGN KEY (session_id) REFERENCES fj_chat_sessions(id) ON DELETE CASCADE,
  FOREIGN KEY (job_id) REFERENCES fj_boss_jobs(id) ON DELETE SET NULL,
  UNIQUE (run_id, item_type, entity_id),
  CHECK (item_type IN ('chat_session', 'related_job')),
  CHECK (status IN ('pending', 'running', 'succeeded', 'failed', 'skipped')),
  CHECK (retryable IN (0, 1))
);

CREATE INDEX IF NOT EXISTS idx_fj_job_hunt_refresh_items_run_status
  ON fj_job_hunt_refresh_items(run_id, item_type, status, created_at);

CREATE INDEX IF NOT EXISTS idx_fj_codex_sessions_updated_at
  ON fj_codex_sessions(updated_at DESC);

CREATE TABLE IF NOT EXISTS fj_fact_resume_links (
  fact_id TEXT NOT NULL,
  resume_version_id TEXT NOT NULL,
  linked_by TEXT NOT NULL DEFAULT 'user',
  created_at TEXT NOT NULL,
  PRIMARY KEY (fact_id, resume_version_id),
  FOREIGN KEY (fact_id) REFERENCES fj_profile_facts(id) ON DELETE CASCADE,
  FOREIGN KEY (resume_version_id) REFERENCES fj_resume_versions(id) ON DELETE CASCADE,
  CHECK (linked_by IN ('ai_extraction', 'user', 'derived', 'migration'))
);

CREATE INDEX IF NOT EXISTS idx_fj_fact_resume_links_resume
  ON fj_fact_resume_links(resume_version_id, fact_id);

CREATE TABLE IF NOT EXISTS fj_question_resume_links (
  question_id TEXT NOT NULL,
  resume_version_id TEXT NOT NULL,
  linked_by TEXT NOT NULL DEFAULT 'user',
  created_at TEXT NOT NULL,
  PRIMARY KEY (question_id, resume_version_id),
  FOREIGN KEY (question_id) REFERENCES fj_profile_questions(id) ON DELETE CASCADE,
  FOREIGN KEY (resume_version_id) REFERENCES fj_resume_versions(id) ON DELETE CASCADE,
  CHECK (linked_by IN ('ai_extraction', 'user', 'derived', 'migration'))
);

CREATE INDEX IF NOT EXISTS idx_fj_question_resume_links_resume
  ON fj_question_resume_links(resume_version_id, question_id);

CREATE TABLE IF NOT EXISTS fj_profile_question_evidence (
  id TEXT PRIMARY KEY,
  question_id TEXT NOT NULL,
  source_id TEXT,
  resume_version_id TEXT,
  source_excerpt TEXT NOT NULL,
  extraction_method TEXT NOT NULL DEFAULT 'ai',
  confidence REAL NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  FOREIGN KEY (question_id) REFERENCES fj_profile_questions(id) ON DELETE CASCADE,
  FOREIGN KEY (source_id) REFERENCES fj_profile_sources(id) ON DELETE SET NULL,
  FOREIGN KEY (resume_version_id) REFERENCES fj_resume_versions(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_fj_profile_question_evidence_question
  ON fj_profile_question_evidence(question_id, created_at DESC);

CREATE TABLE IF NOT EXISTS fj_profile_qa_revisions (
  id TEXT PRIMARY KEY,
  question_id TEXT NOT NULL,
  revision INTEGER NOT NULL,
  answer_json TEXT NOT NULL,
  source_type TEXT NOT NULL DEFAULT 'user',
  status TEXT NOT NULL DEFAULT 'current',
  created_at TEXT NOT NULL,
  FOREIGN KEY (question_id) REFERENCES fj_profile_questions(id) ON DELETE CASCADE,
  UNIQUE (question_id, revision),
  CHECK (source_type IN ('user', 'ai_extraction', 'restored', 'migration')),
  CHECK (status IN ('current', 'history'))
);

CREATE INDEX IF NOT EXISTS idx_fj_profile_qa_revisions_question
  ON fj_profile_qa_revisions(question_id, revision DESC);

CREATE TABLE IF NOT EXISTS fj_profile_qa_templates (
  id TEXT PRIMARY KEY,
  profile_id TEXT NOT NULL,
  question_key TEXT NOT NULL,
  question_text TEXT NOT NULL,
  reason TEXT NOT NULL DEFAULT '',
  answer_type TEXT NOT NULL DEFAULT 'text',
  required_stage TEXT NOT NULL DEFAULT 'chat',
  priority TEXT NOT NULL DEFAULT 'medium',
  writes_to_field TEXT,
  enabled INTEGER NOT NULL DEFAULT 1,
  sort_order INTEGER NOT NULL DEFAULT 0,
  source_type TEXT NOT NULL DEFAULT 'system',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (profile_id) REFERENCES fj_candidate_profiles(id) ON DELETE CASCADE,
  UNIQUE (profile_id, question_key),
  CHECK (source_type IN ('system', 'user')),
  CHECK (enabled IN (0, 1))
);

CREATE INDEX IF NOT EXISTS idx_fj_profile_qa_templates_profile
  ON fj_profile_qa_templates(profile_id, enabled, sort_order, updated_at DESC);

CREATE TABLE IF NOT EXISTS fj_profile_issues_v3 (
  id TEXT PRIMARY KEY,
  profile_id TEXT NOT NULL,
  resume_version_id TEXT,
  source_id TEXT,
  operation_run_id TEXT,
  issue_type TEXT NOT NULL,
  title TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  source_excerpt TEXT NOT NULL DEFAULT '',
  payload_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'pending',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  resolved_at TEXT,
  FOREIGN KEY (profile_id) REFERENCES fj_candidate_profiles(id) ON DELETE CASCADE,
  FOREIGN KEY (resume_version_id) REFERENCES fj_resume_versions(id) ON DELETE SET NULL,
  FOREIGN KEY (source_id) REFERENCES fj_profile_sources(id) ON DELETE SET NULL,
  FOREIGN KEY (operation_run_id) REFERENCES fj_resume_analysis_operations(id) ON DELETE SET NULL,
  CHECK (issue_type IN ('uncertain_fact', 'fact_conflict', 'missing_information', 'missing_qa', 'qa_conflict', 'orphaned_profile_data', 'analysis_choice')),
  CHECK (status IN ('pending', 'organizing', 'awaiting_confirmation', 'resolved', 'dismissed'))
);

CREATE INDEX IF NOT EXISTS idx_fj_profile_issues_v3_profile
  ON fj_profile_issues_v3(profile_id, status, updated_at DESC);

CREATE TABLE IF NOT EXISTS fj_profile_issue_answers (
  id TEXT PRIMARY KEY,
  issue_id TEXT NOT NULL,
  answer_text TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY (issue_id) REFERENCES fj_profile_issues_v3(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_fj_profile_issue_answers_issue
  ON fj_profile_issue_answers(issue_id, created_at DESC);

CREATE TABLE IF NOT EXISTS fj_profile_issue_change_sets (
  id TEXT PRIMARY KEY,
  issue_id TEXT NOT NULL,
  answer_id TEXT NOT NULL,
  changes_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'draft',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  applied_at TEXT,
  FOREIGN KEY (issue_id) REFERENCES fj_profile_issues_v3(id) ON DELETE CASCADE,
  FOREIGN KEY (answer_id) REFERENCES fj_profile_issue_answers(id) ON DELETE CASCADE,
  CHECK (status IN ('draft', 'applied', 'discarded'))
);

CREATE INDEX IF NOT EXISTS idx_fj_profile_issue_change_sets_issue
  ON fj_profile_issue_change_sets(issue_id, created_at DESC);

CREATE TABLE IF NOT EXISTS fj_profile_context_heads (
  id TEXT PRIMARY KEY,
  profile_id TEXT NOT NULL,
  resume_version_id TEXT NOT NULL,
  view_type TEXT NOT NULL,
  current_revision_id TEXT,
  dependency_versions_json TEXT NOT NULL DEFAULT '{}',
  stale INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (profile_id) REFERENCES fj_candidate_profiles(id) ON DELETE CASCADE,
  FOREIGN KEY (resume_version_id) REFERENCES fj_resume_versions(id) ON DELETE CASCADE,
  UNIQUE (profile_id, resume_version_id, view_type),
  CHECK (view_type IN ('full', 'search', 'evaluation', 'chat')),
  CHECK (stale IN (0, 1))
);

CREATE TABLE IF NOT EXISTS fj_profile_context_revisions (
  id TEXT PRIMARY KEY,
  head_id TEXT NOT NULL,
  revision INTEGER NOT NULL,
  content TEXT NOT NULL,
  source_type TEXT NOT NULL DEFAULT 'generated',
  status TEXT NOT NULL DEFAULT 'draft',
  dependency_versions_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (head_id) REFERENCES fj_profile_context_heads(id) ON DELETE CASCADE,
  UNIQUE (head_id, revision),
  CHECK (source_type IN ('generated', 'user_edit', 'restored', 'migration')),
  CHECK (status IN ('draft', 'current', 'history'))
);

CREATE INDEX IF NOT EXISTS idx_fj_profile_context_revisions_head
  ON fj_profile_context_revisions(head_id, revision DESC);

CREATE TABLE IF NOT EXISTS fj_filter_strategy_search_keywords (
  id TEXT PRIMARY KEY,
  filter_strategy_id TEXT NOT NULL,
  keyword TEXT NOT NULL,
  reason TEXT NOT NULL DEFAULT '',
  enabled INTEGER NOT NULL DEFAULT 1,
  sort_order INTEGER NOT NULL DEFAULT 0,
  source_type TEXT NOT NULL DEFAULT 'user',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (filter_strategy_id) REFERENCES fj_job_filter_strategies(id) ON DELETE CASCADE,
  CHECK (enabled IN (0, 1)),
  CHECK (source_type IN ('user', 'ai', 'migration'))
);

CREATE INDEX IF NOT EXISTS idx_fj_filter_strategy_keywords_strategy
  ON fj_filter_strategy_search_keywords(filter_strategy_id, sort_order, created_at);

CREATE TABLE IF NOT EXISTS fj_strategy_change_sets (
  id TEXT PRIMARY KEY,
  profile_id TEXT NOT NULL,
  resume_version_id TEXT NOT NULL,
  strategy_type TEXT NOT NULL,
  target_strategy_id TEXT,
  payload_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'draft',
  operation_run_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  applied_at TEXT,
  FOREIGN KEY (profile_id) REFERENCES fj_candidate_profiles(id) ON DELETE CASCADE,
  FOREIGN KEY (resume_version_id) REFERENCES fj_resume_versions(id) ON DELETE CASCADE,
  CHECK (strategy_type IN ('filter', 'recommendation', 'search_keywords')),
  CHECK (status IN ('draft', 'applied', 'discarded'))
);

CREATE INDEX IF NOT EXISTS idx_fj_strategy_change_sets_profile
  ON fj_strategy_change_sets(profile_id, status, updated_at DESC);
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
            self._ensure_fj_delivery_strategy_columns(connection)
            self._ensure_fj_boss_executor_schema(connection)
            self._ensure_fj_company_governance_schema(connection)
            self._ensure_fj_execution_observability_schema(connection)
            self._ensure_codex_integration_schema(connection)
            self._ensure_resume_analysis_v2_schema(connection)
            self._ensure_resume_analysis_v3_schema(connection)
            self._ensure_job_hunt_refresh_schema(connection)
            self._ensure_job_hunt_analysis_schema(connection)
            self._ensure_job_progress_schema(connection)
            # 兼容升级只从可靠旧事实追加事件，并按完整事件流重放 shadow Pipeline。
            from backend.app.services.fine_job.job_activity import migrate_legacy_job_activity
            from backend.app.services.fine_job.execution_reconciliation import (
                initialize_execution_observability,
            )

            migrate_legacy_job_activity(connection)
            initialize_execution_observability(connection)

    def _ensure_job_hunt_refresh_schema(self, connection: sqlite3.Connection) -> None:
        """为已有数据库补齐 Refresh Scope 与 Run 关联字段。"""
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(fj_job_hunt_refresh_runs)")
        }
        if "scope_id" not in columns:
            connection.execute("ALTER TABLE fj_job_hunt_refresh_runs ADD COLUMN scope_id TEXT")
        if "scope_generated_at" not in columns:
            connection.execute(
                "ALTER TABLE fj_job_hunt_refresh_runs ADD COLUMN scope_generated_at TEXT"
            )
        if "prompt_submitted_at" not in columns:
            connection.execute(
                "ALTER TABLE fj_job_hunt_refresh_runs ADD COLUMN prompt_submitted_at TEXT"
            )
        scope_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(fj_job_hunt_refresh_scopes)")
        }
        scope_migrations = {
            "requested_source_mode": (
                "ALTER TABLE fj_job_hunt_refresh_scopes "
                "ADD COLUMN requested_source_mode TEXT NOT NULL DEFAULT 'auto'"
            ),
            "scope_source": (
                "ALTER TABLE fj_job_hunt_refresh_scopes "
                "ADD COLUMN scope_source TEXT NOT NULL DEFAULT 'refresh'"
            ),
            "chat_list_synced_at": (
                "ALTER TABLE fj_job_hunt_refresh_scopes ADD COLUMN chat_list_synced_at TEXT"
            ),
            "session_ids_in_scope_json": (
                "ALTER TABLE fj_job_hunt_refresh_scopes "
                "ADD COLUMN session_ids_in_scope_json TEXT NOT NULL DEFAULT '[]'"
            ),
        }
        for column, ddl in scope_migrations.items():
            if column not in scope_columns:
                connection.execute(ddl)

        # 旧版在 Prompt 提交前写入 waiting_chat_messages；无 Codex 引用且 Item 未启动时恢复真实语义。
        connection.execute(
            """
            UPDATE fj_job_hunt_refresh_runs
            SET current_step = 'waiting_codex', updated_at = COALESCE(updated_at, created_at)
            WHERE status = 'pending'
              AND current_step = 'waiting_chat_messages'
              AND codex_session_ref IS NULL
              AND NOT EXISTS (
                SELECT 1 FROM fj_job_hunt_refresh_items i
                WHERE i.run_id = fj_job_hunt_refresh_runs.id
                  AND (i.started_at IS NOT NULL OR i.status <> 'pending')
              )
            """
        )

    def _ensure_fj_execution_observability_schema(
        self,
        connection: sqlite3.Connection,
    ) -> None:
        automation_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(fj_automation_actions)")
        }
        for column, definition in (
            ("executor_id", "TEXT"),
            ("started_at", "TEXT"),
            ("dispatch_started_at", "TEXT"),
            ("attempt_count", "INTEGER NOT NULL DEFAULT 0"),
            ("canonical_status", "TEXT"),
            ("canonical_updated_at", "TEXT"),
            ("canonical_reason", "TEXT NOT NULL DEFAULT ''"),
        ):
            if column not in automation_columns:
                connection.execute(
                    f"ALTER TABLE fj_automation_actions ADD COLUMN {column} {definition}"
                )

        chat_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(fj_chat_send_actions)")
        }
        for column, definition in (
            ("canonical_status", "TEXT"),
            ("canonical_updated_at", "TEXT"),
            ("canonical_reason", "TEXT NOT NULL DEFAULT ''"),
        ):
            if column not in chat_columns:
                connection.execute(
                    f"ALTER TABLE fj_chat_send_actions ADD COLUMN {column} {definition}"
                )

    def _ensure_job_hunt_analysis_schema(self, connection: sqlite3.Connection) -> None:
        """补齐求职数据更新的会话分析、列表提示和单项状态表。"""
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS fj_conversation_insights (
              id TEXT PRIMARY KEY,
              run_id TEXT,
              session_id TEXT NOT NULL,
              job_id TEXT,
              status TEXT NOT NULL DEFAULT 'analyzed',
              insight_json TEXT NOT NULL DEFAULT '{}',
              model TEXT NOT NULL DEFAULT '',
              prompt_version TEXT NOT NULL DEFAULT '',
              analysis_version TEXT NOT NULL DEFAULT 'job-hunt-refresh-analysis-v1',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              FOREIGN KEY (run_id) REFERENCES fj_job_hunt_refresh_runs(id) ON DELETE CASCADE,
              FOREIGN KEY (session_id) REFERENCES fj_chat_sessions(id) ON DELETE CASCADE,
              FOREIGN KEY (job_id) REFERENCES fj_boss_jobs(id) ON DELETE SET NULL,
              UNIQUE (run_id, session_id, analysis_version),
              CHECK (status IN ('analyzed', 'skipped', 'failed'))
            );

            CREATE INDEX IF NOT EXISTS idx_fj_conversation_insights_session_time
              ON fj_conversation_insights(session_id, updated_at DESC);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_fj_conversation_insights_single_current
              ON fj_conversation_insights(session_id, analysis_version)
              WHERE run_id IS NULL;

            CREATE TABLE IF NOT EXISTS fj_chat_attention_states (
              session_id TEXT PRIMARY KEY,
              job_id TEXT,
              run_id TEXT,
              insight_id TEXT,
              attention_status TEXT NOT NULL DEFAULT 'unknown',
              display_label TEXT NOT NULL DEFAULT '待判断',
              recommended_action TEXT NOT NULL DEFAULT 'no_further_action',
              reason TEXT NOT NULL DEFAULT '',
              decision TEXT NOT NULL DEFAULT 'wait',
              reason_code TEXT NOT NULL DEFAULT '',
              recommended_at TEXT,
              priority INTEGER NOT NULL DEFAULT 0,
              evidence_message_ids_json TEXT NOT NULL DEFAULT '[]',
              source TEXT NOT NULL DEFAULT 'analysis',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              FOREIGN KEY (session_id) REFERENCES fj_chat_sessions(id) ON DELETE CASCADE,
              FOREIGN KEY (job_id) REFERENCES fj_boss_jobs(id) ON DELETE SET NULL,
              FOREIGN KEY (run_id) REFERENCES fj_job_hunt_refresh_runs(id) ON DELETE SET NULL,
              FOREIGN KEY (insight_id) REFERENCES fj_conversation_insights(id) ON DELETE SET NULL,
              CHECK (attention_status IN (
                'needs_reply', 'needs_resume', 'needs_followup', 'needs_rejection_reason',
                'needs_interview_confirm', 'needs_info', 'waiting', 'no_action', 'unknown'
              )),
              CHECK (recommended_action IN (
                'reply_recruiter', 'send_resume', 'follow_up', 'ask_rejection_reason',
                'confirm_interview', 'provide_information', 'wait_for_recruiter',
                'no_further_action'
              )),
              CHECK (decision IN ('follow', 'wait', 'do_not_follow'))
            );

            CREATE INDEX IF NOT EXISTS idx_fj_chat_attention_states_status
              ON fj_chat_attention_states(attention_status, updated_at DESC);

            CREATE TABLE IF NOT EXISTS fj_job_hunt_refresh_analysis_items (
              id TEXT PRIMARY KEY,
              run_id TEXT NOT NULL,
              session_id TEXT NOT NULL,
              job_id TEXT,
              status TEXT NOT NULL DEFAULT 'pending',
              result_json TEXT NOT NULL DEFAULT '{}',
              error_category TEXT,
              error_message TEXT,
              started_at TEXT,
              completed_at TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              FOREIGN KEY (run_id) REFERENCES fj_job_hunt_refresh_runs(id) ON DELETE CASCADE,
              FOREIGN KEY (session_id) REFERENCES fj_chat_sessions(id) ON DELETE CASCADE,
              FOREIGN KEY (job_id) REFERENCES fj_boss_jobs(id) ON DELETE SET NULL,
              UNIQUE (run_id, session_id),
              CHECK (status IN ('pending', 'running', 'analyzed', 'skipped', 'failed'))
            );

            CREATE INDEX IF NOT EXISTS idx_fj_job_hunt_refresh_analysis_items_run_status
              ON fj_job_hunt_refresh_analysis_items(run_id, status, created_at);

            CREATE TABLE IF NOT EXISTS fj_job_hunt_refresh_analysis_contexts (
              run_id TEXT PRIMARY KEY,
              status TEXT NOT NULL DEFAULT 'prepared',
              context_json TEXT NOT NULL DEFAULT '{}',
              context_characters INTEGER NOT NULL DEFAULT 0,
              max_context_characters INTEGER NOT NULL DEFAULT 0,
              blocker_reason TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              FOREIGN KEY (run_id) REFERENCES fj_job_hunt_refresh_runs(id) ON DELETE CASCADE,
              CHECK (status IN ('prepared', 'blocked'))
            );
            """
        )

        attention_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(fj_chat_attention_states)")
        }
        for column, definition in (
            ("decision", "TEXT NOT NULL DEFAULT 'wait'"),
            ("reason_code", "TEXT NOT NULL DEFAULT ''"),
            ("recommended_at", "TEXT"),
        ):
            if column not in attention_columns:
                connection.execute(
                    f"ALTER TABLE fj_chat_attention_states ADD COLUMN {column} {definition}"
                )

        reply_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(fj_chat_reply_tasks)")
        }
        if "action_kind" not in reply_columns:
            connection.execute(
                "ALTER TABLE fj_chat_reply_tasks ADD COLUMN action_kind TEXT NOT NULL DEFAULT 'reply'"
            )
        if "insight_id" not in reply_columns:
            connection.execute("ALTER TABLE fj_chat_reply_tasks ADD COLUMN insight_id TEXT")

    def _ensure_job_progress_schema(self, connection: sqlite3.Connection) -> None:
        """升级正式求职进展表，并保留已有事件、终态与分析结果。"""
        table_sql = {
            name: str(row["sql"] or "") if row else ""
            for name in (
                "fj_job_applications",
                "fj_job_activity_events",
                "fj_job_pipeline_snapshots",
                "fj_execution_evidence",
                "fj_conversation_insights",
            )
            for row in [connection.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
                (name,),
            ).fetchone()]
        }
        pipeline_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(fj_job_pipeline_snapshots)")
        }
        required_activity_types = (
            "candidate_initiated_contact",
            "recruiter_initiated_contact",
            "conversation_state_analyzed",
            "resume_accepted",
            "resume_viewed",
            "under_review",
            "job_closed",
        )
        rebuild = {
            "applications": "'offer'" not in table_sql["fj_job_applications"],
            # 部分升级过的数据库可能已有个别新事件，必须逐项确认正式事件集合。
            "activities": any(
                f"'{event_type}'" not in table_sql["fj_job_activity_events"]
                for event_type in required_activity_types
            ),
            "pipeline": "waiting_on" not in pipeline_columns,
            "evidence": "'rejection_observed'" not in table_sql["fj_execution_evidence"],
            "insights": "run_id TEXT NOT NULL" in table_sql["fj_conversation_insights"],
        }
        if not any(rebuild.values()):
            return

        connection.commit()
        connection.execute("PRAGMA foreign_keys = OFF")
        try:
            if rebuild["applications"]:
                connection.executescript(
                    """
                    CREATE TABLE fj_job_applications_next (
                      id TEXT PRIMARY KEY, job_id TEXT NOT NULL UNIQUE, company_id TEXT,
                      status TEXT DEFAULT 'pending_greeting', source TEXT NOT NULL DEFAULT 'manual',
                      source_action_id TEXT, evidence_level TEXT NOT NULL DEFAULT 'confirmed',
                      applied_at TEXT NOT NULL, note TEXT NOT NULL DEFAULT '',
                      created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                      FOREIGN KEY (job_id) REFERENCES fj_boss_jobs(id) ON DELETE CASCADE,
                      FOREIGN KEY (company_id) REFERENCES fj_companies(id) ON DELETE SET NULL,
                      CHECK (status IS NULL OR status IN (
                        'pending_greeting', 'pending_application', 'communicating',
                        'offer', 'rejected', 'closed'
                      )),
                      CHECK (source IN ('boss_action', 'manual', 'mcp', 'migration')),
                      CHECK (evidence_level IN ('confirmed', 'inferred'))
                    );
                    INSERT INTO fj_job_applications_next
                    SELECT * FROM fj_job_applications;
                    DROP TABLE fj_job_applications;
                    ALTER TABLE fj_job_applications_next RENAME TO fj_job_applications;
                    CREATE INDEX idx_fj_job_applications_status_time
                      ON fj_job_applications(status, applied_at DESC);
                    CREATE INDEX idx_fj_job_applications_company
                      ON fj_job_applications(company_id, status, applied_at DESC);
                    """
                )

            if rebuild["activities"]:
                connection.executescript(
                    """
                    CREATE TABLE fj_job_activity_events_next (
                      id TEXT PRIMARY KEY, job_id TEXT NOT NULL, company_id TEXT,
                      chat_session_id TEXT, event_type TEXT NOT NULL, occurred_at TEXT NOT NULL,
                      source TEXT NOT NULL, source_ref_type TEXT NOT NULL,
                      source_ref_id TEXT NOT NULL, confidence REAL NOT NULL DEFAULT 1,
                      evidence_level TEXT NOT NULL DEFAULT 'direct',
                      payload_json TEXT NOT NULL DEFAULT '{}', dedupe_key TEXT NOT NULL UNIQUE,
                      created_at TEXT NOT NULL,
                      FOREIGN KEY (job_id) REFERENCES fj_boss_jobs(id) ON DELETE CASCADE,
                      FOREIGN KEY (company_id) REFERENCES fj_companies(id) ON DELETE SET NULL,
                      FOREIGN KEY (chat_session_id) REFERENCES fj_chat_sessions(id) ON DELETE SET NULL,
                      CHECK (event_type IN (
                        'job_discovered', 'job_shortlisted',
                        'candidate_initiated_contact', 'recruiter_initiated_contact',
                        'conversation_state_analyzed',
                        'greeting_requested', 'greeting_sent', 'greeting_failed',
                        'recruiter_replied', 'candidate_replied',
                        'resume_requested', 'resume_submitted', 'resume_accepted', 'resume_viewed',
                        'under_review', 'interview_intent_detected', 'interview_invited',
                        'interview_scheduled', 'rejected', 'job_closed',
                        'followup_recommended', 'no_response_detected', 'offer_received',
                        'conversation_closed', 'manual_stage_changed'
                      )),
                      CHECK (confidence >= 0 AND confidence <= 1),
                      CHECK (evidence_level IN ('direct', 'strong_inferred', 'weak_inferred'))
                    );
                    INSERT INTO fj_job_activity_events_next
                    SELECT * FROM fj_job_activity_events;
                    DROP TABLE fj_job_activity_events;
                    ALTER TABLE fj_job_activity_events_next RENAME TO fj_job_activity_events;
                    CREATE INDEX idx_fj_job_activity_job_time
                      ON fj_job_activity_events(job_id, occurred_at DESC, created_at DESC);
                    CREATE INDEX idx_fj_job_activity_type_time
                      ON fj_job_activity_events(event_type, occurred_at DESC);
                    """
                )

            if rebuild["pipeline"]:
                connection.executescript(
                    """
                    CREATE TABLE fj_job_pipeline_snapshots_next (
                      job_id TEXT PRIMARY KEY, company_id TEXT, stage TEXT NOT NULL,
                      stage_source TEXT NOT NULL, stage_event_id TEXT NOT NULL,
                      stage_updated_at TEXT NOT NULL,
                      waiting_on TEXT NOT NULL DEFAULT 'unknown', waiting_since_at TEXT,
                      contact_origin TEXT NOT NULL DEFAULT 'unknown',
                      rejection_reason_source TEXT NOT NULL DEFAULT 'unknown',
                      rejection_reason_category TEXT NOT NULL DEFAULT 'unknown',
                      rejection_reason_summary TEXT NOT NULL DEFAULT '',
                      projection_version INTEGER NOT NULL DEFAULT 2,
                      created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                      FOREIGN KEY (job_id) REFERENCES fj_boss_jobs(id) ON DELETE CASCADE,
                      FOREIGN KEY (company_id) REFERENCES fj_companies(id) ON DELETE SET NULL,
                      FOREIGN KEY (stage_event_id) REFERENCES fj_job_activity_events(id) ON DELETE CASCADE,
                      CHECK (stage IN (
                        'discovered', 'shortlisted', 'greeted', 'communicating',
                        'resume_requested', 'resume_submitted', 'resume_viewed', 'under_review',
                        'interview_scheduling', 'interviewing', 'offer', 'rejected', 'closed'
                      )),
                      CHECK (waiting_on IN ('candidate', 'recruiter', 'none', 'unknown')),
                      CHECK (contact_origin IN (
                        'finejob_auto', 'candidate_initiated', 'recruiter_initiated',
                        'external_candidate_initiated', 'unknown'
                      )),
                      CHECK (rejection_reason_source IN ('recruiter_explicit', 'ai_inferred', 'unknown')),
                      CHECK (rejection_reason_category IN (
                        'experience', 'education', 'skills', 'industry_background', 'salary',
                        'location', 'availability', 'position_filled', 'headcount_closed',
                        'fit', 'other', 'unknown'
                      ))
                    );
                    INSERT INTO fj_job_pipeline_snapshots_next (
                      job_id, company_id, stage, stage_source, stage_event_id,
                      stage_updated_at, created_at, updated_at
                    )
                    SELECT job_id, company_id, stage, stage_source, stage_event_id,
                           stage_updated_at, created_at, updated_at
                    FROM fj_job_pipeline_snapshots;
                    DROP TABLE fj_job_pipeline_snapshots;
                    ALTER TABLE fj_job_pipeline_snapshots_next RENAME TO fj_job_pipeline_snapshots;
                    CREATE INDEX idx_fj_pipeline_stage_time
                      ON fj_job_pipeline_snapshots(stage, stage_updated_at DESC);
                    """
                )

            if rebuild["evidence"]:
                connection.executescript(
                    """
                    CREATE TABLE fj_execution_evidence_next (
                      id TEXT PRIMARY KEY, action_ref_type TEXT NOT NULL,
                      action_ref_id TEXT NOT NULL, evidence_type TEXT NOT NULL,
                      source TEXT NOT NULL, source_ref_type TEXT NOT NULL,
                      source_ref_id TEXT NOT NULL, observed_at TEXT NOT NULL,
                      confidence REAL NOT NULL DEFAULT 1, evidence_level TEXT NOT NULL,
                      payload_json TEXT NOT NULL DEFAULT '{}', dedupe_key TEXT NOT NULL UNIQUE,
                      created_at TEXT NOT NULL,
                      CHECK (action_ref_type IN ('automation_action', 'chat_send_action')),
                      CHECK (evidence_type IN (
                        'outbound_message_observed', 'inbound_reply_observed',
                        'conversation_created', 'greeting_state_changed',
                        'page_state_confirmed', 'protocol_acknowledged', 'rejection_observed'
                      )),
                      CHECK (confidence >= 0 AND confidence <= 1),
                      CHECK (evidence_level IN ('direct', 'strong_inferred', 'weak_inferred'))
                    );
                    INSERT INTO fj_execution_evidence_next
                    SELECT * FROM fj_execution_evidence;
                    DROP TABLE fj_execution_evidence;
                    ALTER TABLE fj_execution_evidence_next RENAME TO fj_execution_evidence;
                    CREATE INDEX idx_fj_execution_evidence_action
                      ON fj_execution_evidence(action_ref_type, action_ref_id, observed_at DESC);
                    """
                )

            if rebuild["insights"]:
                connection.executescript(
                    """
                    CREATE TABLE fj_conversation_insights_next (
                      id TEXT PRIMARY KEY, run_id TEXT, session_id TEXT NOT NULL, job_id TEXT,
                      status TEXT NOT NULL DEFAULT 'analyzed', insight_json TEXT NOT NULL DEFAULT '{}',
                      model TEXT NOT NULL DEFAULT '', prompt_version TEXT NOT NULL DEFAULT '',
                      analysis_version TEXT NOT NULL DEFAULT 'job-hunt-refresh-analysis-v1',
                      created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                      FOREIGN KEY (run_id) REFERENCES fj_job_hunt_refresh_runs(id) ON DELETE CASCADE,
                      FOREIGN KEY (session_id) REFERENCES fj_chat_sessions(id) ON DELETE CASCADE,
                      FOREIGN KEY (job_id) REFERENCES fj_boss_jobs(id) ON DELETE SET NULL,
                      UNIQUE (run_id, session_id, analysis_version),
                      CHECK (status IN ('analyzed', 'skipped', 'failed'))
                    );
                    INSERT INTO fj_conversation_insights_next
                    SELECT * FROM fj_conversation_insights;
                    DROP TABLE fj_conversation_insights;
                    ALTER TABLE fj_conversation_insights_next RENAME TO fj_conversation_insights;
                    CREATE INDEX idx_fj_conversation_insights_session_time
                      ON fj_conversation_insights(session_id, updated_at DESC);
                    CREATE UNIQUE INDEX idx_fj_conversation_insights_single_current
                      ON fj_conversation_insights(session_id, analysis_version)
                      WHERE run_id IS NULL;
                    """
                )
            connection.commit()
        finally:
            connection.execute("PRAGMA foreign_keys = ON")

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.sqlite_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON;")
        # 在当前工作区沙箱中，`DELETE` 日志模式无法可靠移除回滚日志文件。
        # `TRUNCATE` 保留文件回滚语义，同时避免执行该删除步骤。
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
            "delivery_evaluation_json": (
                "ALTER TABLE fj_boss_jobs ADD COLUMN delivery_evaluation_json TEXT"
            ),
            "search_keyword": (
                "ALTER TABLE fj_boss_jobs ADD COLUMN search_keyword TEXT NOT NULL DEFAULT ''"
            ),
            "company_id": "ALTER TABLE fj_boss_jobs ADD COLUMN company_id TEXT",
            "is_test": "ALTER TABLE fj_boss_jobs ADD COLUMN is_test INTEGER NOT NULL DEFAULT 0",
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

    def _ensure_fj_company_governance_schema(
        self,
        connection: sqlite3.Connection,
    ) -> None:
        """补齐公司治理、投递事实和筛选冷却字段，并关联已有岗位。"""
        strategy_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(fj_job_filter_strategies)")
        }
        if "cooldown_rules_json" not in strategy_columns:
            connection.execute(
                "ALTER TABLE fj_job_filter_strategies ADD COLUMN cooldown_rules_json TEXT NOT NULL DEFAULT '{}'"
            )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_fj_boss_jobs_company_id ON fj_boss_jobs(company_id)"
        )

        # 旧库执行 DDL 时不会替换已有表，这里保证新增治理表完整存在。
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS fj_companies (
              id TEXT PRIMARY KEY, canonical_name TEXT NOT NULL,
              normalized_name TEXT NOT NULL UNIQUE,
              company_type TEXT NOT NULL DEFAULT 'unknown',
              classification_source TEXT NOT NULL DEFAULT 'capture',
              notes TEXT NOT NULL DEFAULT '', is_blacklisted INTEGER NOT NULL DEFAULT 0,
              blacklist_reason TEXT NOT NULL DEFAULT '', blacklisted_at TEXT,
              version INTEGER NOT NULL DEFAULT 1,
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_fj_companies_type_blacklisted
              ON fj_companies(company_type, is_blacklisted, updated_at DESC);
            CREATE TABLE IF NOT EXISTS fj_company_aliases (
              id TEXT PRIMARY KEY, company_id TEXT NOT NULL,
              alias_name TEXT NOT NULL, normalized_alias TEXT NOT NULL UNIQUE,
              created_at TEXT NOT NULL,
              FOREIGN KEY (company_id) REFERENCES fj_companies(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_fj_company_aliases_company
              ON fj_company_aliases(company_id, created_at DESC);
            CREATE TABLE IF NOT EXISTS fj_job_applications (
              id TEXT PRIMARY KEY, job_id TEXT NOT NULL UNIQUE, company_id TEXT,
              status TEXT DEFAULT 'pending_greeting', source TEXT NOT NULL DEFAULT 'manual',
              source_action_id TEXT, evidence_level TEXT NOT NULL DEFAULT 'confirmed',
              applied_at TEXT NOT NULL, note TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
              FOREIGN KEY (job_id) REFERENCES fj_boss_jobs(id) ON DELETE CASCADE,
              FOREIGN KEY (company_id) REFERENCES fj_companies(id) ON DELETE SET NULL,
              CHECK (status IS NULL OR status IN (
                'pending_greeting', 'pending_application', 'communicating',
                'offer', 'rejected', 'closed'
              )),
              CHECK (source IN ('boss_action', 'manual', 'mcp', 'migration')),
              CHECK (evidence_level IN ('confirmed', 'inferred'))
            );
            CREATE INDEX IF NOT EXISTS idx_fj_job_applications_status_time
              ON fj_job_applications(status, applied_at DESC);
            CREATE INDEX IF NOT EXISTS idx_fj_job_applications_company
              ON fj_job_applications(company_id, status, applied_at DESC);
            CREATE TABLE IF NOT EXISTS fj_filter_exclusion_states (
              strategy_id TEXT PRIMARY KEY, strategy_version INTEGER NOT NULL,
              status TEXT NOT NULL DEFAULT 'stale', last_full_refreshed_at TEXT,
              updated_at TEXT NOT NULL,
              FOREIGN KEY (strategy_id) REFERENCES fj_job_filter_strategies(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS fj_filter_exclusion_entries (
              id TEXT PRIMARY KEY, strategy_id TEXT NOT NULL,
              entity_type TEXT NOT NULL, entity_id TEXT NOT NULL,
              rule_type TEXT NOT NULL, source_event_at TEXT, excluded_until TEXT,
              reason TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              FOREIGN KEY (strategy_id) REFERENCES fj_job_filter_strategies(id) ON DELETE CASCADE,
              UNIQUE (strategy_id, entity_type, entity_id, rule_type)
            );
            CREATE INDEX IF NOT EXISTS idx_fj_filter_exclusion_entries_active
              ON fj_filter_exclusion_entries(strategy_id, entity_type, entity_id, excluded_until);
            """
        )

        self._migrate_fj_job_application_status(connection)

        # 每次初始化按最新包含规则校正旧岗位，已有外包配置升级后立即生效。
        from backend.app.services.fine_job.companies import reconcile_job_companies

        reconcile_job_companies(connection, source="migration")
        now = connection.execute(
            "SELECT strftime('%Y-%m-%dT%H:%M:%SZ', 'now') AS value"
        ).fetchone()["value"]

        # 已成功发送的打招呼动作迁移为已投递事实。
        action_rows = connection.execute(
            """
            SELECT a.id, a.job_id, COALESCE(a.completed_at, a.updated_at) AS applied_at,
                   j.company_id
            FROM fj_automation_actions a
            JOIN fj_boss_jobs j ON j.id = a.job_id
            WHERE a.status = 'succeeded'
            """
        ).fetchall()
        for row in action_rows:
            connection.execute(
                """
                INSERT OR IGNORE INTO fj_job_applications (
                  id, job_id, company_id, status, source, source_action_id,
                  evidence_level, applied_at, note, created_at, updated_at
                ) VALUES (?, ?, ?, 'pending_application', 'migration', ?, 'confirmed', ?, '', ?, ?)
                """,
                (
                    str(uuid4()), row["job_id"], row["company_id"], row["id"],
                    row["applied_at"] or now, now, now,
                ),
            )

    def _migrate_fj_job_application_status(
        self,
        connection: sqlite3.Connection,
    ) -> None:
        """把旧投递状态一次性转换为新的投递阶段枚举。"""
        table_row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'fj_job_applications'"
        ).fetchone()
        table_sql = str(table_row[0] or "") if table_row else ""
        if "'applied', 'cleared'" not in table_sql:
            return

        connection.execute("ALTER TABLE fj_job_applications RENAME TO fj_job_applications_legacy")
        connection.execute(
            """
            CREATE TABLE fj_job_applications (
              id TEXT PRIMARY KEY, job_id TEXT NOT NULL UNIQUE, company_id TEXT,
              status TEXT DEFAULT 'pending_greeting', source TEXT NOT NULL DEFAULT 'manual',
              source_action_id TEXT, evidence_level TEXT NOT NULL DEFAULT 'confirmed',
              applied_at TEXT NOT NULL, note TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
              FOREIGN KEY (job_id) REFERENCES fj_boss_jobs(id) ON DELETE CASCADE,
              FOREIGN KEY (company_id) REFERENCES fj_companies(id) ON DELETE SET NULL,
              CHECK (status IS NULL OR status IN (
                'pending_greeting', 'pending_application', 'communicating',
                'offer', 'rejected', 'closed'
              )),
              CHECK (source IN ('boss_action', 'manual', 'mcp', 'migration')),
              CHECK (evidence_level IN ('confirmed', 'inferred'))
            )
            """
        )
        connection.execute(
            """
            INSERT INTO fj_job_applications (
              id, job_id, company_id, status, source, source_action_id,
              evidence_level, applied_at, note, created_at, updated_at
            )
            SELECT
              old.id, old.job_id, old.company_id,
              CASE
                WHEN old.status = 'cleared' THEN 'pending_greeting'
                WHEN old.status = 'applied' THEN CASE
                  WHEN EXISTS (
                    SELECT 1 FROM fj_chat_messages m
                    WHERE m.session_id IN (
                      SELECT s.id FROM fj_chat_sessions s WHERE s.job_id = old.job_id
                    )
                    AND m.direction = 'outbound'
                    AND m.content = '附件状态更新'
                  ) THEN 'communicating'
                  ELSE 'pending_application'
                END
                WHEN old.status IN ('offer', 'rejected', 'closed') THEN old.status
                ELSE NULL
              END,
              old.source, old.source_action_id, old.evidence_level,
              old.applied_at, old.note, old.created_at, old.updated_at
            FROM fj_job_applications_legacy old
            """
        )
        connection.execute("DROP TABLE fj_job_applications_legacy")
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_fj_job_applications_status_time "
            "ON fj_job_applications(status, applied_at DESC)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_fj_job_applications_company "
            "ON fj_job_applications(company_id, status, applied_at DESC)"
        )

    def _ensure_fj_delivery_strategy_columns(
        self,
        connection: sqlite3.Connection,
    ) -> None:
        """为旧投递策略补充发送后页面验证开关，升级后默认不改变执行耗时。"""
        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(fj_delivery_strategies)")
        }
        if "force_contact_verification_enabled" not in columns:
            connection.execute(
                "ALTER TABLE fj_delivery_strategies ADD COLUMN force_contact_verification_enabled INTEGER NOT NULL DEFAULT 0"
            )

    def _ensure_fj_boss_executor_schema(
        self,
        connection: sqlite3.Connection,
    ) -> None:
        """补齐执行任务需要的状态字段。"""
        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(fj_automation_actions)")
        }
        migrations = {
            "execution_state": "ALTER TABLE fj_automation_actions ADD COLUMN execution_state TEXT NOT NULL DEFAULT 'queued'",
            "execution_epoch": "ALTER TABLE fj_automation_actions ADD COLUMN execution_epoch INTEGER NOT NULL DEFAULT 0",
            "last_status_code": "ALTER TABLE fj_automation_actions ADD COLUMN last_status_code TEXT",
            "result_json": "ALTER TABLE fj_automation_actions ADD COLUMN result_json TEXT NOT NULL DEFAULT '{}'",
            "task_type": "ALTER TABLE fj_automation_actions ADD COLUMN task_type TEXT NOT NULL DEFAULT 'BOSS_DEFAULT_GREETING'",
        }
        for column, ddl in migrations.items():
            if column not in columns:
                connection.execute(ddl)

        executor_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(fj_boss_executor_instances)")
        }
        executor_migrations = {
            "task_cooldown_max_seconds": "ALTER TABLE fj_boss_executor_instances ADD COLUMN task_cooldown_max_seconds INTEGER NOT NULL DEFAULT 4",
            "page_load_wait_max_seconds": "ALTER TABLE fj_boss_executor_instances ADD COLUMN page_load_wait_max_seconds INTEGER NOT NULL DEFAULT 3",
            "runtime_phase": "ALTER TABLE fj_boss_executor_instances ADD COLUMN runtime_phase TEXT NOT NULL DEFAULT 'idle'",
            "runtime_detail": "ALTER TABLE fj_boss_executor_instances ADD COLUMN runtime_detail TEXT NOT NULL DEFAULT ''",
            "runtime_until_at": "ALTER TABLE fj_boss_executor_instances ADD COLUMN runtime_until_at TEXT",
        }
        for column, ddl in executor_migrations.items():
            if column not in executor_columns:
                connection.execute(ddl)

        # 兼容早期已经写入岗位评估结果的数据。
        evaluation_rows = connection.execute(
            "SELECT id, payload_json FROM fj_boss_jobs WHERE delivery_evaluation_json IS NULL"
        ).fetchall()
        for row in evaluation_rows:
            try:
                payload = json.loads(row["payload_json"] or "{}")
            except json.JSONDecodeError:
                payload = {}
            evaluation = payload.get("delivery_evaluation")
            if evaluation is not None:
                connection.execute(
                    "UPDATE fj_boss_jobs SET delivery_evaluation_json = ? WHERE id = ?",
                    (json.dumps(evaluation, ensure_ascii=False), row["id"]),
                )

    def _ensure_codex_integration_schema(self, connection: sqlite3.Connection) -> None:
        """补齐 Codex 聚合上下文、授权和失效判断使用的业务版本字段。"""
        migrations = {
            "fj_profile_analysis_runs": {
                "error_category": "ALTER TABLE fj_profile_analysis_runs ADD COLUMN error_category TEXT",
            },
            "fj_resumes": {
                "facts_version": "ALTER TABLE fj_resumes ADD COLUMN facts_version INTEGER NOT NULL DEFAULT 1",
            },
            "fj_boss_jobs": {
                "detail_version": "ALTER TABLE fj_boss_jobs ADD COLUMN detail_version INTEGER NOT NULL DEFAULT 1",
            },
            "fj_job_evaluations": {
                "job_detail_version": "ALTER TABLE fj_job_evaluations ADD COLUMN job_detail_version INTEGER NOT NULL DEFAULT 1",
                "resume_facts_version": "ALTER TABLE fj_job_evaluations ADD COLUMN resume_facts_version INTEGER NOT NULL DEFAULT 1",
                "structure_version": "ALTER TABLE fj_job_evaluations ADD COLUMN structure_version INTEGER NOT NULL DEFAULT 1",
                "candidate_profile_id": "ALTER TABLE fj_job_evaluations ADD COLUMN candidate_profile_id TEXT",
                "profile_context_version": "ALTER TABLE fj_job_evaluations ADD COLUMN profile_context_version INTEGER",
                "resume_version_id": "ALTER TABLE fj_job_evaluations ADD COLUMN resume_version_id TEXT",
            },
            "fj_review_items": {
                "version": "ALTER TABLE fj_review_items ADD COLUMN version INTEGER NOT NULL DEFAULT 1",
                "content_categories_json": "ALTER TABLE fj_review_items ADD COLUMN content_categories_json TEXT NOT NULL DEFAULT '[]'",
                "classification_version": "ALTER TABLE fj_review_items ADD COLUMN classification_version INTEGER NOT NULL DEFAULT 1",
                "authorization_mode": "ALTER TABLE fj_review_items ADD COLUMN authorization_mode TEXT NOT NULL DEFAULT 'manual_confirmation'",
                "candidate_profile_id": "ALTER TABLE fj_review_items ADD COLUMN candidate_profile_id TEXT",
                "profile_context_version": "ALTER TABLE fj_review_items ADD COLUMN profile_context_version INTEGER",
                "resume_version_id": "ALTER TABLE fj_review_items ADD COLUMN resume_version_id TEXT",
            },
            "fj_automation_actions": {
                "authorization_mode": "ALTER TABLE fj_automation_actions ADD COLUMN authorization_mode TEXT NOT NULL DEFAULT 'manual_confirmation'",
                "authorization_source": "ALTER TABLE fj_automation_actions ADD COLUMN authorization_source TEXT NOT NULL DEFAULT 'confirmation'",
                "content_categories_json": "ALTER TABLE fj_automation_actions ADD COLUMN content_categories_json TEXT NOT NULL DEFAULT '[]'",
                "classification_version": "ALTER TABLE fj_automation_actions ADD COLUMN classification_version INTEGER NOT NULL DEFAULT 1",
            },
            "fj_chat_reply_tasks": {
                "text_version": "ALTER TABLE fj_chat_reply_tasks ADD COLUMN text_version INTEGER NOT NULL DEFAULT 1",
                "content_categories_json": "ALTER TABLE fj_chat_reply_tasks ADD COLUMN content_categories_json TEXT NOT NULL DEFAULT '[]'",
                "classification_version": "ALTER TABLE fj_chat_reply_tasks ADD COLUMN classification_version INTEGER NOT NULL DEFAULT 1",
                "candidate_profile_id": "ALTER TABLE fj_chat_reply_tasks ADD COLUMN candidate_profile_id TEXT",
                "profile_context_version": "ALTER TABLE fj_chat_reply_tasks ADD COLUMN profile_context_version INTEGER",
                "generation_due_at": "ALTER TABLE fj_chat_reply_tasks ADD COLUMN generation_due_at TEXT",
                "input_message_ids_json": "ALTER TABLE fj_chat_reply_tasks ADD COLUMN input_message_ids_json TEXT NOT NULL DEFAULT '[]'",
                "decision": "ALTER TABLE fj_chat_reply_tasks ADD COLUMN decision TEXT NOT NULL DEFAULT 'reply'",
                "facts_used_json": "ALTER TABLE fj_chat_reply_tasks ADD COLUMN facts_used_json TEXT NOT NULL DEFAULT '[]'",
                "warnings_json": "ALTER TABLE fj_chat_reply_tasks ADD COLUMN warnings_json TEXT NOT NULL DEFAULT '[]'",
                "requires_user_input": "ALTER TABLE fj_chat_reply_tasks ADD COLUMN requires_user_input INTEGER NOT NULL DEFAULT 0",
                "decision_reason": "ALTER TABLE fj_chat_reply_tasks ADD COLUMN decision_reason TEXT NOT NULL DEFAULT ''",
            },
            "fj_chat_send_actions": {
                "authorization_mode": "ALTER TABLE fj_chat_send_actions ADD COLUMN authorization_mode TEXT NOT NULL DEFAULT 'manual_confirmation'",
                "authorization_source": "ALTER TABLE fj_chat_send_actions ADD COLUMN authorization_source TEXT NOT NULL DEFAULT 'confirmation'",
                "content_categories_json": "ALTER TABLE fj_chat_send_actions ADD COLUMN content_categories_json TEXT NOT NULL DEFAULT '[]'",
                "classification_version": "ALTER TABLE fj_chat_send_actions ADD COLUMN classification_version INTEGER NOT NULL DEFAULT 1",
                "leader_tab_id": "ALTER TABLE fj_chat_send_actions ADD COLUMN leader_tab_id TEXT NOT NULL DEFAULT ''",
                "leader_epoch": "ALTER TABLE fj_chat_send_actions ADD COLUMN leader_epoch INTEGER NOT NULL DEFAULT 0",
                "dispatch_deadline_at": "ALTER TABLE fj_chat_send_actions ADD COLUMN dispatch_deadline_at TEXT",
                "platform_message_id": "ALTER TABLE fj_chat_send_actions ADD COLUMN platform_message_id TEXT NOT NULL DEFAULT ''",
                "client_mid": "ALTER TABLE fj_chat_send_actions ADD COLUMN client_mid TEXT NOT NULL DEFAULT ''",
            },
        }
        for table, table_migrations in migrations.items():
            columns = {
                row["name"] for row in connection.execute(f"PRAGMA table_info({table})")
            }
            for column, ddl in table_migrations.items():
                if column not in columns:
                    connection.execute(ddl)
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_fj_chat_reply_tasks_due "
            "ON fj_chat_reply_tasks(status, generation_due_at, created_at)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_fj_chat_send_actions_dispatch_deadline "
            "ON fj_chat_send_actions(status, dispatch_deadline_at)"
        )

    def _ensure_resume_analysis_v2_schema(self, connection: sqlite3.Connection) -> None:
        """补齐简历组、资料作用域和派生版本使用的 V2 字段。"""
        migrations = {
            "fj_profile_sources": {
                "resume_family_id": "ALTER TABLE fj_profile_sources ADD COLUMN resume_family_id TEXT",
                "editable_text": "ALTER TABLE fj_profile_sources ADD COLUMN editable_text TEXT NOT NULL DEFAULT ''",
            },
            "fj_resume_versions": {
                "resume_family_id": "ALTER TABLE fj_resume_versions ADD COLUMN resume_family_id TEXT",
                "parent_version_id": "ALTER TABLE fj_resume_versions ADD COLUMN parent_version_id TEXT",
                "version_type": "ALTER TABLE fj_resume_versions ADD COLUMN version_type TEXT NOT NULL DEFAULT 'base'",
                "target_job_id": "ALTER TABLE fj_resume_versions ADD COLUMN target_job_id TEXT",
                "derived_reason": "ALTER TABLE fj_resume_versions ADD COLUMN derived_reason TEXT NOT NULL DEFAULT ''",
                "based_on_content_version": "ALTER TABLE fj_resume_versions ADD COLUMN based_on_content_version INTEGER NOT NULL DEFAULT 1",
            },
            "fj_resume_families": {
                "base_version_id": "ALTER TABLE fj_resume_families ADD COLUMN base_version_id TEXT",
            },
            "fj_profile_facts": {
                "scope_type": "ALTER TABLE fj_profile_facts ADD COLUMN scope_type TEXT NOT NULL DEFAULT 'general'",
                "scope_id": "ALTER TABLE fj_profile_facts ADD COLUMN scope_id TEXT",
                "confirmed_by": "ALTER TABLE fj_profile_facts ADD COLUMN confirmed_by TEXT",
                "analysis_operation_run_id": "ALTER TABLE fj_profile_facts ADD COLUMN analysis_operation_run_id TEXT",
                "source_content_version": "ALTER TABLE fj_profile_facts ADD COLUMN source_content_version INTEGER",
            },
            "fj_profile_questions": {
                "scope_type": "ALTER TABLE fj_profile_questions ADD COLUMN scope_type TEXT NOT NULL DEFAULT 'general'",
                "scope_id": "ALTER TABLE fj_profile_questions ADD COLUMN scope_id TEXT",
                "scope_key": "ALTER TABLE fj_profile_questions ADD COLUMN scope_key TEXT NOT NULL DEFAULT 'general'",
                "confirmed_by": "ALTER TABLE fj_profile_questions ADD COLUMN confirmed_by TEXT",
                "analysis_operation_run_id": "ALTER TABLE fj_profile_questions ADD COLUMN analysis_operation_run_id TEXT",
                "source_content_version": "ALTER TABLE fj_profile_questions ADD COLUMN source_content_version INTEGER",
            },
            "fj_profile_artifacts": {
                "context_scope_id": "ALTER TABLE fj_profile_artifacts ADD COLUMN context_scope_id TEXT",
            },
            "fj_resume_search_keywords": {
                "version": "ALTER TABLE fj_resume_search_keywords ADD COLUMN version INTEGER NOT NULL DEFAULT 1",
                "status": "ALTER TABLE fj_resume_search_keywords ADD COLUMN status TEXT NOT NULL DEFAULT 'current'",
            },
        }
        for table, table_migrations in migrations.items():
            columns = {
                row["name"] for row in connection.execute(f"PRAGMA table_info({table})")
            }
            for column, ddl in table_migrations.items():
                if column not in columns:
                    connection.execute(ddl)

        # 旧数据优先沿用组内默认版本，其次采用最早的基础版本。
        connection.execute(
            """
            UPDATE fj_resume_families
            SET base_version_id = COALESCE(
              base_version_id,
              default_version_id,
              (
                SELECT v.id FROM fj_resume_versions v
                WHERE v.resume_family_id = fj_resume_families.id
                  AND v.version_type = 'base'
                ORDER BY v.created_at, v.id
                LIMIT 1
              )
            )
            WHERE base_version_id IS NULL
            """
        )

        # 识别稿默认采用当前识别正文，保证旧资料可以直接进入 V2 编辑流程。
        connection.execute(
            """
            UPDATE fj_profile_sources
            SET editable_text = CASE
              WHEN editable_text <> '' THEN editable_text
              WHEN recognized_text <> '' THEN recognized_text
              ELSE raw_text
            END
            """
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO fj_resume_families (
              id, profile_id, name, root_source_id, target_role_family,
              content_version, analysis_version, status, created_at, updated_at
            )
            SELECT 'family_' || id, profile_id, title, id, '', source_version, 0,
                   'active', created_at, updated_at
            FROM fj_profile_sources
            WHERE source_type = 'pdf'
            """
        )
        connection.execute(
            """
            UPDATE fj_profile_sources
            SET resume_family_id = 'family_' || id
            WHERE source_type = 'pdf' AND resume_family_id IS NULL
            """
        )
        connection.execute(
            """
            UPDATE fj_resume_versions
            SET resume_family_id = (
              SELECT resume_family_id FROM fj_profile_sources
              WHERE fj_profile_sources.id = fj_resume_versions.source_id
            )
            WHERE resume_family_id IS NULL AND source_id IS NOT NULL
            """
        )
        connection.execute(
            """
            UPDATE fj_profile_questions
            SET scope_type = 'resume_family',
                scope_id = (
                  SELECT resume_family_id FROM fj_profile_sources
                  WHERE fj_profile_sources.id = fj_profile_questions.source_id
                ),
                scope_key = (
                  SELECT resume_family_id FROM fj_profile_sources
                  WHERE fj_profile_sources.id = fj_profile_questions.source_id
                )
            WHERE source_id IS NOT NULL
              AND EXISTS (
                SELECT 1 FROM fj_profile_sources
                WHERE fj_profile_sources.id = fj_profile_questions.source_id
                  AND resume_family_id IS NOT NULL
              )
            """
        )
        self._rebuild_profile_questions_scope_constraint(connection)

    def _rebuild_profile_questions_scope_constraint(
        self,
        connection: sqlite3.Connection,
    ) -> None:
        """将旧版问题唯一键升级为“档案 + 作用域 + 问题键”。"""
        row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'fj_profile_questions'"
        ).fetchone()
        table_sql = str(row["sql"] or "") if row else ""
        if "UNIQUE (profile_id, question_key)" not in table_sql:
            return

        connection.commit()
        connection.execute("PRAGMA foreign_keys = OFF")
        try:
            connection.execute(
                """
                CREATE TABLE fj_profile_questions_v2 (
                  id TEXT PRIMARY KEY,
                  profile_id TEXT NOT NULL,
                  scope_type TEXT NOT NULL DEFAULT 'general',
                  scope_id TEXT,
                  scope_key TEXT NOT NULL DEFAULT 'general',
                  question_key TEXT NOT NULL,
                  question_text TEXT NOT NULL,
                  reason TEXT NOT NULL DEFAULT '',
                  origin TEXT NOT NULL DEFAULT 'user',
                  answer_type TEXT NOT NULL DEFAULT 'text',
                  required_stage TEXT NOT NULL DEFAULT 'chat',
                  priority TEXT NOT NULL DEFAULT 'medium',
                  proposed_answer_json TEXT,
                  final_answer_json TEXT,
                  status TEXT NOT NULL DEFAULT 'pending',
                  external_use TEXT NOT NULL DEFAULT 'prohibited',
                  valid_until TEXT,
                  source_id TEXT,
                  job_id TEXT,
                  writes_to_field TEXT,
                  enabled INTEGER NOT NULL DEFAULT 1,
                  confirmed_by TEXT,
                  analysis_operation_run_id TEXT,
                  source_content_version INTEGER,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL,
                  FOREIGN KEY (profile_id) REFERENCES fj_candidate_profiles(id) ON DELETE CASCADE,
                  FOREIGN KEY (source_id) REFERENCES fj_profile_sources(id) ON DELETE SET NULL,
                  UNIQUE (profile_id, scope_key, question_key),
                  CHECK (scope_type IN ('general', 'resume_family')),
                  CHECK (origin IN ('default', 'resume_analysis', 'jd_analysis', 'user')),
                  CHECK (answer_type IN ('text', 'number', 'date', 'range', 'select', 'multi_select', 'boolean')),
                  CHECK (required_stage IN ('search', 'greeting', 'application', 'chat', 'interview')),
                  CHECK (priority IN ('high', 'medium', 'low')),
                  CHECK (status IN ('pending', 'proposed_answer', 'answered', 'confirmed', 'declined', 'conflicted', 'stale')),
                  CHECK (external_use IN ('prohibited', 'summary_only', 'allowed')),
                  CHECK (enabled IN (0, 1))
                )
                """
            )
            connection.execute(
                """
                INSERT INTO fj_profile_questions_v2 (
                  id, profile_id, scope_type, scope_id, scope_key, question_key,
                  question_text, reason, origin, answer_type, required_stage,
                  priority, proposed_answer_json, final_answer_json, status,
                  external_use, valid_until, source_id, job_id, writes_to_field,
                  enabled, confirmed_by, analysis_operation_run_id,
                  source_content_version, created_at, updated_at
                )
                SELECT id, profile_id, scope_type, scope_id,
                       CASE WHEN scope_type = 'resume_family' AND scope_id IS NOT NULL
                            THEN scope_id ELSE 'general' END,
                       question_key, question_text, reason, origin, answer_type,
                       required_stage, priority, proposed_answer_json,
                       final_answer_json, status, external_use, valid_until,
                       source_id, job_id, writes_to_field, enabled, confirmed_by,
                       analysis_operation_run_id, source_content_version,
                       created_at, updated_at
                FROM fj_profile_questions
                """
            )
            connection.execute("DROP TABLE fj_profile_questions")
            connection.execute("ALTER TABLE fj_profile_questions_v2 RENAME TO fj_profile_questions")
            connection.execute(
                """
                CREATE INDEX idx_fj_profile_questions_profile
                ON fj_profile_questions(profile_id, enabled, priority, updated_at DESC)
                """
            )
            connection.commit()
        finally:
            connection.execute("PRAGMA foreign_keys = ON")

    def _ensure_resume_analysis_v3_schema(self, connection: sqlite3.Connection) -> None:
        """补齐 V3 的具体简历关联、上下文修订和下游快照字段。"""
        migrations = {
            "fj_profile_sources": {
                "resume_version_id": "ALTER TABLE fj_profile_sources ADD COLUMN resume_version_id TEXT",
            },
            "fj_resume_families": {
                "default_delivery_version_id": "ALTER TABLE fj_resume_families ADD COLUMN default_delivery_version_id TEXT",
            },
            "fj_resume_versions": {
                "current_role": "ALTER TABLE fj_resume_versions ADD COLUMN current_role TEXT NOT NULL DEFAULT 'derived'",
                "origin_type": "ALTER TABLE fj_resume_versions ADD COLUMN origin_type TEXT NOT NULL DEFAULT 'manual_copy'",
                "derived_from_version_id": "ALTER TABLE fj_resume_versions ADD COLUMN derived_from_version_id TEXT",
                "target_job_snapshot_json": "ALTER TABLE fj_resume_versions ADD COLUMN target_job_snapshot_json TEXT NOT NULL DEFAULT '{}'",
                "deleted_at": "ALTER TABLE fj_resume_versions ADD COLUMN deleted_at TEXT",
            },
            "fj_profile_facts": {
                "applies_to_all_resumes": "ALTER TABLE fj_profile_facts ADD COLUMN applies_to_all_resumes INTEGER NOT NULL DEFAULT 0",
            },
            "fj_profile_questions": {
                "applies_to_all_resumes": "ALTER TABLE fj_profile_questions ADD COLUMN applies_to_all_resumes INTEGER NOT NULL DEFAULT 0",
            },
            "fj_resume_analysis_runs": {
                "resume_version_id": "ALTER TABLE fj_resume_analysis_runs ADD COLUMN resume_version_id TEXT",
            },
            "fj_job_filter_strategies": {
                "candidate_profile_id": "ALTER TABLE fj_job_filter_strategies ADD COLUMN candidate_profile_id TEXT",
                "resume_version_id": "ALTER TABLE fj_job_filter_strategies ADD COLUMN resume_version_id TEXT",
                "source_type": "ALTER TABLE fj_job_filter_strategies ADD COLUMN source_type TEXT NOT NULL DEFAULT 'user'",
                "strategy_version": "ALTER TABLE fj_job_filter_strategies ADD COLUMN strategy_version INTEGER NOT NULL DEFAULT 1",
                "based_on_analysis_run_id": "ALTER TABLE fj_job_filter_strategies ADD COLUMN based_on_analysis_run_id TEXT",
                "based_on_resume_content_version": "ALTER TABLE fj_job_filter_strategies ADD COLUMN based_on_resume_content_version INTEGER",
                "based_on_facts_version": "ALTER TABLE fj_job_filter_strategies ADD COLUMN based_on_facts_version INTEGER",
                "based_on_qa_version": "ALTER TABLE fj_job_filter_strategies ADD COLUMN based_on_qa_version INTEGER",
            },
            "fj_job_recommendation_strategies": {
                "candidate_profile_id": "ALTER TABLE fj_job_recommendation_strategies ADD COLUMN candidate_profile_id TEXT",
                "resume_version_id": "ALTER TABLE fj_job_recommendation_strategies ADD COLUMN resume_version_id TEXT",
                "source_type": "ALTER TABLE fj_job_recommendation_strategies ADD COLUMN source_type TEXT NOT NULL DEFAULT 'user'",
                "strategy_version": "ALTER TABLE fj_job_recommendation_strategies ADD COLUMN strategy_version INTEGER NOT NULL DEFAULT 1",
                "based_on_analysis_run_id": "ALTER TABLE fj_job_recommendation_strategies ADD COLUMN based_on_analysis_run_id TEXT",
                "based_on_resume_content_version": "ALTER TABLE fj_job_recommendation_strategies ADD COLUMN based_on_resume_content_version INTEGER",
                "based_on_facts_version": "ALTER TABLE fj_job_recommendation_strategies ADD COLUMN based_on_facts_version INTEGER",
                "based_on_qa_version": "ALTER TABLE fj_job_recommendation_strategies ADD COLUMN based_on_qa_version INTEGER",
            },
            "fj_job_evaluations": {
                "context_revision_id": "ALTER TABLE fj_job_evaluations ADD COLUMN context_revision_id TEXT",
                "filter_strategy_version": "ALTER TABLE fj_job_evaluations ADD COLUMN filter_strategy_version INTEGER",
                "recommendation_strategy_version": "ALTER TABLE fj_job_evaluations ADD COLUMN recommendation_strategy_version INTEGER",
                "profile_facts_version": "ALTER TABLE fj_job_evaluations ADD COLUMN profile_facts_version INTEGER",
                "profile_questions_version": "ALTER TABLE fj_job_evaluations ADD COLUMN profile_questions_version INTEGER",
                "candidate_snapshot_json": "ALTER TABLE fj_job_evaluations ADD COLUMN candidate_snapshot_json TEXT NOT NULL DEFAULT '{}'",
            },
            "fj_review_items": {
                "context_revision_id": "ALTER TABLE fj_review_items ADD COLUMN context_revision_id TEXT",
            },
            "fj_chat_sessions": {
                "candidate_profile_id": "ALTER TABLE fj_chat_sessions ADD COLUMN candidate_profile_id TEXT",
                "resume_version_id": "ALTER TABLE fj_chat_sessions ADD COLUMN resume_version_id TEXT",
                "context_revision_id": "ALTER TABLE fj_chat_sessions ADD COLUMN context_revision_id TEXT",
                "evaluation_id": "ALTER TABLE fj_chat_sessions ADD COLUMN evaluation_id TEXT",
                "peer_title": "ALTER TABLE fj_chat_sessions ADD COLUMN peer_title TEXT NOT NULL DEFAULT ''",
                "platform_latest_msg_id": "ALTER TABLE fj_chat_sessions ADD COLUMN platform_latest_msg_id TEXT NOT NULL DEFAULT ''",
                "platform_latest_message_status": "ALTER TABLE fj_chat_sessions ADD COLUMN platform_latest_message_status INTEGER",
                "platform_relation_type": "ALTER TABLE fj_chat_sessions ADD COLUMN platform_relation_type INTEGER",
                "platform_chat_status": "ALTER TABLE fj_chat_sessions ADD COLUMN platform_chat_status INTEGER",
                "platform_latest_message_text": "ALTER TABLE fj_chat_sessions ADD COLUMN platform_latest_message_text TEXT NOT NULL DEFAULT ''",
                "platform_latest_message_at": "ALTER TABLE fj_chat_sessions ADD COLUMN platform_latest_message_at TEXT",
                "platform_latest_from_id": "ALTER TABLE fj_chat_sessions ADD COLUMN platform_latest_from_id TEXT NOT NULL DEFAULT ''",
                "platform_latest_to_id": "ALTER TABLE fj_chat_sessions ADD COLUMN platform_latest_to_id TEXT NOT NULL DEFAULT ''",
                "platform_synced_at": "ALTER TABLE fj_chat_sessions ADD COLUMN platform_synced_at TEXT",
                "platform_list_index": "ALTER TABLE fj_chat_sessions ADD COLUMN platform_list_index INTEGER NOT NULL DEFAULT 0",
                "message_update_required": "ALTER TABLE fj_chat_sessions ADD COLUMN message_update_required INTEGER NOT NULL DEFAULT 0",
                "history_has_more": "ALTER TABLE fj_chat_sessions ADD COLUMN history_has_more INTEGER NOT NULL DEFAULT 0",
                "history_next_cursor": "ALTER TABLE fj_chat_sessions ADD COLUMN history_next_cursor TEXT NOT NULL DEFAULT ''",
            },
            "fj_chat_reply_tasks": {
                "resume_version_id": "ALTER TABLE fj_chat_reply_tasks ADD COLUMN resume_version_id TEXT",
                "context_revision_id": "ALTER TABLE fj_chat_reply_tasks ADD COLUMN context_revision_id TEXT",
            },
        }
        for table, table_migrations in migrations.items():
            columns = {
                row["name"] for row in connection.execute(f"PRAGMA table_info({table})")
            }
            for column, ddl in table_migrations.items():
                if column not in columns:
                    connection.execute(ddl)

        # 现有基础/派生角色与真实生成来源转换为独立字段。
        connection.execute(
            """
            UPDATE fj_resume_versions
            SET current_role = CASE
                  WHEN id IN (
                    SELECT base_version_id FROM fj_resume_families
                    WHERE base_version_id IS NOT NULL
                  ) THEN 'base'
                  ELSE 'derived'
                END,
                origin_type = CASE
                  WHEN version_type = 'base' THEN 'upload_base'
                  WHEN source_id IS NOT NULL THEN 'upload_derived'
                  WHEN version_type = 'jd_tailored' THEN 'ai_derived'
                  ELSE 'manual_copy'
                END,
                derived_from_version_id = COALESCE(derived_from_version_id, parent_version_id)
            """
        )
        connection.execute(
            """
            UPDATE fj_profile_sources
            SET resume_version_id = (
              SELECT v.id FROM fj_resume_versions v
              WHERE v.source_id = fj_profile_sources.id
              ORDER BY CASE v.current_role WHEN 'base' THEN 0 ELSE 1 END,
                       v.created_at, v.id
              LIMIT 1
            )
            WHERE resume_version_id IS NULL
            """
        )
        connection.execute(
            """
            UPDATE fj_resume_analysis_runs
            SET resume_version_id = (
              SELECT base_version_id FROM fj_resume_families f
              WHERE f.id = fj_resume_analysis_runs.resume_family_id
            )
            WHERE resume_version_id IS NULL
            """
        )
        # 具体简历已经唯一归属候选人档案，补齐旧策略缺失的档案关联。
        for strategy_table in (
            "fj_job_filter_strategies",
            "fj_job_recommendation_strategies",
        ):
            connection.execute(
                f"""
                UPDATE {strategy_table}
                SET candidate_profile_id = (
                  SELECT v.profile_id FROM fj_resume_versions v
                  WHERE v.id = {strategy_table}.resume_version_id
                )
                WHERE resume_version_id IS NOT NULL
                  AND COALESCE(candidate_profile_id, '') = ''
                """
            )

        # 旧通用作用域迁移为用户可见的“适用全部简历”。
        connection.execute(
            "UPDATE fj_profile_facts SET applies_to_all_resumes = 1 WHERE scope_type = 'general'"
        )
        connection.execute(
            "UPDATE fj_profile_questions SET applies_to_all_resumes = 1 WHERE scope_type = 'general' AND origin <> 'default'"
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO fj_fact_resume_links (
              fact_id, resume_version_id, linked_by, created_at
            )
            SELECT f.id,
                   COALESCE(
                     (
                       SELECT v.id
                       FROM fj_profile_fact_evidence e
                       JOIN fj_resume_versions v ON v.source_id = e.source_id
                       WHERE e.fact_id = f.id
                       ORDER BY v.created_at, v.id LIMIT 1
                     ),
                     (
                       SELECT rf.base_version_id FROM fj_resume_families rf
                       WHERE rf.id = f.scope_id
                     )
                   ),
                   'migration', f.created_at
            FROM fj_profile_facts f
            WHERE f.scope_type = 'resume_family'
              AND COALESCE(
                    (
                      SELECT v.id
                      FROM fj_profile_fact_evidence e
                      JOIN fj_resume_versions v ON v.source_id = e.source_id
                      WHERE e.fact_id = f.id
                      ORDER BY v.created_at, v.id LIMIT 1
                    ),
                    (
                      SELECT rf.base_version_id FROM fj_resume_families rf
                      WHERE rf.id = f.scope_id
                    )
                  ) IS NOT NULL
            """
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO fj_question_resume_links (
              question_id, resume_version_id, linked_by, created_at
            )
            SELECT q.id,
                   COALESCE(
                     (
                       SELECT v.id FROM fj_resume_versions v
                       WHERE v.source_id = q.source_id
                       ORDER BY v.created_at, v.id LIMIT 1
                     ),
                     (
                       SELECT rf.base_version_id FROM fj_resume_families rf
                       WHERE rf.id = q.scope_id
                     )
                   ),
                   'migration', q.created_at
            FROM fj_profile_questions q
            WHERE q.scope_type = 'resume_family'
              AND COALESCE(
                    (
                      SELECT v.id FROM fj_resume_versions v
                      WHERE v.source_id = q.source_id
                      ORDER BY v.created_at, v.id LIMIT 1
                    ),
                    (
                      SELECT rf.base_version_id FROM fj_resume_families rf
                      WHERE rf.id = q.scope_id
                    )
                  ) IS NOT NULL
            """
        )

        # 默认问题成为提取模板，避免在正式 QA 中产生通用空记录。
        connection.execute(
            """
            INSERT OR IGNORE INTO fj_profile_qa_templates (
              id, profile_id, question_key, question_text, reason, answer_type,
              required_stage, priority, writes_to_field, enabled, sort_order,
              source_type, created_at, updated_at
            )
            SELECT 'template_' || q.profile_id || '_' || q.question_key,
                   q.profile_id, q.question_key, q.question_text, q.reason,
                   q.answer_type, q.required_stage, q.priority, q.writes_to_field,
                   q.enabled, ROW_NUMBER() OVER (
                     PARTITION BY q.profile_id ORDER BY q.created_at, q.question_key
                   ), 'system', q.created_at, q.updated_at
            FROM fj_profile_questions q
            WHERE q.origin = 'default'
            """
        )
        connection.execute(
            """
            DELETE FROM fj_profile_questions
            WHERE origin = 'default' AND status = 'pending'
              AND final_answer_json IS NULL AND proposed_answer_json IS NULL
            """
        )
        self._merge_resume_analysis_v3_duplicates(connection)
        connection.execute(
            """
            INSERT OR IGNORE INTO fj_profile_qa_revisions (
              id, question_id, revision, answer_json, source_type,
              status, created_at
            )
            SELECT 'qa_revision_' || q.id, q.id, 1, q.final_answer_json,
                   'migration', 'current', q.updated_at
            FROM fj_profile_questions q
            WHERE q.final_answer_json IS NOT NULL
            """
        )

    def _merge_resume_analysis_v3_duplicates(
        self,
        connection: sqlite3.Connection,
    ) -> None:
        """合并旧通用/简历组作用域中内容完全相同的事实和 QA。"""
        fact_groups = connection.execute(
            """
            SELECT GROUP_CONCAT(id) AS ids
            FROM fj_profile_facts
            GROUP BY profile_id, domain, entity_type, entity_id, field_key, value_json
            HAVING COUNT(*) > 1
            """
        ).fetchall()
        for group in fact_groups:
            fact_ids = [item for item in str(group["ids"] or "").split(",") if item]
            if len(fact_ids) < 2:
                continue
            placeholders = ",".join("?" for _ in fact_ids)
            rows = connection.execute(
                f"""
                SELECT id, applies_to_all_resumes FROM fj_profile_facts
                WHERE id IN ({placeholders})
                ORDER BY CASE status WHEN 'confirmed' THEN 0 ELSE 1 END,
                         applies_to_all_resumes DESC, created_at, id
                """,
                fact_ids,
            ).fetchall()
            canonical_id = str(rows[0]["id"])
            applies_to_all = any(bool(row["applies_to_all_resumes"]) for row in rows)
            for row in rows[1:]:
                duplicate_id = str(row["id"])
                connection.execute(
                    """
                    INSERT OR IGNORE INTO fj_fact_resume_links (
                      fact_id, resume_version_id, linked_by, created_at
                    )
                    SELECT ?, resume_version_id, linked_by, created_at
                    FROM fj_fact_resume_links WHERE fact_id = ?
                    """,
                    (canonical_id, duplicate_id),
                )
                connection.execute(
                    "UPDATE fj_profile_fact_evidence SET fact_id = ? WHERE fact_id = ?",
                    (canonical_id, duplicate_id),
                )
                connection.execute(
                    "DELETE FROM fj_profile_facts WHERE id = ?", (duplicate_id,)
                )
            connection.execute(
                "UPDATE fj_profile_facts SET applies_to_all_resumes = ? WHERE id = ?",
                (1 if applies_to_all else 0, canonical_id),
            )

        question_groups = connection.execute(
            """
            SELECT GROUP_CONCAT(id) AS ids
            FROM fj_profile_questions
            WHERE final_answer_json IS NOT NULL
            GROUP BY profile_id, question_key, final_answer_json
            HAVING COUNT(*) > 1
            """
        ).fetchall()
        for group in question_groups:
            question_ids = [item for item in str(group["ids"] or "").split(",") if item]
            if len(question_ids) < 2:
                continue
            placeholders = ",".join("?" for _ in question_ids)
            rows = connection.execute(
                f"""
                SELECT id, applies_to_all_resumes FROM fj_profile_questions
                WHERE id IN ({placeholders})
                ORDER BY CASE status WHEN 'confirmed' THEN 0 ELSE 1 END,
                         applies_to_all_resumes DESC, created_at, id
                """,
                question_ids,
            ).fetchall()
            canonical_id = str(rows[0]["id"])
            applies_to_all = any(bool(row["applies_to_all_resumes"]) for row in rows)
            for row in rows[1:]:
                duplicate_id = str(row["id"])
                connection.execute(
                    """
                    INSERT OR IGNORE INTO fj_question_resume_links (
                      question_id, resume_version_id, linked_by, created_at
                    )
                    SELECT ?, resume_version_id, linked_by, created_at
                    FROM fj_question_resume_links WHERE question_id = ?
                    """,
                    (canonical_id, duplicate_id),
                )
                connection.execute(
                    "UPDATE fj_profile_question_evidence SET question_id = ? WHERE question_id = ?",
                    (canonical_id, duplicate_id),
                )
                connection.execute(
                    "UPDATE fj_profile_answer_variants SET question_id = ? WHERE question_id = ?",
                    (canonical_id, duplicate_id),
                )
                connection.execute(
                    "DELETE FROM fj_profile_qa_revisions WHERE question_id = ?",
                    (duplicate_id,),
                )
                connection.execute(
                    "DELETE FROM fj_profile_questions WHERE id = ?", (duplicate_id,)
                )
            connection.execute(
                "UPDATE fj_profile_questions SET applies_to_all_resumes = ? WHERE id = ?",
                (1 if applies_to_all else 0, canonical_id),
            )

        # 现有筛选策略的关键词数组转换为稳定、有序的词组记录。
        connection.execute(
            """
            INSERT OR IGNORE INTO fj_filter_strategy_search_keywords (
              id, filter_strategy_id, keyword, reason, enabled, sort_order,
              source_type, created_at, updated_at
            )
            SELECT 'keyword_' || s.id || '_' || j.key,
                   s.id, CAST(j.value AS TEXT), '', 1, CAST(j.key AS INTEGER),
                   'migration', s.created_at, s.updated_at
            FROM fj_job_filter_strategies s, json_each(s.search_keywords_json) j
            WHERE TRIM(CAST(j.value AS TEXT)) <> ''
            """
        )

        # 旧 V2 待处理记录保留到新的回答闭环中。
        connection.execute(
            """
            INSERT OR IGNORE INTO fj_profile_issues_v3 (
              id, profile_id, resume_version_id, source_id, operation_run_id,
              issue_type, title, description, source_excerpt, payload_json,
              status, created_at, updated_at, resolved_at
            )
            SELECT 'v3_' || i.id, i.profile_id, f.base_version_id, i.source_id,
                   i.operation_run_id,
                   CASE i.issue_type
                     WHEN 'uncertain_fact' THEN 'uncertain_fact'
                     WHEN 'conflict' THEN 'fact_conflict'
                     WHEN 'suggested_question' THEN 'missing_qa'
                     ELSE 'missing_information'
                   END,
                   i.title, i.description, i.source_excerpt, i.payload_json,
                   CASE i.status WHEN 'resolved' THEN 'resolved'
                                 WHEN 'dismissed' THEN 'dismissed'
                                 ELSE 'pending' END,
                   i.created_at, i.updated_at, i.resolved_at
            FROM fj_resume_analysis_issues i
            LEFT JOIN fj_resume_families f ON f.id = i.resume_family_id
            """
        )
