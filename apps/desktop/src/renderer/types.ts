export type PoolStatus = "pending" | "running" | "succeeded" | "failed";
export type RunTaskType = "summary" | "report";
export type RunStatus = "pending" | "running" | "completed" | "failed" | "cancelled";
export type FeedbackValue = "useful" | "useless";

export type FineJobCodexSessionStatus = "idle" | "starting" | "running" | "exited" | "failed";

export interface FineJobCodexPermissions {
  enabled: boolean;
  permissions: Record<string, boolean>;
  supported: Record<string, boolean>;
}

export interface FineJobCodexPendingItem {
  id: string;
  version?: number;
  text_version?: number;
  draft_message?: string;
  final_message?: string;
  final_text?: string;
  status: string;
  created_at: string;
}

export interface FineJobCodexPendingWork {
  greetings: FineJobCodexPendingItem[];
  chat_replies: FineJobCodexPendingItem[];
  automation_actions: Array<Record<string, unknown>>;
  chat_actions: Array<Record<string, unknown>>;
}

export interface ApiPoolItem {
  id: string;
  knowledge_item_id?: string;
  result_snapshot_id?: string | null;
  source_name?: string | null;
  title?: string | null;
  source_type: string;
  source_value: string;
  cleaning_level?: "basic" | "enhanced" | null;
  current_status: string;
  display_updated_at: string;
  is_deleted?: boolean;
  was_resummarized?: boolean;
  last_failed_category?: string | null;
}

export interface PoolListResponse {
  items: ApiPoolItem[];
  total: number;
}

export interface PoolCreateRequest {
  source_type: "url" | "pdf" | "markdown" | "text";
  source_value: string;
  title?: string | null;
  raw_text?: string | null;
  capture_source?: "manual" | "screenshot_ocr" | null;
  captured_at?: string | null;
  category?: string | null;
  tags?: string[];
}

export interface PoolMetadataSuggestionRequest {
  source_type: "url" | "pdf" | "markdown" | "text";
  source_value: string;
  title?: string | null;
  raw_text?: string | null;
}

export interface PoolMetadataSuggestionResponse {
  category: string;
  tags: string[];
  strategy: string;
}

export interface PoolCommitMetadataRequest {
  category?: string | null;
  tags?: string[];
  cleaned_text?: string | null;
  cleaning_level?: "basic" | "enhanced" | null;
}

export interface PoolCreateResponse {
  item: ApiPoolItem;
}

export type PdfDraftParserName = "auto" | "pymupdf4llm_markdown" | "rapid_ocr";

export interface FineJobResume {
  id: string;
  name: string;
  file_path: string;
  file_hash?: string;
  parser_name: string;
  raw_text?: string;
  markdown_text?: string | null;
  preview_text: string;
  page_count: number;
  char_count: number;
  quality_score: number;
  is_ocr: boolean;
  warnings: string[];
  fallback_from?: string | null;
  fallback_reason?: string | null;
  status: "parsed" | "failed";
  created_at: string;
  updated_at: string;
}

export interface FineJobResumeListEnvelope {
  resumes: FineJobResume[];
}

export interface FineJobResumeEnvelope {
  resume: FineJobResume;
}

export interface FineJobResumeCreateFromFileRequest {
  file_path: string;
  name?: string | null;
  parser_name?: PdfDraftParserName;
}

export interface FineJobResumeFact {
  id?: string | null;
  resume_id?: string;
  fact_type: "basic" | "contact" | "education" | "experience" | "project" | "skill" | string;
  fact_key: string;
  fact_value: string;
  confidence: number;
  source_text?: string | null;
  user_confirmed: boolean;
  sensitive: boolean;
  created_at?: string;
  updated_at?: string;
}

export interface FineJobResumeFactListEnvelope {
  facts: FineJobResumeFact[];
}

export interface FineJobResumeFactsSaveRequest {
  facts: FineJobResumeFact[];
}

export type FineJobWorkMode = "any" | "onsite" | "hybrid" | "remote";

export interface FineJobIntent {
  id?: string;
  target_title: string;
  cities: string[];
  keywords: string[];
  expanded_keywords: string[];
  excluded_keywords: string[];
  salary_min?: number | null;
  salary_max?: number | null;
  work_mode: FineJobWorkMode;
  notes: string;
  ready?: boolean;
  created_at?: string;
  updated_at?: string;
}

export interface FineJobIntentEnvelope {
  intent: FineJobIntent | null;
}

export type FineJobPlatformName = "boss";
export type FineJobPlatformSessionStatus = "ready" | "needs_login" | "invalid";

export interface FineJobPlatformSession {
  platform: FineJobPlatformName;
  display_name: string;
  login_url: string;
  browser_profile: string;
  browser_channel: "chrome" | "msedge";
  status: FineJobPlatformSessionStatus;
  status_detail: string;
  ready?: boolean;
  last_checked_at?: string | null;
  created_at?: string;
  updated_at?: string;
}

export interface FineJobPlatformSessionEnvelope {
  session: FineJobPlatformSession | null;
}

export interface FineJobPlatformSessionListEnvelope {
  sessions: FineJobPlatformSession[];
}

export interface FineJobPlatformLoginActionEnvelope {
  session: FineJobPlatformSession;
  detail: string;
}

export interface FineJobBossBrowserStatus {
  running: boolean;
  cdp_port: number;
  current_url?: string | null;
  current_title?: string | null;
  is_search_page: boolean;
}

export interface FineJobBossNetworkDebugStatus {
  active: boolean;
  event_count: number;
  request_count: number;
  output_path?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  target_count: number;
  targets: Array<{
    target_id: string;
    url: string;
    title: string;
  }>;
  error_message?: string | null;
}

export interface FineJobBossCity {
  name: string;
  code: string;
}

export interface FineJobBossCityListResponse {
  cities: FineJobBossCity[];
}

export interface FineJobBossSearchPageRequest {
  keyword: string;
  city: string;
  filters?: Record<string, string>;
}

export interface FineJobBossSearchPageResponse {
  url: string;
  status: FineJobBossBrowserStatus;
}

export interface FineJobBossCaptureRequest extends FineJobBossSearchPageRequest {
  pages: number;
  include_details: boolean;
  prefer_current_page: boolean;
  filter_strategy_id?: string | null;
}

export interface FineJobBossCapturedJob {
  job_id?: string;
  encrypt_job_id?: string;
  title?: string;
  boss_name?: string;
  salary?: string;
  location?: string;
  experience?: string;
  degree?: string;
  company_scale?: string;
  company_industry?: string;
  company_stage?: string;
  welfare?: string;
  boss_active_status?: string;
  job_link?: string;
  tags?: string;
  skills?: string;
  job_labels?: string;
  detail_status?: "not_collected" | "queued" | "collecting" | "completed" | "failed";
  detail_version?: number;
  detail?: Record<string, unknown> | null;
  detail_error?: string | null;
  recommended?: boolean;
  recommendation_source?: "strategy" | "ai" | "rules" | "llm" | null;
  recommendation_reason?: string | null;
  filter_status?: "pass" | "pass_for_human" | "reject" | "review" | "exclude" | null;
  strategy_filter_status?: "pass" | "pass_for_human" | "reject" | "review" | "exclude" | null;
  final_filter_status?: "pass" | "pass_for_human" | "reject" | "review" | "exclude" | null;
  processing_state?: "new" | "reprocessable" | "duplicate" | "excluded" | null;
  filter_reasons?: string[];
  filter_missing_fields?: string[];
  filter_strategy_id?: string | null;
  company_id?: string | null;
  company_type?: FineJobCompanyType;
  is_outsourcing_company?: boolean;
  is_blacklisted?: boolean;
  application_status?: "pending_greeting" | "pending_application" | "communicating" | "rejected" | null;
  applied_at?: string | null;
  cooldown_excluded?: boolean;
  cooldown_reasons?: string[];
  delivery_evaluation?: FineJobBossDeliveryEvaluation | null;
  list_collected_at?: string | null;
  detail_collected_at?: string | null;
  is_previously_collected?: boolean;
  first_collected_at?: string | null;
  last_collected_at?: string | null;
  collect_count?: number;
  [key: string]: unknown;
}

