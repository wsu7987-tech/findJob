export type ProfileExternalUse = "prohibited" | "summary_only" | "allowed";

export interface ProfileVersions {
  sources_version: number;
  facts_version: number;
  questions_version: number;
  answers_version: number;
  strategy_version: number;
  context_version: number;
}

export interface CandidateProfile {
  id: string;
  display_name: string;
  status: "draft" | "ready" | "stale" | "archived";
  versions: ProfileVersions;
  created_at: string;
  updated_at: string;
}

export interface ProfileSource {
  id: string;
  profile_id: string;
  resume_family_id: string | null;
  resume_version_id: string | null;
  source_type: "pdf" | "markdown" | "text" | "project";
  title: string;
  file_path: string | null;
  raw_text: string;
  recognized_text: string;
  editable_text: string;
  normalized_markdown: string;
  recognizer_name: string | null;
  status: string;
  active_analysis_run_id: string | null;
  enabled: boolean;
  source_version: number;
  created_at: string;
  updated_at: string;
}

export interface ProfileFact {
  id: string;
  profile_id: string;
  domain: string;
  entity_type: string;
  entity_id: string;
  field_key: string;
  value: unknown;
  source_type: "document" | "user_answer" | "manual" | "ai_inference";
  sort_order: number;
  valid_from: string | null;
  valid_to: string | null;
  date_precision: "year" | "month" | "day" | "unknown";
  is_current: boolean;
  confidence: number;
  status: "proposed" | "confirmed" | "rejected" | "conflicted" | "stale";
  conflict_group_id: string | null;
  sensitivity: "normal" | "private" | "sensitive";
  external_use: ProfileExternalUse;
  scope_type: "general" | "resume_family";
  scope_id: string | null;
  confirmed_by: "ai_extraction" | "user" | null;
  applies_to_all_resumes: boolean;
  resume_version_ids: string[];
  disclosure_policy: Record<string, unknown>;
  valid_until: string | null;
  created_at: string;
  updated_at: string;
}

export interface ProfileQuestion {
  id: string;
  profile_id: string;
  question_key: string;
  question_text: string;
  reason: string;
  origin: "default" | "resume_analysis" | "jd_analysis" | "user";
  answer_type: string;
  required_stage: string;
  priority: "high" | "medium" | "low";
  proposed_answer: unknown;
  final_answer: unknown;
  status: string;
  external_use: ProfileExternalUse;
  scope_type: "general" | "resume_family";
  scope_id: string | null;
  confirmed_by: "ai_extraction" | "user" | null;
  applies_to_all_resumes: boolean;
  resume_version_ids: string[];
  valid_until: string | null;
  source_id: string | null;
  job_id: string | null;
  writes_to_field: string | null;
  enabled: boolean;
  created_at: string;
  updated_at: string;
}

