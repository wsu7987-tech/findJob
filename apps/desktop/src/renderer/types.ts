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
  filter_status?: "pass" | "reject" | "review" | "exclude" | null;
  filter_reasons?: string[];
  filter_missing_fields?: string[];
  filter_strategy_id?: string | null;
  company_id?: string | null;
  company_type?: FineJobCompanyType;
  is_outsourcing_company?: boolean;
  is_blacklisted?: boolean;
  application_status?: "applied" | "cleared" | null;
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
  exclude_outsourcing_companies: boolean;
  applied_company: FineJobCooldownRule;
  detailed_company: FineJobCooldownRule;
  evaluated_company: FineJobCooldownRule;
  applied_job: FineJobCooldownRule;
  detailed_job: FineJobCooldownRule;
  evaluated_job: FineJobCooldownRule;
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
export type FineJobAutomationActionStatus =
  | "queued"
  | "leased"
  | "succeeded"
  | "failed"
  | "blocked"
  | "unknown"
  | "cancelled";

export type FineJobBossExecutionState =
  | "queued"
  | "opening_page"
  | "waiting_page_ready"
  | "page_verified"
  | "ready_to_dispatch"
  | "dispatch_started"
  | "request_accepted"
  | "succeeded"
  | "cancellation_requested"
  | "cancelled"
  | "blocked"
  | "failed_before_dispatch"
  | "failed_after_dispatch"
  | "unknown_after_dispatch";

export type FineJobBossVerificationState =
  | "not_required"
  | "waiting_refresh"
  | "refreshing"
  | "waiting_snapshot"
  | "page_confirmed"
  | "manual_confirmed"
  | "pending"
  | "chat_confirmed";

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
  evaluation: FineJobBossDeliveryEvaluation;
  created_at: string;
  updated_at: string;
  resolved_at?: string | null;
  action_id?: string | null;
  action_status?: FineJobAutomationActionStatus | null;
  execution_state?: FineJobBossExecutionState | null;
  action_last_error?: string | null;
}

export interface FineJobReviewItemListEnvelope {
  items: FineJobReviewItem[];
  total: number;
  page?: number;
  page_size?: number;
}

export interface FineJobReviewQuery {
  status?: FineJobReviewStatus;
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
  lease_owner?: string | null;
  lease_expires_at?: string | null;
  attempt_count: number;
  last_error?: string | null;
  job_title: string;
  company_name: string;
  created_at: string;
  updated_at: string;
  completed_at?: string | null;
  execution_state: FineJobBossExecutionState;
  execution_epoch: number;
  queue_position: number;
  page_open_attempts: number;
  page_deadline_at?: string | null;
  dispatch_started_at?: string | null;
  request_accepted_at?: string | null;
  verification_state: FineJobBossVerificationState;
  verification_method: "none" | "page_refresh" | "manual" | "chat";
  verification_delay_seconds?: number | null;
  verification_due_at?: string | null;
  verification_started_at?: string | null;
  verification_completed_at?: string | null;
  verification_attempts: number;
  cooldown_seconds?: number | null;
  next_eligible_at?: string | null;
  last_status_code?: string | null;
  result: Record<string, unknown>;
  navigation_task_id?: string | null;
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
  permission_state: "not_authorized" | "allowed" | "paused" | "risk_paused";
  queue_state: "running" | "paused" | "emergency_stopped" | "risk_paused";
  risk_state: string;
  browser_connected: boolean;
  current_action_id?: string | null;
  current_epoch?: number | null;
  cooldown_seconds?: number | null;
  next_eligible_at?: string | null;
  last_heartbeat_at?: string | null;
  updated_at: string;
}

export interface FineJobBossExecutorQueueAction {
  id: string;
  job_id: string;
  review_item_id: string;
  action_type: "BOSS_DEFAULT_GREETING";
  status: FineJobAutomationActionStatus;
  execution_state: FineJobBossExecutionState;
  execution_epoch: number;
  queue_position: number;
  page_open_attempts: number;
  request_accepted_at?: string | null;
  verification_state: FineJobBossVerificationState;
  verification_method: "none" | "page_refresh" | "manual" | "chat";
  verification_delay_seconds?: number | null;
  verification_due_at?: string | null;
  verification_started_at?: string | null;
  verification_completed_at?: string | null;
  verification_attempts: number;
  job_title: string;
  company_name: string;
  encrypt_job_id: string;
  last_status_code?: string | null;
  last_error?: string | null;
}

export interface FineJobBossExecutorDashboard {
  executor: FineJobBossExecutorInstance | null;
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
  queue: { actions: FineJobBossExecutorQueueAction[]; total: number };
  current_action?: FineJobBossExecutorQueueAction | null;
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
  company_name: string;
  status: FineJobChatSessionStatus;
  session_version: number;
  latest_message_id?: string | null;
  latest_inbound_message_id?: string | null;
  last_message_at?: string | null;
  latest_message_content?: string;
  latest_message_direction?: "inbound" | "outbound";
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
  status: FineJobChatReplyStatus;
  based_on_message_id: string;
  based_on_session_version: number;
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
  outcome?: "accepted" | "failed" | "unknown" | null;
  status_code: string;
  error_message: string;
  evidence: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface FineJobChatSessionDetail {
  session: FineJobChatSession;
  messages: FineJobChatMessage[];
  reply_tasks: FineJobChatReplyTask[];
  send_actions: FineJobChatSendAction[];
}

export interface FineJobChatRuntimeEnvelope { runtime: FineJobChatRuntime }
export interface FineJobChatSessionListEnvelope { sessions: FineJobChatSession[] }
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