export type FineJobBossCaptureTaskStatus = "queued" | "running" | "completed" | "failed";

export interface FineJobBossCaptureTask {
  id: string;
  status: FineJobBossCaptureTaskStatus;
  stage: string;
  message: string;
  keyword: string;
  city: string;
  pages: number;
  auto_details: boolean;
  used_current_page: boolean;
  source_url?: string | null;
  progress_current: number;
  progress_total: number;
  jobs_collected: number;
  details_completed: number;
  details_failed: number;
  duplicate_jobs_count: number;
  continuation_available?: boolean;
  has_more?: boolean;
  last_added_jobs?: number;
  total_pages_loaded?: number;
  stop_requested?: boolean;
  current_job?: Record<string, unknown> | null;
  estimated_seconds_min: number;
  estimated_seconds_max: number;
  jobs: FineJobBossCapturedJob[];
  jobs_path?: string | null;
  details_path?: string | null;
  created_at: string;
  updated_at: string;
  finished_at?: string | null;
  error_message?: string | null;
}

export interface FineJobBossDetailSuggestionResponse {
  selected_job_ids: string[];
  task: FineJobBossCaptureTask;
}

export interface FineJobBossFilterResult {
  job_id: string;
  status: "pass" | "reject" | "review" | "exclude";
  reasons: string[];
  missing_fields: string[];
  strategy_id?: string | null;
  cooldown_excluded?: boolean;
  cooldown_reasons?: string[];
}

export interface FineJobBossFilterApplicationResponse {
  selected_job_ids: string[];
  results: FineJobBossFilterResult[];
  task: FineJobBossCaptureTask;
}

export interface FineJobBossDeliveryEvaluation {
  evaluation_version: "2.0";
  job_id: string;
  decision: "recommend" | "review" | "reject";
  confidence: number;
  summary: string;
  reasons: string[];
  risks: string[];
  missing_fields: string[];
  missing_information: string[];
  hard_requirements: Array<{
    name: string;
    status: "pass" | "fail" | "unknown";
    jd_evidence: string;
    resume_evidence: string;
  }>;
  match_dimensions: Record<string, number>;
  strengths: string[];
  gaps: Array<{
    item: string;
    severity: "high" | "medium" | "low";
    can_fix_by_resume: boolean;
  }>;
  resume_suggestions: Array<{
    section: string;
    suggestion: string;
    basis: string;
  }>;
  greeting_draft: {
    status: "ready" | "not_generated";
    text: string;
    facts_used: string[];
  };
  source: "rules" | "llm";
}

export interface FineJobBossDeliveryEvaluationResponse {
  evaluations: FineJobBossDeliveryEvaluation[];
  task: FineJobBossCaptureTask;
}

export interface FineJobBossHistoryDeliveryEvaluationResponse {
  evaluation: FineJobBossDeliveryEvaluation;
  job: FineJobBossHistoryJob;
}

export type FineJobBossHistorySortField =
  | "last_collected_at"
  | "first_collected_at"
  | "collect_count"
  | "title"
  | "company_name";

export interface FineJobBossHistoryQuery {
  query?: string;
  search_keyword?: string;
  city?: string;
  company_scale?: string;
  company_industry?: string;
  company_stage?: string;
  detail_status?: string;
  repeat_status?: "all" | "first_seen" | "repeated";
  collected_from?: string;
  collected_to?: string;
  sort_by?: FineJobBossHistorySortField;
  sort_order?: "asc" | "desc";
  page?: number;
  page_size?: number;
}

export interface FineJobBossHistoryJob extends FineJobBossCapturedJob {
  id: string;
  search_keyword: string;
  first_collected_at: string;
  last_collected_at: string;
  collect_count: number;
  latest_capture_id: string;
  pipeline_stage?: FineJobPipelineStage | null;
  waiting_on?: FineJobWaitingOn;
  contact_origin?: FineJobContactOrigin;
  attention_status?: string;
}

export interface FineJobBossHistoryResponse {
  items: FineJobBossHistoryJob[];
  total: number;
  page: number;
  page_size: number;
}

export type FineJobAutomationLevel = "assist" | "semi_auto" | "auto_greeting";
export type FineJobResumeSubmitMode = "manual" | "auto_on_invite";
export type FineJobContactShareMode = "manual" | "auto_after_match";
export type FineJobInterviewAcceptMode = "manual" | "auto_in_selected_slots";

export interface FineJobDeliveryStrategy {
  id?: string;
  automation_level: FineJobAutomationLevel;
  auto_greeting_enabled: boolean;
  force_contact_verification_enabled: boolean;
  daily_greeting_limit: number;
  hourly_greeting_limit: number;
  min_match_score: number;
  resume_submit_mode: FineJobResumeSubmitMode;
  contact_share_mode: FineJobContactShareMode;
  interview_accept_mode: FineJobInterviewAcceptMode;
  only_online_interview: boolean;
  pause_on_risk: boolean;
  notes: string;
  ready?: boolean;
  confirmed_at?: string | null;
  created_at?: string;
  updated_at?: string;
}

export interface FineJobDeliveryStrategyEnvelope {
  strategy: FineJobDeliveryStrategy | null;
}

export type FineJobUnknownValuePolicy = "keep" | "review" | "exclude";
export type FineJobJobType = "full_time" | "internship" | "part_time";
export type FineJobCooldownPeriod = "disabled" | "days_3" | "days_7" | "days_30" | "permanent";

export interface FineJobCooldownRule {
  period: FineJobCooldownPeriod;
  exclude_outsourcing: boolean;
}

export interface FineJobCooldownRules {
  applied_company: FineJobCooldownRule;
  detailed_and_evaluated_company: FineJobCooldownRule;
  applied_job: FineJobCooldownRule;
  detailed_and_evaluated_job: FineJobCooldownRule;
}

export interface FineJobFilterStrategy {
  id?: string;
  name: string;
  enabled: boolean;
  search_keywords: string[];
  cities: string[];
  title_include_any: string[];
  title_include_all: string[];
  title_exclude: string[];
  company_include: string[];
  company_exclude: string[];
  company_scales: string[];
  company_industries: string[];
  company_stages: string[];
  degrees: string[];
  experiences: string[];
  job_types: FineJobJobType[];
  monthly_salary_min?: number | null;
  monthly_salary_max_at_least?: number | null;
  daily_salary_min?: number | null;
  skill_include_any: string[];
  skill_include_all: string[];
  skill_exclude: string[];
  boss_active_statuses: string[];
  cooldown_rules: FineJobCooldownRules;
  unknown_value_policy: FineJobUnknownValuePolicy;
  notes: string;
  candidate_profile_id?: string | null;
  resume_version_id?: string | null;
  source_type?: "user" | "ai" | "migration";
  based_on_analysis_run_id?: string | null;
  based_on_resume_content_version?: number | null;
  based_on_facts_version?: number | null;
  based_on_qa_version?: number | null;
  strategy_version?: number;
  created_at?: string;
  updated_at?: string;
}