export interface ProfileAnswerVariant {
  id: string;
  question_id: string;
  name: string;
  scope_type: "general" | "role_family" | "job";
  scope_id: string | null;
  answer_text: string;
  internal_note: string;
  usage_condition: string;
  status: "draft" | "confirmed" | "rejected" | "stale";
  generated_by: "system" | "ai" | "user";
  based_on_job_version: number | null;
  external_use: ProfileExternalUse;
  disclosure_policy: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface ProfileQARevision {
  id: string;
  question_id: string;
  revision: number;
  answer: unknown;
  source_type: "user" | "ai_extraction" | "restored" | "migration";
  status: "current" | "history";
  created_at: string;
}

export interface ProfileAnalysisRun {
  id: string;
  profile_id: string;
  source_ids: string[];
  input_versions: ProfileVersions;
  ai_model: string | null;
  prompt_version: string;
  status: string;
  quality: Record<string, unknown>;
  error_category: string | null;
  error_message: string | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface ProfileAnalysisItem {
  id: string;
  analysis_run_id: string;
  item_type: "fact" | "question" | "answer_variant" | "strategy" | "search_query" | "resume_version_suggestion";
  source_refs: Array<Record<string, unknown>>;
  payload: Record<string, unknown>;
  status: string;
  result_resource_type: string | null;
  result_resource_id: string | null;
  decision_note: string | null;
  decided_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface ProfileResumeVersion {
  id: string;
  profile_id: string;
  resume_family_id: string | null;
  parent_version_id: string | null;
  version_type: "base" | "jd_tailored" | "manual_variant" | "language_variant";
  target_job_id: string | null;
  derived_reason: string;
  based_on_content_version: number;
  name: string;
  role_family: string;
  campaign_id: string | null;
  source_id: string | null;
  content: string;
  fact_ids: string[];
  is_default: boolean;
  current_role: "base" | "derived";
  origin_type: "upload_base" | "upload_derived" | "ai_derived" | "manual_copy";
  derived_from_version_id: string | null;
  target_job_snapshot: Record<string, unknown>;
  status: string;
  content_version: number;
  confirmed_at: string | null;
  created_at: string;
  updated_at: string;
  deleted_at: string | null;
}

export interface ProfileSearchQuery {
  id: string;
  campaign_id: string;
  name: string;
  role_family: string;
  platform: string;
  keyword: string;
  cities: string[];
  work_modes: string[];
  positive_terms: string[];
  excluded_terms: string[];
  priority: number;
  reason: string;
  enabled: boolean;
  created_at: string;
  updated_at: string;
}

export interface ProfileCampaign {
  id: string;
  profile_id: string;
  name: string;
  target_titles: string[];
  role_families: string[];
  cities: string[];
  districts: string[];
  work_modes: string[];
  salary: Record<string, unknown>;
  industries: string[];
  company_scales: string[];
  resume_version_id: string | null;
  filter_strategy_id: string | null;
  recommendation_strategy_id: string | null;
  delivery_strategy_id: string | null;
  excluded_terms: string[];
  status: string;
  campaign_version: number;
  confirmed_at: string | null;
  queries: ProfileSearchQuery[];
  created_at: string;
  updated_at: string;
}

export interface ProfileContext {
  profile_id: string;
  resume_family_id: string | null;
  view: "full" | "search" | "evaluation" | "chat";
  versions: ProfileVersions;
  artifact_version: number;
  markdown: string;
  generated_at: string;
}

export type ResumeAnalysisOperationId =
  | "clean_content"
  | "extract_facts"
  | "extract_qa"
  | "generate_filter_strategy"
  | "generate_recommendation_strategy"
  | "generate_search_keywords";

export interface ResumeFamily {
  id: string;
  profile_id: string;
  name: string;
  root_source_id: string | null;
  target_role_family: string;
  base_version_id: string | null;
  default_version_id: string | null;
  default_delivery_version_id: string | null;
  content_version: number;
  analysis_version: number;
  status: "active" | "stale" | "archived";
  created_at: string;
  updated_at: string;
}

export interface ResumeAnalysisOperation {
  id: string;
  run_id: string;
  operation_id: ResumeAnalysisOperationId;
  sequence_no: number;
  status: "queued" | "running" | "succeeded" | "failed" | "blocked" | "cancelled" | "stale";
  output_summary: Record<string, unknown>;
  error_category: string | null;
  error_message: string | null;
  started_at: string | null;
  completed_at: string | null;
}

export interface ResumeAnalysisRun {
  id: string;
  profile_id: string;
  resume_family_id: string;
  resume_version_id: string;
  source_ids: string[];
  operation_ids: ResumeAnalysisOperationId[];
  pipeline_mode: "single" | "chained";
  execution_path: "structured" | "codex_workspace";
  status: "queued" | "running" | "completed" | "partial_failed" | "failed" | "cancelled";
  error_category: string | null;
  error_message: string | null;
  operations: ResumeAnalysisOperation[];
  created_at: string;
  updated_at: string;
}

export interface ResumeAnalysisIssue {
  id: string;
  resume_family_id: string;
  issue_type: "uncertain_fact" | "conflict" | "missing_information" | "suggested_question";
  title: string;
  description: string;
  source_excerpt: string;
  status: "pending" | "resolved" | "dismissed";
  created_at: string;
  updated_at: string;
}

export interface ResumeStrategy {
  id: string;
  resume_family_id: string;
  strategy_type: "filter" | "recommendation";
  name: string;
  content: Record<string, unknown>;
  version: number;
  status: "current" | "stale" | "archived";
  generated_by: "ai" | "user";
  created_at: string;
  updated_at: string;
}

export interface ResumeSearchKeyword {
  id: string;
  resume_family_id: string;
  keyword: string;
  sort_order: number;
  reason: string;
  enabled: boolean;
  version: number;
  status: "current" | "stale" | "archived";
}

export interface ProfileIssueAnswer {
  id: string;
  issue_id: string;
  answer_text: string;
  created_at: string;
}

export interface ProfileIssueChangeSet {
  id: string;
  issue_id: string;
  answer_id: string;
  changes: Record<string, unknown>;
  status: "draft" | "applied" | "discarded";
  created_at: string;
  updated_at: string;
  applied_at: string | null;
}

export interface ProfileIssue {
  id: string;
  profile_id: string;
  resume_version_id: string | null;
  source_id: string | null;
  operation_run_id: string | null;
  issue_type: "uncertain_fact" | "fact_conflict" | "missing_information" | "missing_qa" | "qa_conflict" | "orphaned_profile_data" | "analysis_choice";
  title: string;
  description: string;
  source_excerpt: string;
  payload: Record<string, unknown>;
  status: "pending" | "organizing" | "awaiting_confirmation" | "resolved" | "dismissed";
  answers: ProfileIssueAnswer[];
  change_sets: ProfileIssueChangeSet[];
  created_at: string;
  updated_at: string;
  resolved_at: string | null;
}

export interface ProfileQATemplate {
  id: string;
  profile_id: string;
  question_key: string;
  question_text: string;
  reason: string;
  answer_type: string;
  required_stage: string;
  priority: "high" | "medium" | "low";
  writes_to_field: string | null;
  enabled: boolean;
  sort_order: number;
  source_type: "system" | "user";
  created_at: string;
  updated_at: string;
}

export type ProfileContextView = "full" | "search" | "evaluation" | "chat";

export interface ProfileContextRevision {
  id: string;
  revision: number;
  content: string;
  source_type: "generated" | "user_edit" | "restored" | "migration";
  status: "draft" | "current" | "history";
  dependency_versions: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface ProfileContextHead {
  id: string;
  profile_id: string;
  resume_version_id: string;
  view: ProfileContextView;
  stale: boolean;
  dependency_versions: Record<string, unknown>;
  current_revision: ProfileContextRevision | null;
  draft_revision: ProfileContextRevision | null;
  history: ProfileContextRevision[];
  created_at: string;
  updated_at: string;
}

export interface ResumeDeleteImpact {
  resume_version_id: string;
  resume_family_id: string | null;
  is_base: boolean;
  source_id: string | null;
  derived_versions: Array<Record<string, unknown>>;
  exclusive_fact_ids: string[];
  exclusive_question_ids: string[];
  shared_fact_ids: string[];
  shared_question_ids: string[];
}

export interface AIDerivedResumePreview {
  source_resume_version_id: string;
  suggested_name: string;
  content: string;
  derived_reason: string;
  target_job_id?: string | null;
  target_job_snapshot: Record<string, unknown>;
}