export interface FineJobFilterStrategyListEnvelope {
  strategies: FineJobFilterStrategy[];
}

export interface FineJobFilterStrategyEnvelope {
  strategy: FineJobFilterStrategy;
}

export interface FineJobFilterExclusionState {
  strategy_id: string;
  status: "ready" | "stale";
  strategy_version: number;
  last_full_refreshed_at?: string | null;
  updated_at?: string | null;
  company_count: number;
  job_count: number;
}

export type FineJobEvaluationMethod = "rules" | "llm" | "hybrid";

export interface FineJobRecommendationStrategy {
  id?: string;
  name: string;
  enabled: boolean;
  filter_strategy_id?: string | null;
  resume_id?: string | null;
  evaluation_method: FineJobEvaluationMethod;
  desired_responsibilities: string[];
  required_skills: string[];
  preferred_skills: string[];
  excluded_terms: string[];
  preferred_industries: string[];
  work_preferences: string;
  risk_notes: string;
  minimum_confidence: number;
  insufficient_info_action: "review" | "reject";
  notes: string;
  candidate_profile_id?: string | null;
  resume_version_id?: string | null;
  source_type?: "user" | "ai" | "migration";
  based_on_analysis_run_id?: string | null;
  based_on_resume_content_version?: number | null;
  based_on_facts_version?: number | null;
  based_on_qa_version?: number | null;
  strategy_version?: number;
  created_at?: string;
  updated_at?: string;
}

export interface FineJobRecommendationStrategyListEnvelope {
  strategies: FineJobRecommendationStrategy[];
}

export interface FineJobRecommendationStrategyEnvelope {
  strategy: FineJobRecommendationStrategy;
}

export type FineJobCompanyType = "unknown" | "direct" | "outsourcing";

export interface FineJobCompany {
  id: string;
  canonical_name: string;
  normalized_name: string;
  company_type: FineJobCompanyType;
  classification_source: "capture" | "manual" | "mcp" | "migration";
  notes: string;
  is_blacklisted: boolean;
  blacklist_reason: string;
  blacklisted_at?: string | null;
  version: number;
  aliases: Array<{ id: string; alias_name: string }>;
  job_count: number;
  applied_job_count: number;
  last_detail_at?: string | null;
  last_evaluated_at?: string | null;
  last_applied_at?: string | null;
  created_at: string;
  updated_at: string;
}

export interface FineJobCompanyEnvelope { company: FineJobCompany; }
export interface FineJobCompanyListEnvelope {
  items: FineJobCompany[];
  total: number;
  page: number;
  page_size: number;
}

export interface FineJobStrategySearchKeyword {
  id: string;
  filter_strategy_id: string;
  keyword: string;
  reason: string;
  enabled: boolean;
  sort_order: number;
  source_type: "user" | "ai" | "migration";
  created_at: string;
  updated_at: string;
}

export interface FineJobStrategySearchKeywordEnvelope {
  keyword: FineJobStrategySearchKeyword;
}

export interface FineJobStrategySearchKeywordListEnvelope {
  keywords: FineJobStrategySearchKeyword[];
}

export interface FineJobStrategyChangeSet {
  id: string;
  profile_id: string;
  resume_version_id: string;
  strategy_type: "filter" | "recommendation" | "search_keywords";
  target_strategy_id?: string | null;
  payload: Record<string, unknown>;
  status: "draft" | "applied" | "discarded";
  operation_run_id?: string | null;
  created_at: string;
  updated_at: string;
  applied_at?: string | null;
}

export interface FineJobStrategyChangeSetListEnvelope {
  change_sets: FineJobStrategyChangeSet[];
}

export interface FineJobStrategyChangeSetEnvelope {
  change_set: FineJobStrategyChangeSet;
}

export type FineJobDeliveryRunMode = "dry_run" | "live";
export type FineJobDeliveryRunStatus =
  | "pending"
  | "running"
  | "completed"
  | "failed"
  | "paused"
  | "cancelled";

export interface FineJobDeliveryRun {
  id: string;
  mode: FineJobDeliveryRunMode;
  status: FineJobDeliveryRunStatus;
  stage: string;
  searched_count: number;
  skipped_count: number;
  greeted_count: number;
  error_count: number;
  started_at: string;
  updated_at: string;
  finished_at?: string | null;
  error_message?: string | null;
}

export interface FineJobDeliveryRunListEnvelope {
  runs: FineJobDeliveryRun[];
}

export interface FineJobDeliveryRunEnvelope {
  run: FineJobDeliveryRun;
}

export interface FineJobDeliveryRunCreateRequest {
  mode: FineJobDeliveryRunMode;
  real_collect?: boolean;
}

export interface FineJobDeliveryCandidate {
  id: string;
  run_id: string;
  platform: string;
  keyword: string;
  city: string;
  job_url: string;
  job_title: string;
  company_name: string;
  salary_text: string;
  location_text: string;
  experience_text: string;
  education_text: string;
  hr_active_text: string;
  jd_text: string;
  match_score?: number | null;
  decision: string;
  reason: string;
  created_at: string;
  updated_at: string;
}

export interface FineJobDeliveryCandidateListEnvelope {
  candidates: FineJobDeliveryCandidate[];
}

export interface FineJobActionLog {
  id: string;
  run_id?: string | null;
  level: "info" | "warning" | "error" | string;
  action_type: string;
  message: string;
  detail: Record<string, unknown>;
  created_at: string;
  source?: "legacy_run" | "main_workflow";
  category?: string;
  outcome?: string;
  job_id?: string | null;
  job_title?: string | null;
  company_name?: string | null;
}

export interface FineJobActionLogListEnvelope {
  logs: FineJobActionLog[];
  total?: number;
  page?: number;
  page_size?: number;
  action_types?: string[];
}

export type FineJobReviewStatus = "pending" | "approved" | "rejected" | "dismissed";
export type FineJobReviewTab = FineJobReviewStatus | "running" | "executed";
export type FineJobAutomationActionStatus =
  | "queued"
  | "running"
  | "leased"
  | "succeeded"
  | "failed"
  | "blocked"
  | "unknown"
  | "cancelled";

export type FineJobBossExecutionState =
  | "queued"
  | "running"
  | "succeeded"
  | "cancelled"
  | "blocked"
  | "failed"
  | "unknown";

export interface FineJobReviewItem {
  id: string;
  job_id: string;
  evaluation_id: string;
  action_type: "start_conversation";
  status: FineJobReviewStatus;
  ai_decision: "recommend" | "review" | "reject";
  draft_message: string;
  final_message: string;
  resolution_note: string;
  auto_approved: boolean;
  job_title: string;
  company_name: string;
  job_link: string;
  company_id?: string | null;
  company_type?: FineJobCompanyType | null;
  evaluation: FineJobBossDeliveryEvaluation;
  created_at: string;
  updated_at: string;
  resolved_at?: string | null;
  action_id?: string | null;
  action_status?: FineJobAutomationActionStatus | null;
  execution_state?: FineJobBossExecutionState | null;
  action_last_error?: string | null;
  company_chat_session_id?: string | null;
  job_chat_session_id?: string | null;
}

export type FineJobPipelineStage =
  | "discovered" | "shortlisted" | "greeted" | "communicating"
  | "resume_requested" | "resume_submitted" | "resume_viewed" | "under_review"
  | "interview_scheduling" | "interviewing" | "offer" | "rejected" | "closed";
export type FineJobWaitingOn = "candidate" | "recruiter" | "none" | "unknown";
export type FineJobContactOrigin =
  | "finejob_auto" | "candidate_initiated" | "recruiter_initiated"
  | "external_candidate_initiated" | "unknown";

export interface FineJobPipelineSnapshot {
  job_id: string;
  company_id?: string | null;
  stage: FineJobPipelineStage;
  stage_source: string;
  stage_event_id: string;
  stage_updated_at: string;
  waiting_on: FineJobWaitingOn;
  waiting_since_at?: string | null;
  contact_origin: FineJobContactOrigin;
  rejection_reason_source: "recruiter_explicit" | "ai_inferred" | "unknown";
  rejection_reason_category: string;
  rejection_reason_summary: string;
  projection_version: number;
  created_at: string;
  updated_at: string;
}

export interface FineJobJobProgress {
  job_id: string;
  session_id?: string | null;
  stage: FineJobPipelineStage;
  stage_updated_at: string;
  waiting_on: FineJobWaitingOn;
  waiting_since_at?: string | null;
  contact_origin: FineJobContactOrigin;
  latest_activity?: FineJobActivityEvent | null;
  followup: {
    decision: "follow" | "wait" | "do_not_follow";
    reason_code: string;
    reason_summary: string;
    recommended_at?: string | null;
    recommended_action: string;
    draft_message: string;
    draft_task_id?: string | null;
  };
  outcome: {
    status: "ongoing" | "offer" | "rejected" | "closed";
    rejection_reason_source: "recruiter_explicit" | "ai_inferred" | "unknown";
    rejection_reason_category: string;
    rejection_reason_summary: string;
  };
  primary_action?: {
    type: "reply" | "followup" | "ask_rejection_reason";
    label: string;
  } | null;
  analysis_updated_at?: string | null;
}

export interface FineJobActivityEvent {
  id: string;
  job_id: string;
  company_id?: string | null;
  chat_session_id?: string | null;
  event_type: string;
  occurred_at: string;
  source: string;
  source_ref_type: string;
  source_ref_id: string;
  confidence: number;
  evidence_level: "direct" | "strong_inferred" | "weak_inferred";
  payload: Record<string, unknown>;
  created_at: string;
}

export interface FineJobExecutionEvidence {
  id: string;
  action_ref_type: string;
  action_ref_id: string;
  evidence_type: string;
  source: string;
  source_ref_type: string;
  source_ref_id: string;
  observed_at: string;
  confidence: number;
  evidence_level: "direct" | "strong_inferred" | "weak_inferred";
  payload: Record<string, unknown>;
  created_at: string;
}

export interface FineJobExecutionReconciliation {
  id: string;
  previous_status: string;
  new_status: string;
  reconciled_at: string;
  reconciliation_reason: string;
  evidence_id: string;
  evidence_level: string;
}

export interface FineJobExecutionSummary {
  action_ref_type: string;
  action_ref_id: string;
  action_type: string;
  dedupe_identity: string;
  session_id?: string | null;
  raw_status: string;
  canonical_status: string;
  canonical_reason: string;
  canonical_updated_at?: string | null;
  status_code: string;
  error_message: string;
  executor_id: string;
  leader_tab_id: string;
  execution_epoch: number;
  attempt_count: number;
  created_at: string;
  started_at?: string | null;
  dispatch_started_at?: string | null;
  completed_at?: string | null;
  evidence: FineJobExecutionEvidence[];
  reconciliations: FineJobExecutionReconciliation[];
}

export interface FineJobJobJourney {
  job_id: string;
  pipeline?: FineJobPipelineSnapshot | null;
  legacy_application?: {
    id: string;
    status?: string | null;
    source: string;
    applied_at: string;
    updated_at: string;
  } | null;
  progress?: FineJobJobProgress | null;
  activities: FineJobActivityEvent[];
  executions: FineJobExecutionSummary[];
}

export interface FineJobReviewItemListEnvelope {
  items: FineJobReviewItem[];
  total: number;
  page?: number;
  page_size?: number;
}

export interface FineJobReviewQuery {
  status?: FineJobReviewStatus;
  execution_view?: "running" | "executed" | "";
  decision?: FineJobReviewItem["ai_decision"] | "";
  query?: string;
  execution_state?: FineJobBossExecutionState | "";
  created_from?: string;
  created_to?: string;
  page?: number;
  page_size?: number;
}

export interface FineJobReviewBatchResponse {
  results: Array<{ review_item_id: string; success: boolean; error_message: string }>;
  succeeded: number;
  failed: number;
}

export interface FineJobAutomationAction {
  id: string;
  job_id: string;
  evaluation_id: string;
  review_item_id: string;
  action_type: "start_conversation" | "BOSS_DEFAULT_GREETING";
  status: FineJobAutomationActionStatus;
  idempotency_key: string;
  payload: Record<string, unknown>;
  last_error?: string | null;
  job_title: string;
  company_name: string;
  created_at: string;
  updated_at: string;
  completed_at?: string | null;
  execution_state: FineJobBossExecutionState;
  execution_epoch: number;
  last_status_code?: string | null;
  result: Record<string, unknown>;
}

export interface FineJobAutomationActionEnvelope {
  action: FineJobAutomationAction;
}

export interface FineJobAutomationActionListEnvelope {
  actions: FineJobAutomationAction[];
  total: number;
}

export interface FineJobBossExecutorInstance {
  id: string;
  label: string;
  protocol_version: string;
  plugin_version: string;
  capabilities: string[];
  queue_state: "running" | "paused" | "risk_paused";
  risk_state: string;
  browser_connected: boolean;
  last_heartbeat_at?: string | null;
  task_cooldown_max_seconds: number;
  page_load_wait_max_seconds: number;
  runtime_phase?: "idle" | "task_cooldown";
  runtime_detail?: string;
  runtime_until_at?: string | null;
  updated_at: string;
}

export interface FineJobBossExecutorQueueAction {
  id: string;
  job_id: string;
  review_item_id: string;
  action_type: string;
  task_type: "BOSS_DEFAULT_GREETING" | "TEST_DELAY";
  status: FineJobAutomationActionStatus;
  execution_state: FineJobBossExecutionState;
  execution_epoch: number;
  job_title: string;
  company_name: string;
  encrypt_job_id: string;
  last_status_code?: string | null;
  last_error?: string | null;
  close_page_after_completion: boolean;
  delay_seconds: number;
}

export interface FineJobBossExecutorTestJob {
  id: string;
  encrypt_job_id: string;
  title: string;
  company_name: string;
  job_link: string;
  created_at: string;
  updated_at: string;
}

export interface FineJobBossExecutorDashboard {
  executor: FineJobBossExecutorInstance | null;
  current_task?: FineJobBossExecutorQueueAction | null;
  queue: { actions: FineJobBossExecutorQueueAction[]; total: number };
  protocol_version: string;
}

export interface FineJobOperationsDashboard {
  generated_at: string;
  metrics: Record<string, number>;
  review_counts: Record<string, number>;
  action_counts: Record<string, number>;
  execution_counts: Record<string, number>;
  capture_counts: Record<string, number>;
  executor: FineJobBossExecutorInstance | null;
  current_task?: FineJobBossExecutorQueueAction | null;
  queue: { actions: FineJobBossExecutorQueueAction[]; total: number };
  recent_issues: FineJobActionLog[];
  legacy_runs: FineJobDeliveryRun[];
}

export interface FineJobBossNavigationTask {
  id: string;
  action_id?: string | null;
  job_id: string;
  source_context: "capture" | "history" | "review" | "queue";
  target_url: string;
  target_encrypt_job_id: string;
  browser_target_id?: string | null;
  status: "queued" | "opened" | "failed";
  error_code?: string | null;
  error_message?: string | null;
  created_at: string;
  updated_at: string;
  opened_at?: string | null;
}

export type FineJobChatSessionStatus = "active" | "human_takeover" | "paused" | "unsupported";
export type FineJobChatReplyStatus =
  | "pending_generation"
  | "generating"
  | "awaiting_review"
  | "confirmed"
  | "cancelled"
  | "stale"
  | "failed";

export interface FineJobChatLeader {
  account_uid: string;
  executor_id: string;
  tab_id: string;
  leader_epoch: number;
  lease_expires_at: string;
  updated_at: string;
}

export interface FineJobChatRuntime {
  id: "boss";
  listen_enabled: boolean;
  generation_enabled: boolean;
  send_enabled: boolean;
  trigger_mode: "immediate" | "interval" | "manual";
  interval_minutes: 0 | 5 | 10 | 30 | 60;
  last_scheduled_at?: string | null;
  leader_executor_id?: string | null;
  leader_tab_id?: string | null;
  leader_epoch: number;
  leader_lease_expires_at?: string | null;
  leaders?: FineJobChatLeader[];
  updated_at: string;
}

export interface FineJobChatSession {
  id: string;
  platform: "boss";
  account_uid: string;
  peer_uid: string;
  encrypt_peer_uid: string;
  security_id: string;
  job_id?: string | null;
  encrypt_job_id: string;
  job_title: string;
  peer_name: string;
  peer_title?: string;
  company_name: string;
  status: FineJobChatSessionStatus;
  identity_state?: "ready" | "incomplete";
  job_context_state?: "linked" | "unlinked";
  session_version: number;
  latest_message_id?: string | null;
  latest_inbound_message_id?: string | null;
  last_message_at?: string | null;
  latest_message_content?: string;
  latest_message_direction?: "inbound" | "outbound";
  latest_platform_msg_id?: string;
  platform_latest_message_status?: 0 | 1 | 2 | null;
  platform_relation_type?: 1 | 2 | 3 | 5 | null;
  platform_chat_status?: number | null;
  platform_latest_message_at?: string | null;
  platform_synced_at?: string | null;
  message_update_required?: boolean;
  has_local_messages?: boolean;
  history_has_more?: boolean;
  attention_status?: string;
  attention_label?: string;
  attention_action?: string;
  attention_reason?: string;
  attention_priority?: number;
  attention_updated_at?: string;
  progress?: FineJobJobProgress | null;
  reply_task_id?: string | null;
  reply_task_status?: FineJobChatReplyStatus | null;
  reply_draft_text?: string;
  reply_final_text?: string;
  unhandled_count?: number;
  created_at: string;
  updated_at: string;
}

export interface FineJobChatMessage {
  id: string;
  session_id: string;
  platform_message_id: string;
  direction: "inbound" | "outbound";
  message_type: "text" | "image" | "system" | "unknown";
  content: string;
  source: "websocket" | "manual" | "assistant";
  sent_at: string;
  observed_at: string;
  raw_meta: Record<string, unknown>;
}

export interface FineJobChatReplyTask {
  id: string;
  session_id: string;
  trigger_source: "realtime" | "interval" | "manual";
  action_kind?: "reply" | "followup" | "ask_rejection_reason";
  insight_id?: string | null;
  status: FineJobChatReplyStatus;
  based_on_message_id: string;
  based_on_session_version: number;
  generation_due_at?: string | null;
  input_message_ids?: string[];
  decision?: "reply" | "manual" | "ignore";
  facts_used?: string[];
  warnings?: string[];
  requires_user_input?: boolean;
  decision_reason?: string;
  content_categories?: string[];
  context: Record<string, unknown>;
  draft_text: string;
  final_text: string;
  generation_model: string;
  generation_error?: string | null;
  generated_at?: string | null;
  created_at: string;
  updated_at: string;
}

export interface FineJobChatSendAction {
  id: string;
  reply_task_id: string;
  session_id: string;
  status: "queued" | "leased" | "dispatching" | "accepted" | "failed" | "unknown" | "cancelled";
  text: string;
  execution_epoch: number;
  leader_tab_id?: string;
  leader_epoch?: number;
  dispatch_deadline_at?: string | null;
  outcome?: "accepted" | "failed" | "unknown" | null;
  status_code: string;
  error_message: string;
  evidence: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  platform_message_id?: string;
  client_mid?: string;
}

export interface FineJobConversationInsight {
  id: string;
  session_id: string;
  job_id?: string | null;
  run_id?: string | null;
  status: "analyzed" | "skipped" | "failed";
  insight: Record<string, unknown>;
  model: string;
  prompt_version: string;
  analysis_version: string;
  created_at: string;
  updated_at: string;
}

export interface FineJobChatSessionDetail {
  session: FineJobChatSession;
  messages: FineJobChatMessage[];
  reply_tasks: FineJobChatReplyTask[];
  send_actions: FineJobChatSendAction[];
  latest_conversation_insight?: FineJobConversationInsight | null;
  messages_truncated?: boolean;
  message_count?: number;
}

export interface FineJobChatRuntimeEnvelope { runtime: FineJobChatRuntime }
export interface FineJobChatSessionListEnvelope {
  sessions: FineJobChatSession[];
  next_offset?: number | null;
}

export interface FineJobChatFriendListRefreshResponse {
  account_uid: string;
  count: number;
  created_count: number;
  changed_count: number;
  source_url: string;
  synced_at: string | null;
  session_ids?: string[];
  created_session_ids?: string[];
  reused_local_snapshot?: boolean;
  age_minutes?: number | null;
}
export interface FineJobChatHistoryRefreshResponse {
  session_id: string;
  fetched_count: number;
  inserted_count: number;
  message_update_required: boolean;
  has_more: boolean;
}

export interface FineJobReviewChatLinkBatchResponse {
  matched: number;
  archived: number;
  confirmed: number;
  unmatched: number;
}
export interface FineJobChatBatchSummary {
  pending_chat_count: number;
  pending_job_count: number;
  queued_chat_count: number;
  batch_limit: number;
}
export interface FineJobChatBatchTask {
  id: string;
  status: "queued" | "running" | "completed" | "failed";
  total: number;
  current: number;
  chat_completed: number;
  job_completed: number;
  job_skipped: number;
  failed: number;
  current_session_name: string;
  current_job_title: string;
  stage: string;
  message: string;
  created_at: string;
  finished_at?: string | null;
}
export interface FineJobChatJobUpdateResponse {
  action: "view" | "update";
  history_job_id: string;
  job: FineJobBossHistoryJob;
  task?: FineJobBossCaptureTask | null;
}

export type FineJobJobHuntRefreshRunStatus =
  | "pending"
  | "running"
  | "completed"
  | "completed_with_errors"
  | "failed"
  | "cancelled";

export interface FineJobJobHuntRefreshWorkflowOptions {
  refresh_chat_list: boolean;
  refresh_chat_messages: boolean;
  refresh_related_jobs: boolean;
  analyze_conversations: boolean;
  generate_missing_suggestions: boolean;
  generate_reply_drafts?: boolean;
  generate_followup_recommendations?: boolean;
}

export interface FineJobJobHuntRefreshContext {
  timezone: "Asia/Shanghai";
  latest_local_message_at?: string | null;
  last_successful_completed_at?: string | null;
  default_since_time: string;
  latest_unconsumed_scope_id?: string | null;
  chat_list_synced_at?: string | null;
}

export type FineJobJobHuntRefreshScopeSourceMode = "auto" | "local" | "refresh";

export interface FineJobJobHuntRefreshScopeJobRef {
  entity_id: string;
  session_id: string;
  job_id?: string | null;
  encrypt_job_id?: string | null;
}

export interface FineJobJobHuntRefreshScope {
  id: string;
  selected_since_time: string;
  requested_source_mode: FineJobJobHuntRefreshScopeSourceMode;
  scope_source: "local" | "refresh";
  account_uid: string;
  source_url: string;
  friend_list_synced_at: string;
  chat_list_synced_at?: string | null;
  scope_generated_at: string;
  latest_local_message_at?: string | null;
  session_ids_in_scope: string[];
  session_ids_to_sync: string[];
  new_session_ids: string[];
  related_jobs: FineJobJobHuntRefreshScopeJobRef[];
  related_job_ids: string[];
  encrypt_job_ids: string[];
  jobs_to_collect: FineJobJobHuntRefreshScopeJobRef[];
  jobs_missing_jd: FineJobJobHuntRefreshScopeJobRef[];
  jobs_missing_evaluation: FineJobJobHuntRefreshScopeJobRef[];
  unresolved_session_ids: string[];
  counts: {
    refreshed_sessions: number;
    sessions_in_scope: number;
    sessions_to_sync: number;
    new_sessions_to_sync: number;
    related_jobs: number;
    chat_update_jobs: number;
    extra_jobs: number;
    jobs_to_update: number;
    jobs_to_collect: number;
    jobs_missing_jd: number;
    jobs_missing_evaluation: number;
    unresolved_relations: number;
  };
  friend_list_result: FineJobChatFriendListRefreshResponse;
  created_at: string;
}

export interface FineJobJobHuntRefreshItem {
  id: string;
  run_id: string;
  item_type: "chat_session" | "related_job";
  entity_id: string;
  session_id: string;
  job_id?: string | null;
  status: "pending" | "running" | "succeeded" | "failed" | "skipped";
  step: string;
  retryable: boolean;
  operation_ref_type?: string | null;
  operation_ref_id?: string | null;
  result: Record<string, unknown>;
  error_category?: string | null;
  error_message?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  created_at: string;
  updated_at: string;
}

export interface FineJobJobHuntRefreshProgressStep {
  total: number;
  completed: number;
  succeeded: number;
  failed: number;
  skipped?: number;
}

export interface FineJobJobHuntRefreshRun {
  id: string;
  scope_id: string;
  scope_generated_at: string;
  status: FineJobJobHuntRefreshRunStatus;
  selected_since_time: string;
  latest_local_message_at?: string | null;
  workflow_options: FineJobJobHuntRefreshWorkflowOptions;
  estimated_sessions: number;
  estimated_update_sessions: number;
  estimated_jobs: number;
  estimated_refresh_jobs: number;
  estimated_missing_jd: number;
  estimated_missing_suggestions: number;
  processed_sessions: number;
  processed_jobs: number;
  failed_sessions: number;
  failed_jobs: number;
  chat_list_status: "pending" | "running" | "succeeded" | "failed" | "skipped";
  chat_list_retryable: boolean;
  current_step: string;
  trigger_source: string;
  codex_session_ref?: string | null;
  summary: {
    chat_list?: Record<string, unknown>;
    sessions_total?: number;
    sessions_succeeded?: number;
    sessions_failed?: number;
    new_messages?: number;
    related_jobs_total?: number;
    jobs_succeeded?: number;
    jobs_failed?: number;
    jobs_created?: number;
    jobs_refreshed?: number;
    jobs_reused?: number;
    unresolved_jobs?: number;
    analysis?: Record<string, unknown>;
    conversations_analyzed?: number;
    conversations_skipped?: number;
    conversation_analysis_failed?: number;
    activities_written?: number;
    reply_drafts_generated?: number;
    missing_suggestions_total?: number;
    missing_suggestions_generated?: number;
    missing_suggestions_skipped?: number;
    progress_updates?: number;
    waiting_for_recruiter?: number;
    waiting_for_candidate?: number;
    followup_recommended?: number;
    resume_viewed?: number;
    under_review?: number;
    rejections_detected?: number;
    jobs_closed?: number;
  };
  error_summary?: string | null;
  prompt_submitted_at?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  created_at: string;
  updated_at: string;
  items: FineJobJobHuntRefreshItem[];
  progress: {
    chat_list: { status: string };
    chat_messages: FineJobJobHuntRefreshProgressStep;
    related_jobs: FineJobJobHuntRefreshProgressStep;
  };
  resume_available: boolean;
  scope: FineJobJobHuntRefreshScope;
}

export interface FineJobJobHuntRefreshRunListEnvelope {
  runs: FineJobJobHuntRefreshRun[];
}

export interface FineJobChatReplyEnvelope { reply_task: FineJobChatReplyTask }
export interface FineJobChatSendActionEnvelope { action: FineJobChatSendAction }

export interface PdfDraftCreateRequest {
  file_path: string;
  title?: string | null;
}

export interface PdfDraftReparseRequest {
  parser_name: PdfDraftParserName;
}

export interface PdfDraftPreviewPage {
  page_number: number;
  content_type: "markdown" | "text";
  content: string;
}

export interface PdfDraftParseResult {
  id: string;
  parser_name: string;
  status: string;
  raw_text: string;
  markdown_text?: string | null;
  preview_text: string;
  page_count: number;
  char_count: number;
  quality_score: number;
  is_ocr: boolean;
  warnings: string[];
  fallback_from?: string | null;
  fallback_reason?: string | null;
  created_at: string;
}

export interface PdfReparseJob {
  id: string;
  draft_id: string;
  parser_name: string;
  status: "queued" | "running" | "completed" | "failed" | "cancelled";
  created_at: string;
  started_at?: string | null;
  finished_at?: string | null;
  error_message?: string | null;
  processed_pages: number;
  total_pages: number;
  latest_available_page: number;
  cancel_requested: boolean;
  preview_result_id?: string | null;
}

export interface PdfDraft {
  id: string;
  file_path: string;
  title?: string | null;
  source_name: string;
  created_at: string;
  updated_at: string;
  saved_parse_result_id?: string | null;
  latest_preview_result_id?: string | null;
  parse_results: PdfDraftParseResult[];
}

export interface PdfDraftEnvelope {
  draft: PdfDraft;
}

export interface PdfDraftReparseResponse {
  draft: PdfDraft;
  job: PdfReparseJob;
}

export interface PdfReparseJobEnvelope {
  job: PdfReparseJob;
}

export interface PdfReparseJobListEnvelope {
  jobs: PdfReparseJob[];
}

export interface PdfDraftPreviewPageEnvelope {
  page: PdfDraftPreviewPage;
}

export interface PdfDraftCommitResponse {
  item: ApiPoolItem;
}

export interface PdfDraftDeleteResponse {
  deleted: boolean;
}

export type WebDraftParserName = "playwright_dom";

export interface WebDraftCreateRequest {
  url: string;
  title?: string | null;
  session_profile_id?: string | null;
}

export interface WebDraftReparseRequest {
  parser_name: WebDraftParserName;
  session_profile_id?: string | null;
}

export interface WebDraftPreviewPage {
  page_number: number;
  content_type: "markdown" | "text";
  content: string;
}

export interface WebDraftParseResult {
  id: string;
  parser_name: WebDraftParserName;
  status: string;
  raw_text: string;
  markdown_text?: string | null;
  preview_text: string;
  section_count: number;
  char_count: number;
  quality_score: number;
  warnings: string[];
  auth_mode: string;
  created_at: string;
}

export interface WebReparseJob {
  id: string;
  draft_id: string;
  parser_name: WebDraftParserName;
  status: "queued" | "running" | "completed" | "failed" | "cancelled";
  created_at: string;
  started_at?: string | null;
  finished_at?: string | null;
  error_message?: string | null;
  processed_pages: number;
  total_pages: number;
  latest_available_page: number;
  cancel_requested: boolean;
  preview_result_id?: string | null;
}

export interface WebDraft {
  id: string;
  url: string;
  title?: string | null;
  source_name: string;
  session_profile_id?: string | null;
  created_at: string;
  updated_at: string;
  saved_parse_result_id?: string | null;
  latest_preview_result_id?: string | null;
  parse_results: WebDraftParseResult[];
}

export interface WebDraftEnvelope {
  draft: WebDraft;
}

export interface WebDraftReparseResponse {
  draft: WebDraft;
  job: WebReparseJob;
}

export interface WebReparseJobEnvelope {
  job: WebReparseJob;
}

export interface WebReparseJobListEnvelope {
  jobs: WebReparseJob[];
}

export interface WebDraftPreviewPageEnvelope {
  page: WebDraftPreviewPage;
}

export interface WebDraftCommitResponse {
  item: ApiPoolItem;
}

export interface WebDraftDeleteResponse {
  deleted: boolean;
}

export type WebSessionProfileMode = "browser_profile" | "app_session";
export type WebSessionProfileStatus = "ready" | "needs_login" | "invalid";

export interface WebSessionProfile {
  id: string;
  name: string;
  mode: WebSessionProfileMode;
  browser_channel: string;
  profile_path?: string | null;
  managed_profile_path?: string | null;
  login_url?: string | null;
  status: WebSessionProfileStatus;
  status_detail: string;
  created_at: string;
  updated_at: string;
}

export interface WebSessionProfileCreateRequest {
  name: string;
  mode: WebSessionProfileMode;
  browser_channel?: string | null;
  profile_path?: string | null;
  login_url?: string | null;
}

export interface WebSessionProfileUpdateRequest {
  name?: string | null;
  browser_channel?: string | null;
  profile_path?: string | null;
  login_url?: string | null;
}

export interface WebSessionProfileLoginRequest {
  login_url?: string | null;
}

export interface WebSessionProfileEnvelope {
  profile: WebSessionProfile;
}

export interface WebSessionProfileListEnvelope {
  profiles: WebSessionProfile[];
}

export interface WebSessionProfileDeleteResponse {
  deleted: boolean;
}

export interface SummaryPrecheckItem {
  id: string;
  knowledge_item_id?: string;
  title: string;
  source_type: string;
  cleaning_level?: "basic" | "enhanced" | null;
  current_status: string;
}

export interface SummaryPrecheckResponse {
  items: SummaryPrecheckItem[];
  count: number;
  output_dir: string;
  run_hint?: string | null;
  failed_retry_count?: number | null;
}

export interface SummaryRunCreateResponse {
  run_id: string;
  status: RunStatus;
  stage: string;
}

export interface ApiRunSnapshot {
  run_id: string;
  task_type: string;
  status: string;
  stage: string;
  total_items: number;
  succeeded_items: number;
  failed_items: number;
  skipped_items: number;
  current_item_id: string | null;
  current_item_label: string | null;
  error_category: string | null;
  error_message: string | null;
  updated_at: string;
  finished_at?: string | null;
  report_week_key?: string | null;
  linked_report_version_id?: string | null;
  executor_type?: string | null;
  executor_version?: string | null;
  model_name?: string | null;
  reasoning_effort?: string | null;
  result_snapshots?: RunResultSnapshot[] | null;
}

export interface RunListResponse {
  items: ApiRunSnapshot[];
  total: number;
}

export interface UiRunSnapshot {
  runId: string;
  taskType: string;
  status: string;
  stage: string;
  totalItems: number;
  succeededItems: number;
  failedItems: number;
  skippedItems: number;
  totalProcessed: number;
  progressPercent: number;
  currentItemId: string | null;
  currentItemLabel: string | null;
  errorCategory: string | null;
  errorMessage: string | null;
  updatedAt: string;
  finishedAt: string | null;
  reportWeekKey: string | null;
  linkedReportVersionId: string | null;
  executorType: string;
  executorVersion: string | null;
  modelName: string | null;
  reasoningEffort: string | null;
  resultSnapshots: RunResultSnapshot[];
}

export interface RunResultSnapshot {
  snapshot_id: string;
  knowledge_item_id: string;
  title: string;
  final_category?: string | null;
  created_at: string;
  markdown_path?: string | null;
  markdown_filename?: string | null;
}

export interface AppConfigPayload {
  app_data_dir?: string | null;
  sqlite_path?: string | null;
  qdrant_path?: string | null;
  output_root?: string | null;
  summary_output_dir?: string | null;
  report_output_dir?: string | null;
  llm_provider?: string | null;
  llm_model?: string | null;
  llm_base_url?: string | null;
  llm_api_key?: string | null;
  llm_configured?: boolean | null;
  embedding_provider?: string | null;
  embedding_model?: string | null;
  embedding_base_url?: string | null;
  embedding_api_key?: string | null;
  embedding_configured?: boolean | null;
  fetch_concurrency?: number | null;
  llm_concurrency?: number | null;
  embedding_concurrency?: number | null;
  quick_capture_hotkey?: string | null;
  quick_capture_screenshot_hotkey?: string | null;
  close_to_tray?: boolean | null;
  quick_capture_always_on_top?: boolean | null;
  reasoning_executor?: "llm" | "codex-cli" | null;
  codex_cli_path?: string | null;
  codex_model?: string | null;
  codex_reasoning_effort?: "minimal" | "low" | "medium" | "high" | "xhigh" | null;
  codex_timeout_seconds?: number | null;
}

export interface ProviderConnectivityCheckResponse {
  capability: "llm" | "embedding";
  ok: boolean;
  status: "ready" | "failed" | "invalid";
  provider: string | null;
  model: string | null;
  base_url: string | null;
  detail: string;
  error_category?: string | null;
  checked_at: string;
}

export interface CodexConnectivityCheckResponse {
  capability: "codex-cli";
  ok: boolean;
  status: "ready" | "failed" | "invalid";
  cli_path: string | null;
  cli_version: string | null;
  authenticated: boolean;
  model: string | null;
  reasoning_effort: string | null;
  detail: string;
  error_category?: string | null;
  checked_at: string;
}

export interface CodexModelItem {
  id: string;
  label?: string | null;
  reasoning_efforts?: string[];
}

export interface CodexModelListResponse {
  capability: "codex-models";
  models: CodexModelItem[];
  fetched_at: string;
}

export interface ReportPrecheckResponse {
  week_key?: string;
  available_week_keys?: string[];
  existing_versions?: number[];
  next_version?: number;
}

export interface ReportVersionSummary {
  week_key: string;
  version: number;
  generated_at?: string;
}

export interface ReportEvidenceBundle {
  memory_context_items: unknown[];
  citations: unknown[];
  grounded_claims: unknown[];
  summary_segments: unknown[];
  memory_context_count: number;
  evidence_citation_count: number;
  grounded_claim_count: number;
}

export interface ReportSnapshotItem {
  snapshot_id: string;
  title: string;
  final_category: string;
  created_at: string;
  evidence_citation_count: number;
  memory_context_count: number;
  grounded_claim_count: number;
  top_evidence_titles: string[];
  top_grounded_claims: string[];
  evidence_bundle: ReportEvidenceBundle;
}

export interface ReportGroundedItem {
  snapshot_id: string;
  title: string;
  final_category: string;
  claim: string;
  citation_ids: string[];
  evidence_titles: string[];
}

export interface ReportSnapshotPayload {
  category_stats: Record<string, number>;
  source_distribution: Record<string, number>;
  reading_trend: Record<string, number>;
  evidence_citation_total: number;
  grounded_claim_total: number;
  grounded_items: ReportGroundedItem[];
  items: ReportSnapshotItem[];
}

export interface ReportVersionDetail {
  id?: string;
  week_key?: string;
  version?: number;
  markdown_content: string;
  snapshot_payload?: ReportSnapshotPayload;
  markdown_path?: string | null;
  generated_at?: string | null;
}

export interface ResultDetail {
  id: string;
  knowledge_item_id?: string;
  summary_run_id?: string;
  title?: string | null;
  source_type?: string | null;
  source_value?: string | null;
  generated_category?: string | null;
  generated_tags?: string[] | null;
  final_category?: string | null;
  final_tags?: string[] | null;
  summary_text?: string | null;
  viewpoint_text?: string | null;
  controversy_text?: string | null;
  evidence_bundle?: unknown;
  markdown_path?: string | null;
  markdown_filename?: string | null;
  markdown_content?: string | null;
  related_items?: unknown;
  relation_meta?: unknown;
  summary_meta?: unknown;
  summary_metadata?: unknown;
  created_at?: string | null;
  edited_at?: string | null;
}

export interface ActiveParseResultDetail {
  knowledge_item_id: string;
  source_type: string;
  source_value: string;
  title?: string | null;
  canonical_content: string;
  id: string;
  parser_name: string;
  status: string;
  raw_text: string;
  markdown_text?: string | null;
  preview_text: string;
  page_count: number;
  char_count: number;
  quality_score: number;
  is_ocr: boolean;
  warnings: string[];
  fallback_from?: string | null;
  fallback_reason?: string | null;
  created_at: string;
  saved_at?: string | null;
}

export interface ActiveParseResultEnvelope {
  parse_result: ActiveParseResultDetail;
}

export interface RetrievalFilterPayload {
  source_types?: string[] | null;
  created_at_from?: string | null;
  created_at_to?: string | null;
  knowledge_item_ids?: string[] | null;
  keyword?: string | null;
  category?: string | null;
  user_tags?: string[] | null;
  ai_tags?: string[] | null;
}

export interface RetrievalCitation {
  citation_id: string;
  rank: number;
  knowledge_item_id: string;
  chunk_id: string;
  parent_chunk_id: string;
  title: string;
  section_title: string;
  source_type: string;
  source_name: string;
  source_value: string;
  created_at: string;
  snippet: string;
  context_snippet: string;
  expanded_context_snippet: string;
}

export interface QAGroundedItem {
  snapshot_id: string;
  title: string;
  final_category: string;
  claim: string;
  citation_ids: string[];
  evidence_titles: string[];
}

export type QAMode = "answer" | "knowledge_point" | "summary" | "source";

export interface QARewriteMeta {
  rewritten_question: string;
  requires_history: boolean;
  used_history: boolean;
  intent: string;
  risk_flags: string[];
  confidence: number;
  strategy: string;
}

export interface QAVerificationMeta {
  status: "passed" | "failed" | "skipped";
  reason: string;
  supported_terms: number;
  answer_terms: number;
}

export interface QAAnswerRequestPayload {
  question: string;
  session_id?: string;
  mode: QAMode;
  limit?: number;
  filters?: RetrievalFilterPayload;
}

export interface QAAnswerResponse {
  session_id: string;
  mode: QAMode;
  rewritten_question: string;
  rewrite: QARewriteMeta;
  question: string;
  answer: string;
  answer_status: "grounded" | "insufficient_evidence" | "needs_clarification";
  confidence: number;
  applied_filters: RetrievalFilterPayload;
  citations: RetrievalCitation[];
  used_grounded_items: QAGroundedItem[];
  suggested_queries: string[];
  verification?: QAVerificationMeta;
  retry_count?: number;
}

export interface QAConversationMessage {
  message_id: string;
  role: "user" | "assistant";
  content: string;
  created_at: string;
  question?: string | null;
  rewritten_question?: string | null;
  rewrite?: QARewriteMeta | null;
  answer_status?: "grounded" | "insufficient_evidence" | "needs_clarification" | null;
  confidence?: number | null;
  applied_filters?: RetrievalFilterPayload | null;
  citations?: RetrievalCitation[];
  used_grounded_items?: QAGroundedItem[];
  suggested_queries?: string[];
  verification?: QAVerificationMeta | null;
  retry_count?: number | null;
}

export interface QASessionSummary {
  session_id: string;
  title: string;
  mode: QAMode;
  created_at: string;
  updated_at: string;
  last_question?: string | null;
  message_count: number;
}

export interface QASessionDetail extends QASessionSummary {
  messages: QAConversationMessage[];
}

export interface QASessionListEnvelope {
  items: QASessionSummary[];
}

export interface ApiErrorShape {
  error_category?: string | null;
  error_message?: string | null;
  detail?: string | Record<string, unknown> | null;
}

export interface PollFallbackInput {
  lastEventAt: number;
  now: number;
  thresholdMs: number;
  currentMode: "idle" | "sse" | "polling";
}
