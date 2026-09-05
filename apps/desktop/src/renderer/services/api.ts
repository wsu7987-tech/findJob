import type {
  ApiErrorShape,
  ApiRunSnapshot,
  ActiveParseResultEnvelope,
  AppConfigPayload,
  FeedbackValue,
  FineJobResumeCreateFromFileRequest,
  FineJobResumeEnvelope,
  FineJobResumeFactListEnvelope,
  FineJobResumeFactsSaveRequest,
  FineJobResumeListEnvelope,
  FineJobIntent,
  FineJobIntentEnvelope,
  FineJobDeliveryStrategy,
  FineJobDeliveryStrategyEnvelope,
  FineJobFilterStrategy,
  FineJobFilterStrategyEnvelope,
  FineJobFilterStrategyListEnvelope,
  FineJobFilterExclusionState,
  FineJobRecommendationStrategy,
  FineJobRecommendationStrategyEnvelope,
  FineJobRecommendationStrategyListEnvelope,
  FineJobStrategyChangeSetEnvelope,
  FineJobStrategyChangeSetListEnvelope,
  FineJobStrategySearchKeyword,
  FineJobStrategySearchKeywordEnvelope,
  FineJobStrategySearchKeywordListEnvelope,
  FineJobActionLogListEnvelope,
  FineJobDeliveryCandidateListEnvelope,
  FineJobDeliveryRunCreateRequest,
  FineJobDeliveryRunEnvelope,
  FineJobDeliveryRunListEnvelope,
  FineJobPlatformSession,
  FineJobPlatformSessionEnvelope,
  FineJobPlatformLoginActionEnvelope,
  FineJobPlatformSessionListEnvelope,
  FineJobBossBrowserStatus,
  FineJobBossNetworkDebugStatus,
  FineJobBossCityListResponse,
  FineJobBossCaptureRequest,
  FineJobBossCaptureTask,
  FineJobBossDetailSuggestionResponse,
  FineJobBossFilterApplicationResponse,
  FineJobBossDeliveryEvaluationResponse,
  FineJobBossHistoryDeliveryEvaluationResponse,
  FineJobBossHistoryQuery,
  FineJobBossHistoryJob,
  FineJobBossHistoryResponse,
  FineJobJobJourney,
  FineJobBossSearchPageRequest,
  FineJobBossSearchPageResponse,
  FineJobReviewBatchResponse,
  FineJobReviewChatLinkBatchResponse,
  FineJobReviewItemListEnvelope,
  FineJobReviewQuery,
  FineJobAutomationActionEnvelope,
  FineJobAutomationActionListEnvelope,
  FineJobAutomationActionStatus,
  FineJobBossExecutorDashboard,
  FineJobBossExecutorQueueAction,
  FineJobBossExecutorTestJob,
  FineJobBossNavigationTask,
  FineJobOperationsDashboard,
  FineJobChatRuntime,
  FineJobChatRuntimeEnvelope,
  FineJobChatSessionDetail,
  FineJobChatSessionListEnvelope,
  FineJobChatFriendListRefreshResponse,
  FineJobChatHistoryRefreshResponse,
  FineJobChatBatchSummary,
  FineJobChatBatchTask,
  FineJobChatJobUpdateResponse,
  FineJobJobHuntRefreshContext,
  FineJobJobHuntRefreshScope,
  FineJobJobHuntRefreshRun,
  FineJobJobHuntRefreshRunListEnvelope,
  FineJobJobHuntRefreshWorkflowOptions,
  FineJobChatReplyEnvelope,
  FineJobChatSendActionEnvelope,
  FineJobCodexPendingWork,
  FineJobCodexPermissions,
  FineJobCompanyEnvelope,
  FineJobCompanyListEnvelope,
  FineJobCompanyType,
  PoolCreateRequest,
  PoolCreateResponse,
  PoolCommitMetadataRequest,
  PoolListResponse,
  PoolMetadataSuggestionRequest,
  PoolMetadataSuggestionResponse,
  QAAnswerRequestPayload,
  QAAnswerResponse,
  QASessionDetail,
  QASessionListEnvelope,
  ReportPrecheckResponse,
  ReportVersionDetail,
  ReportVersionSummary,
  ResultDetail,
  RunListResponse,
  SummaryPrecheckResponse,
  SummaryRunCreateResponse
} from "../types";
import type {
  CodexConnectivityCheckResponse,
  CodexModelListResponse,
  ProviderConnectivityCheckResponse
} from "../types";
import type {
  PdfDraftCommitResponse,
  PdfDraftCreateRequest,
  PdfDraftDeleteResponse,
  PdfDraftEnvelope,
  PdfDraftPreviewPageEnvelope,
  PdfDraftReparseResponse,
  PdfReparseJobEnvelope,
  PdfReparseJobListEnvelope,
  PdfDraftReparseRequest
} from "../types";
import type {
  WebDraftCommitResponse,
  WebDraftCreateRequest,
  WebDraftDeleteResponse,
  WebDraftEnvelope,
  WebDraftPreviewPageEnvelope,
  WebDraftReparseResponse,
  WebReparseJobEnvelope,
  WebReparseJobListEnvelope,
  WebDraftReparseRequest,
  WebSessionProfileCreateRequest,
  WebSessionProfileDeleteResponse,
  WebSessionProfileEnvelope,
  WebSessionProfileListEnvelope,
  WebSessionProfileLoginRequest,
  WebSessionProfileUpdateRequest
} from "../types";
import { mapApiError } from "./contract";

export class ApiError extends Error {
  readonly statusCode: number;
  readonly errorCategory?: string | null;
  readonly details?: ApiErrorShape;
  readonly endpointUnavailable: boolean;
  readonly connectionUnavailable: boolean;

  constructor(statusCode: number, details?: ApiErrorShape) {
    super(
      mapApiError({
        ...details,
        statusCode
      })
    );
    this.name = "ApiError";
    this.statusCode = statusCode;
    this.errorCategory = details?.error_category;
    this.details = details;
    this.endpointUnavailable = statusCode === 404 || statusCode === 501;
    this.connectionUnavailable = false;
  }
}

export class NetworkError extends Error {
  readonly endpointUnavailable = false;
  readonly connectionUnavailable = true;

  constructor(message = "无法连接到本地后端，请确认后端服务已经启动。") {
    super(message);
    this.name = "NetworkError";
  }
}

let backendOriginPromise: Promise<string> | null = null;

export const getBackendOrigin = async () => {
  if (!backendOriginPromise) {
    backendOriginPromise = (async () => {
      if (window.desktopBridge) {
        const meta = await window.desktopBridge.getMeta();
        return meta.backendOrigin;
      }

      return import.meta.env.VITE_API_ORIGIN ?? "http://127.0.0.1:8000";
    })();
  }

  return backendOriginPromise;
};

const parseJsonSafely = async (response: Response) => {
  const contentType = response.headers.get("content-type") ?? "";
  if (!contentType.includes("application/json")) {
    return null;
  }

  try {
    return (await response.json()) as Record<string, unknown>;
  } catch {
    return null;
  }
};

const buildHeaders = (init?: RequestInit) => {
  const headers = new Headers(init?.headers ?? {});
  if (!headers.has("Accept")) {
    headers.set("Accept", "application/json");
  }
  if (init?.body != null && !headers.has("Content-Type") && !(init.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }
  return headers;
};

export const request = async <T>(path: string, init?: RequestInit) => {
  const origin = await getBackendOrigin();
  const url = new URL(path, origin).toString();

  let response: Response;
  try {
    response = await fetch(url, {
      ...init,
      headers: buildHeaders(init)
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw error;
    }
    throw new NetworkError();
  }

  const data = await parseJsonSafely(response);

  if (!response.ok) {
    throw new ApiError(response.status, (data ?? undefined) as ApiErrorShape | undefined);
  }

  return (data ?? null) as T;
};

export const api = {
  async getPoolItems() {
    return request<PoolListResponse>("/api/pool/items");
  },
  async createPoolItem(payload: PoolCreateRequest) {
    return request<PoolCreateResponse>("/api/pool/items", {
      method: "POST",
      body: JSON.stringify(payload)
    });
  },
  async suggestPoolMetadata(payload: PoolMetadataSuggestionRequest) {
    return request<PoolMetadataSuggestionResponse>("/api/pool/metadata-suggestions", {
      method: "POST",
      body: JSON.stringify(payload)
    });
  },
  async createPdfDraft(payload: PdfDraftCreateRequest) {
    return request<PdfDraftReparseResponse>("/api/pdf/drafts", {
      method: "POST",
      body: JSON.stringify(payload)
    });
  },
  async getPdfDraft(draftId: string) {
    return request<PdfDraftEnvelope>(`/api/pdf/drafts/${draftId}`);
  },
  async reparsePdfDraft(draftId: string, payload: PdfDraftReparseRequest, signal?: AbortSignal) {
    return request<PdfDraftReparseResponse>(`/api/pdf/drafts/${draftId}/reparse`, {
      method: "POST",
      body: JSON.stringify(payload),
      signal
    });
  },
  async listPdfReparseJobs() {
    return request<PdfReparseJobListEnvelope>("/api/pdf/drafts/jobs");
  },
  async getPdfReparseJob(draftId: string, jobId: string) {
    return request<PdfReparseJobEnvelope>(`/api/pdf/drafts/${draftId}/jobs/${jobId}`);
  },
  async cancelPdfReparseJob(draftId: string, jobId: string) {
    return request<PdfReparseJobEnvelope>(`/api/pdf/drafts/${draftId}/jobs/${jobId}/cancel`, {
      method: "POST"
    });
  },
  async getPdfDraftPreviewPage(draftId: string, parseResultId: string, pageNumber: number) {
    return request<PdfDraftPreviewPageEnvelope>(
      `/api/pdf/drafts/${draftId}/parse-results/${parseResultId}/pages/${pageNumber}`
    );
  },
  async savePdfDraftParseResult(draftId: string, parseResultId: string) {
    return request<PdfDraftEnvelope>(
      `/api/pdf/drafts/${draftId}/parse-results/${parseResultId}/save`,
      {
        method: "POST"
      }
    );
  },
  async commitPdfDraft(draftId: string, payload?: PoolCommitMetadataRequest) {
    return request<PdfDraftCommitResponse>(`/api/pdf/drafts/${draftId}/commit`, {
      method: "POST",
      body: JSON.stringify(payload ?? {})
    });
  },
  async deletePdfDraft(draftId: string) {
    return request<PdfDraftDeleteResponse>(`/api/pdf/drafts/${draftId}`, {
      method: "DELETE"
    });
  },
  async createWebDraft(payload: WebDraftCreateRequest) {
    return request<WebDraftReparseResponse>("/api/web/drafts", {
      method: "POST",
      body: JSON.stringify(payload)
    });
  },
  async getWebDraft(draftId: string) {
    return request<WebDraftEnvelope>(`/api/web/drafts/${draftId}`);
  },
  async reparseWebDraft(draftId: string, payload: WebDraftReparseRequest, signal?: AbortSignal) {
    return request<WebDraftReparseResponse>(`/api/web/drafts/${draftId}/reparse`, {
      method: "POST",
      body: JSON.stringify(payload),
      signal
    });
  },
  async listWebReparseJobs() {
    return request<WebReparseJobListEnvelope>("/api/web/drafts/jobs");
  },
  async getWebReparseJob(draftId: string, jobId: string) {
    return request<WebReparseJobEnvelope>(`/api/web/drafts/${draftId}/jobs/${jobId}`);
  },
  async cancelWebReparseJob(draftId: string, jobId: string) {
    return request<WebReparseJobEnvelope>(`/api/web/drafts/${draftId}/jobs/${jobId}/cancel`, {
      method: "POST"
    });
  },
  async getWebDraftPreviewPage(draftId: string, parseResultId: string, pageNumber: number) {
    return request<WebDraftPreviewPageEnvelope>(
      `/api/web/drafts/${draftId}/parse-results/${parseResultId}/pages/${pageNumber}`
    );
  },
  async saveWebDraftParseResult(draftId: string, parseResultId: string) {
    return request<WebDraftEnvelope>(
      `/api/web/drafts/${draftId}/parse-results/${parseResultId}/save`,
      {
        method: "POST"
      }
    );
  },
  async commitWebDraft(draftId: string, payload?: PoolCommitMetadataRequest) {
    return request<WebDraftCommitResponse>(`/api/web/drafts/${draftId}/commit`, {
      method: "POST",
      body: JSON.stringify(payload ?? {})
    });
  },
  async deleteWebDraft(draftId: string) {
    return request<WebDraftDeleteResponse>(`/api/web/drafts/${draftId}`, {
      method: "DELETE"
    });
  },
  async listWebSessionProfiles() {
    return request<WebSessionProfileListEnvelope>("/api/web/session-profiles");
  },
  async createWebSessionProfile(payload: WebSessionProfileCreateRequest) {
    return request<WebSessionProfileEnvelope>("/api/web/session-profiles", {
      method: "POST",
      body: JSON.stringify(payload)
    });
  },
  async updateWebSessionProfile(profileId: string, payload: WebSessionProfileUpdateRequest) {
    return request<WebSessionProfileEnvelope>(`/api/web/session-profiles/${profileId}`, {
      method: "PATCH",
      body: JSON.stringify(payload)
    });
  },
  async startWebSessionProfileLogin(profileId: string, payload: WebSessionProfileLoginRequest) {
    return request<WebSessionProfileEnvelope>(`/api/web/session-profiles/${profileId}/login`, {
      method: "POST",
      body: JSON.stringify(payload)
    });
  },
  async deleteWebSessionProfile(profileId: string) {
    return request<WebSessionProfileDeleteResponse>(`/api/web/session-profiles/${profileId}`, {
      method: "DELETE"
    });
  },
  async deletePoolItem(itemId: string) {
    return request<{ deleted: boolean }>(`/api/pool/items/${itemId}`, {
      method: "DELETE"
    });
  },
  async reingestPoolItem(itemId: string) {
    return request<{ accepted: boolean }>(`/api/pool/items/${itemId}/reingest`, {
      method: "POST"
    });
  },
  async resummarizePoolItem(itemId: string) {
    return request<{ accepted: boolean }>(`/api/pool/items/${itemId}/resummarize`, {
      method: "POST"
    });
  },
  async getSummaryPrecheck() {
    return request<SummaryPrecheckResponse>("/api/summary/precheck");
  },
  async createSummaryRun(poolIds: string[]) {
    return request<SummaryRunCreateResponse>("/api/summary/runs", {
      method: "POST",
      body: JSON.stringify({ pool_ids: poolIds })
    });
  },
  async getRun(runId: string) {
    return request<ApiRunSnapshot>(`/api/runs/${runId}`);
  },
  async getRuns(taskType?: string, status?: string) {
    const params = new URLSearchParams();
    if (taskType) {
      params.set("task_type", taskType);
    }
    if (status) {
      params.set("status", status);
    }
    const suffix = params.toString() ? `?${params.toString()}` : "";
    return request<RunListResponse>(`/api/runs${suffix}`);
  },
  async cancelRun(runId: string) {
    return request<ApiRunSnapshot>(`/api/runs/${runId}/cancel`, {
      method: "POST"
    });
  },
  async getConfig() {
    return request<AppConfigPayload>("/api/config");
  },
  async updateConfig(payload: Partial<AppConfigPayload>) {
    return request<AppConfigPayload>("/api/config", {
      method: "PATCH",
      body: JSON.stringify(payload)
    });
  },
  async checkLlmConnection() {
    return request<ProviderConnectivityCheckResponse>("/api/config/check-llm", {
      method: "POST"
    });
  },
  async checkEmbeddingConnection() {
    return request<ProviderConnectivityCheckResponse>("/api/config/check-embedding", {
      method: "POST"
    });
  },
  async checkCodexConnection() {
    return request<CodexConnectivityCheckResponse>("/api/config/check-codex", {
      method: "POST"
    });
  },
  async listCodexModels(cliPath: string) {
    return request<CodexModelListResponse>("/api/config/codex-models", {
      method: "POST",
      body: JSON.stringify({ cli_path: cliPath })
    });
  },
  async getFineJobIntent() {
    return request<FineJobIntentEnvelope>("/api/fine-job/job-intent");
  },
  async saveFineJobIntent(payload: FineJobIntent) {
    return request<FineJobIntentEnvelope>("/api/fine-job/job-intent", {
      method: "PUT",
      body: JSON.stringify(payload)
    });
  },
  async getFineJobDeliveryStrategy() {
    return request<FineJobDeliveryStrategyEnvelope>("/api/fine-job/delivery-strategy");
  },
  async saveFineJobDeliveryStrategy(payload: FineJobDeliveryStrategy) {
    return request<FineJobDeliveryStrategyEnvelope>("/api/fine-job/delivery-strategy", {
      method: "PUT",
      body: JSON.stringify(payload)
    });
  },
  async listFineJobFilterStrategies() {
    return request<FineJobFilterStrategyListEnvelope>("/api/fine-job/strategies/filters");
  },
  async createFineJobFilterStrategy(payload: FineJobFilterStrategy) {
    return request<FineJobFilterStrategyEnvelope>("/api/fine-job/strategies/filters", {
      method: "POST",
      body: JSON.stringify(payload)
    });
  },
  async updateFineJobFilterStrategy(strategyId: string, payload: FineJobFilterStrategy) {
    return request<FineJobFilterStrategyEnvelope>(
      `/api/fine-job/strategies/filters/${strategyId}`,
      { method: "PUT", body: JSON.stringify(payload) }
    );
  },
  async deleteFineJobFilterStrategy(strategyId: string) {
    return request<void>(`/api/fine-job/strategies/filters/${strategyId}`, {
      method: "DELETE"
    });
  },
  async getFineJobFilterExclusions(strategyId: string) {
    return request<FineJobFilterExclusionState>(
      `/api/fine-job/strategies/filters/${strategyId}/exclusions`
    );
  },
  async refreshFineJobFilterExclusions(strategyId: string) {
    return request<FineJobFilterExclusionState>(
      `/api/fine-job/strategies/filters/${strategyId}/exclusions/refresh`,
      { method: "POST" }
    );
  },
  async listFineJobStrategySearchKeywords(strategyId: string) {
    return request<FineJobStrategySearchKeywordListEnvelope>(
      `/api/fine-job/strategies/filters/${strategyId}/search-keywords`
    );
  },
  async createFineJobStrategySearchKeyword(
    strategyId: string,
    payload: Pick<FineJobStrategySearchKeyword, "keyword" | "reason" | "enabled" | "sort_order">
  ) {
    return request<FineJobStrategySearchKeywordEnvelope>(
      `/api/fine-job/strategies/filters/${strategyId}/search-keywords`,
      { method: "POST", body: JSON.stringify(payload) }
    );
  },
  async updateFineJobStrategySearchKeyword(
    strategyId: string,
    keywordId: string,
    payload: Pick<FineJobStrategySearchKeyword, "keyword" | "reason" | "enabled" | "sort_order">
  ) {
    return request<FineJobStrategySearchKeywordEnvelope>(
      `/api/fine-job/strategies/filters/${strategyId}/search-keywords/${keywordId}`,
      { method: "PATCH", body: JSON.stringify(payload) }
    );
  },
  async deleteFineJobStrategySearchKeyword(strategyId: string, keywordId: string) {
    return request<void>(
      `/api/fine-job/strategies/filters/${strategyId}/search-keywords/${keywordId}`,
      { method: "DELETE" }
    );
  },
  async reorderFineJobStrategySearchKeywords(strategyId: string, keywordIds: string[]) {
    return request<FineJobStrategySearchKeywordListEnvelope>(
      `/api/fine-job/strategies/filters/${strategyId}/search-keywords/order`,
      { method: "PUT", body: JSON.stringify({ keyword_ids: keywordIds }) }
    );
  },
  async listFineJobFilterStrategyChangeSets(strategyId: string) {
    return request<FineJobStrategyChangeSetListEnvelope>(
      `/api/fine-job/strategies/filters/${strategyId}/ai-change-sets`
    );
  },
  async applyFineJobFilterStrategyChangeSet(
    strategyId: string,
    changeSetId: string,
    payload: { mode: "update_current" | "save_as_new"; name?: string }
  ) {
    return request<FineJobStrategyChangeSetEnvelope>(
      `/api/fine-job/strategies/filters/${strategyId}/ai-change-sets/${changeSetId}/apply`,
      { method: "POST", body: JSON.stringify(payload) }
    );
  },
  async listFineJobRecommendationStrategies() {
    return request<FineJobRecommendationStrategyListEnvelope>(
      "/api/fine-job/strategies/recommendations"
    );
  },
  async createFineJobRecommendationStrategy(payload: FineJobRecommendationStrategy) {
    return request<FineJobRecommendationStrategyEnvelope>(
      "/api/fine-job/strategies/recommendations",
      { method: "POST", body: JSON.stringify(payload) }
    );
  },
  async updateFineJobRecommendationStrategy(
    strategyId: string,
    payload: FineJobRecommendationStrategy
  ) {
    return request<FineJobRecommendationStrategyEnvelope>(
      `/api/fine-job/strategies/recommendations/${strategyId}`,
      { method: "PUT", body: JSON.stringify(payload) }
    );
  },
  async deleteFineJobRecommendationStrategy(strategyId: string) {
    return request<void>(`/api/fine-job/strategies/recommendations/${strategyId}`, {
      method: "DELETE"
    });
  },
  async listFineJobRecommendationStrategyChangeSets(strategyId: string) {
    return request<FineJobStrategyChangeSetListEnvelope>(
      `/api/fine-job/strategies/recommendations/${strategyId}/ai-change-sets`
    );
  },
  async applyFineJobRecommendationStrategyChangeSet(
    strategyId: string,
    changeSetId: string,
    payload: { mode: "update_current" | "save_as_new"; name?: string }
  ) {
    return request<FineJobStrategyChangeSetEnvelope>(
      `/api/fine-job/strategies/recommendations/${strategyId}/ai-change-sets/${changeSetId}/apply`,
      { method: "POST", body: JSON.stringify(payload) }
    );
  },
  async listFineJobDeliveryRuns() {
    return request<FineJobDeliveryRunListEnvelope>("/api/fine-job/delivery-runs");
  },
  async createFineJobDeliveryRun(payload: FineJobDeliveryRunCreateRequest) {
    return request<FineJobDeliveryRunEnvelope>("/api/fine-job/delivery-runs", {
      method: "POST",
      body: JSON.stringify(payload)
    });
  },
  async getFineJobDeliveryRun(runId: string) {
    return request<FineJobDeliveryRunEnvelope>(`/api/fine-job/delivery-runs/${runId}`);
  },
  async deleteFineJobDeliveryRun(runId: string) {
    return request<{ deleted: boolean; id: string; candidates_deleted: number; logs_deleted: number }>(
      `/api/fine-job/delivery-runs/${runId}`,
      { method: "DELETE" }
    );
  },
  async listFineJobDeliveryCandidates(runId: string) {
    return request<FineJobDeliveryCandidateListEnvelope>(
      `/api/fine-job/delivery-runs/${runId}/candidates`
    );
  },
  async listFineJobDeliveryRunLogs(runId: string) {
    return request<FineJobActionLogListEnvelope>(`/api/fine-job/delivery-runs/${runId}/logs`);
  },
  async listFineJobRecentActionLogs(query: {
    query?: string;
    level?: string;
    action_type?: string;
    category?: string;
    outcome?: string;
    source?: string;
    created_from?: string;
    created_to?: string;
    page?: number;
    page_size?: number;
  } = {}) {
    const search = new URLSearchParams();
    for (const [key, value] of Object.entries(query)) {
      if (value !== undefined && value !== null && value !== "") search.set(key, String(value));
    }
    return request<FineJobActionLogListEnvelope>(
      `/api/fine-job/delivery-runs/logs/recent${search.size ? `?${search.toString()}` : ""}`
    );
  },
  async cleanupFineJobActionLogs(payload: {
    before: string;
    source: "all" | "legacy_run" | "main_workflow";
  }) {
    return request<{ deleted: number; before: string }>(
      "/api/fine-job/delivery-runs/logs/cleanup",
      { method: "POST", body: JSON.stringify(payload) }
    );
  },
  async getFineJobOperationsDashboard() {
    return request<FineJobOperationsDashboard>(
      "/api/fine-job/delivery-runs/operations/dashboard"
    );
  },
  async listFineJobPlatformSessions() {
    return request<FineJobPlatformSessionListEnvelope>("/api/fine-job/platform-sessions");
  },
  async getFineJobPlatformSession(platform: string) {
    return request<FineJobPlatformSessionEnvelope>(`/api/fine-job/platform-sessions/${platform}`);
  },
  async saveFineJobPlatformSession(platform: string, payload: FineJobPlatformSession) {
    return request<FineJobPlatformSessionEnvelope>(`/api/fine-job/platform-sessions/${platform}`, {
      method: "PUT",
      body: JSON.stringify(payload)
    });
  },
  async openFineJobBossLoginWindow() {
    return request<FineJobPlatformLoginActionEnvelope>(
      "/api/fine-job/platform-sessions/boss/login-window",
      { method: "POST" }
    );
  },
  async checkFineJobBossLoginStatus() {
    return request<FineJobPlatformLoginActionEnvelope>(
      "/api/fine-job/platform-sessions/boss/check",
      { method: "POST" }
    );
  },
  async getFineJobBossBrowserStatus() {
    return request<FineJobBossBrowserStatus>("/api/fine-job/boss-capture/status");
  },
  async listFineJobBossCities() {
    return request<FineJobBossCityListResponse>("/api/fine-job/boss-capture/cities");
  },
  async startFineJobBossBrowser() {
    return request<FineJobBossBrowserStatus>("/api/fine-job/boss-capture/browser/start", {
      method: "POST"
    });
  },
  async stopFineJobBossBrowser() {
    return request<FineJobBossBrowserStatus>("/api/fine-job/boss-capture/browser/stop", {
      method: "POST"
    });
  },
  async getFineJobBossNetworkDebugStatus() {
    return request<FineJobBossNetworkDebugStatus>("/api/fine-job/boss-network-debug/status");
  },
  async startFineJobBossNetworkDebug() {
    return request<FineJobBossNetworkDebugStatus>("/api/fine-job/boss-network-debug/start", {
      method: "POST"
    });
  },
  async stopFineJobBossNetworkDebug() {
    return request<FineJobBossNetworkDebugStatus>("/api/fine-job/boss-network-debug/stop", {
      method: "POST"
    });
  },
  async locateFineJobBossSearchPage(payload: FineJobBossSearchPageRequest) {
    return request<FineJobBossSearchPageResponse>("/api/fine-job/boss-capture/locate", {
      method: "POST",
      body: JSON.stringify(payload)
    });
  },
  async captureFineJobBossJobs(payload: FineJobBossCaptureRequest) {
    return request<FineJobBossCaptureTask>("/api/fine-job/boss-capture/capture", {
      method: "POST",
      body: JSON.stringify(payload)
    });
  },
  async getFineJobBossCaptureTask(taskId: string) {
    return request<FineJobBossCaptureTask>(`/api/fine-job/boss-capture/tasks/${taskId}`);
  },
  async continueFineJobBossCapture(taskId: string, pages: number) {
    return request<FineJobBossCaptureTask>(
      `/api/fine-job/boss-capture/tasks/${taskId}/continue`,
      {
        method: "POST",
        body: JSON.stringify({ pages })
      }
    );
  },
  async stopFineJobBossCaptureTask(taskId: string) {
    return request<FineJobBossCaptureTask>(
      `/api/fine-job/boss-capture/tasks/${taskId}/stop`,
      { method: "POST" }
    );
  },
  async listFineJobBossCaptureHistory(query: FineJobBossHistoryQuery) {
    const search = new URLSearchParams();
    for (const [key, value] of Object.entries(query)) {
      if (value !== undefined && value !== null && value !== "") {
        search.set(key, String(value));
      }
    }
    const suffix = search.size ? `?${search.toString()}` : "";
    return request<FineJobBossHistoryResponse>(
      `/api/fine-job/boss-capture/history${suffix}`
    );
  },
  async captureSelectedFineJobBossDetails(
    taskId: string,
    jobIds: string[],
    force = false,
    manualOverride = true
  ) {
    return request<FineJobBossCaptureTask>(
      `/api/fine-job/boss-capture/tasks/${taskId}/details`,
      {
        method: "POST",
        body: JSON.stringify({ job_ids: jobIds, force, manual_override: manualOverride })
      }
    );
  },
  async captureFineJobBossHistoryDetails(historyJobId: string, manualOverride = true) {
    return request<FineJobBossCaptureTask>(
      `/api/fine-job/boss-capture/history/${historyJobId}/details`,
      {
        method: "POST",
        body: JSON.stringify({ manual_override: manualOverride })
      }
    );
  },
  async suggestFineJobBossDetails(
    taskId: string,
    payload: {
      mode: "strategy" | "ai";
      command?: string;
      filter_strategy_id?: string | null;
      recommendation_strategy_id?: string | null;
      extra_requirement?: string;
      context_stale_action?: "regenerate" | "use_current" | "cancel";
    }
  ) {
    return request<FineJobBossDetailSuggestionResponse>(
      `/api/fine-job/boss-capture/tasks/${taskId}/suggestions`,
      {
        method: "POST",
        body: JSON.stringify(payload)
      }
    );
  },
  async applyFineJobBossFilter(taskId: string, strategyId: string) {
    return request<FineJobBossFilterApplicationResponse>(
      `/api/fine-job/boss-capture/tasks/${taskId}/filters`,
      { method: "POST", body: JSON.stringify({ strategy_id: strategyId }) }
    );
  },
  async evaluateFineJobBossDeliveries(
    taskId: string,
    payload: {
      recommendation_strategy_id: string;
      filter_strategy_id?: string | null;
      extra_requirement?: string;
      job_ids?: string[];
      context_stale_action?: "regenerate" | "use_current" | "cancel";
      manual_override?: boolean;
    }
  ) {
    return request<FineJobBossDeliveryEvaluationResponse>(
      `/api/fine-job/boss-capture/tasks/${taskId}/delivery-evaluations`,
      { method: "POST", body: JSON.stringify(payload) }
    );
  },
  async evaluateFineJobBossHistoryDelivery(
    historyJobId: string,
    payload: {
      recommendation_strategy_id: string;
      filter_strategy_id?: string | null;
      extra_requirement?: string;
      context_stale_action?: "regenerate" | "use_current" | "cancel";
      manual_override?: boolean;
    }
  ) {
    return request<FineJobBossHistoryDeliveryEvaluationResponse>(
      `/api/fine-job/boss-capture/history/${historyJobId}/delivery-evaluations`,
      {
        method: "POST",
        body: JSON.stringify(payload)
      }
    );
  },
  async listFineJobReviewItems(query: FineJobReviewQuery = {}) {
    const search = new URLSearchParams();
    for (const [key, value] of Object.entries(query)) {
      if (value !== undefined && value !== null && value !== "") search.set(key, String(value));
    }
    return request<FineJobReviewItemListEnvelope>(
      `/api/fine-job/review-items${search.size ? `?${search.toString()}` : ""}`
    );
  },
  async approveFineJobReviewItem(
    reviewItemId: string,
    payload: { message?: string; allow_override?: boolean }
  ) {
    return request<FineJobAutomationActionEnvelope>(
      `/api/fine-job/review-items/${reviewItemId}/approve`,
      { method: "POST", body: JSON.stringify(payload) }
    );
  },
  async rejectFineJobReviewItem(reviewItemId: string, note = "") {
    return request(`/api/fine-job/review-items/${reviewItemId}/reject`, {
      method: "POST",
      body: JSON.stringify({ note })
    });
  },
  async listFineJobAutomationActions(status?: FineJobAutomationActionStatus) {
    const suffix = status ? `?status=${encodeURIComponent(status)}` : "";
    return request<FineJobAutomationActionListEnvelope>(
      `/api/fine-job/automation-actions${suffix}`
    );
  },
  async listFineJobCompanies(query: {
    query?: string;
    company_type?: FineJobCompanyType | "";
    blacklist_status?: "all" | "blacklisted" | "normal";
    page?: number;
    page_size?: number;
  } = {}) {
    const search = new URLSearchParams();
    for (const [key, value] of Object.entries(query)) {
      if (value !== undefined && value !== null && value !== "") search.set(key, String(value));
    }
    return request<FineJobCompanyListEnvelope>(
      `/api/fine-job/companies${search.size ? `?${search.toString()}` : ""}`
    );
  },
  async createFineJobCompany(payload: {
    name: string;
    company_type: FineJobCompanyType;
    notes?: string;
  }) {
    return request<FineJobCompanyEnvelope>("/api/fine-job/companies", {
      method: "POST",
      body: JSON.stringify(payload)
    });
  },
  async archiveFineJobReviewItem(reviewItemId: string, note = "") {
    return request(`/api/fine-job/review-items/${reviewItemId}/archive`, {
      method: "POST",
      body: JSON.stringify({ note })
    });
  },
  async restoreFineJobReviewItem(reviewItemId: string) {
    return request(`/api/fine-job/review-items/${reviewItemId}/restore`, {
      method: "POST"
    });
  },
  async batchFineJobReviewItems(payload: {
    review_item_ids: string[];
    operation: "approve" | "reject" | "archive";
    note?: string;
    allow_override?: boolean;
  }) {
    return request<FineJobReviewBatchResponse>("/api/fine-job/review-items/batch", {
      method: "POST",
      body: JSON.stringify(payload)
    });
  },
  async updateFineJobCompany(
    companyId: string,
    payload: { canonical_name?: string; company_type?: FineJobCompanyType; notes?: string }
  ) {
    return request<FineJobCompanyEnvelope>(`/api/fine-job/companies/${companyId}`, {
      method: "PATCH",
      body: JSON.stringify(payload)
    });
  },
  async setFineJobCompanyBlacklist(companyId: string, blacklisted: boolean, reason = "") {
    return request<FineJobCompanyEnvelope>(`/api/fine-job/companies/${companyId}/blacklist`, {
      method: "PUT",
      body: JSON.stringify({ blacklisted, reason })
    });
  },
  async addFineJobCompanyAlias(companyId: string, aliasName: string) {
    return request<FineJobCompanyEnvelope>(`/api/fine-job/companies/${companyId}/aliases`, {
      method: "POST",
      body: JSON.stringify({ alias_name: aliasName })
    });
  },
  async deleteFineJobCompanyAlias(companyId: string, aliasId: string) {
    return request<{ deleted: boolean; id: string }>(
      `/api/fine-job/companies/${companyId}/aliases/${aliasId}`,
      { method: "DELETE" }
    );
  },
  async setFineJobJobApplication(jobId: string, applied: boolean, note = "") {
    return request(`/api/fine-job/companies/jobs/${jobId}/application`, {
      method: "PUT",
      body: JSON.stringify({ applied, note })
    });
  },
  async getFineJobBossExecutorStatus() {
    return request<FineJobBossExecutorDashboard>("/api/fine-job/boss-executor/status");
  },
  async controlFineJobBossExecutor(command: "start" | "pause") {
    return request<FineJobBossExecutorDashboard>("/api/fine-job/boss-executor/desktop-control", {
      method: "POST",
      body: JSON.stringify({ command })
    });
  },
  async updateFineJobBossExecutorSettings(payload: {
    task_cooldown_max_seconds: number;
    page_load_wait_max_seconds: number;
  }) {
    return request<FineJobBossExecutorDashboard>("/api/fine-job/boss-executor/settings", {
      method: "PATCH",
      body: JSON.stringify(payload)
    });
  },
  async listFineJobBossExecutorTestJobs() {
    return request<{ jobs: FineJobBossExecutorTestJob[] }>("/api/fine-job/boss-executor/test-jobs");
  },
  async updateFineJobBossExecutorTestJob(jobId: string, payload: {
    encrypt_job_id: string;
    job_link: string;
  }) {
    return request<{ job: FineJobBossExecutorTestJob }>(
      `/api/fine-job/boss-executor/test-jobs/${encodeURIComponent(jobId)}`,
      { method: "PUT", body: JSON.stringify(payload) }
    );
  },
  async createFineJobBossExecutorTestTask(payload: {
    job_id: string;
    close_page_after_completion: boolean;
    delay_seconds: number;
  }) {
    return request<{ task: FineJobBossExecutorQueueAction }>("/api/fine-job/boss-executor/test-tasks", {
      method: "POST",
      body: JSON.stringify(payload)
    });
  },
  async testFineJobBossExecutorHeartbeat() {
    return request<FineJobBossExecutorDashboard>("/api/fine-job/boss-executor/desktop-heartbeat-test", {
      method: "POST"
    });
  },
  async disconnectFineJobBossExecutor() {
    return request<FineJobBossExecutorDashboard>("/api/fine-job/boss-executor/desktop-disconnect", {
      method: "POST"
    });
  },
  async createFineJobBossPairingCode() {
    return request<{ code: string; expires_at: string }>(
      "/api/fine-job/boss-executor/pairing-code",
      { method: "POST" }
    );
  },
  async openFineJobBossJob(
    jobId: string,
    sourceContext: "capture" | "history" | "review"
  ) {
    return request<{ navigation: FineJobBossNavigationTask }>(
      "/api/fine-job/boss-navigation/open",
      {
        method: "POST",
        body: JSON.stringify({ job_id: jobId, source_context: sourceContext })
      }
    );
  },
  async returnFineJobBossActionToReview(actionId: string, reason = "用户退回待确认") {
    return request<FineJobAutomationActionEnvelope>(
      `/api/fine-job/automation-actions/${actionId}/return-to-review`,
      { method: "POST", body: JSON.stringify({ reason }) }
    );
  },
  async getFineJobChatRuntime() {
    return request<FineJobChatRuntimeEnvelope>("/api/fine-job/boss-chat/runtime");
  },
  async updateFineJobChatRuntime(payload: Partial<Pick<
    FineJobChatRuntime,
    "listen_enabled" | "generation_enabled" | "send_enabled" | "trigger_mode" | "interval_minutes"
  >>) {
    return request<FineJobChatRuntimeEnvelope>("/api/fine-job/boss-chat/runtime", {
      method: "PATCH",
      body: JSON.stringify(payload)
    });
  },
  async checkFineJobChatNow() {
    return request<{ generated: number }>("/api/fine-job/boss-chat/check", { method: "POST" });
  },
  async refreshFineJobChatFriendList() {
    return request<FineJobChatFriendListRefreshResponse>(
      "/api/fine-job/boss-chat/friend-list/refresh",
      { method: "POST" }
    );
  },
  async listFineJobChatSessions(params: {
    status?: string;
    account_uid?: string;
    q?: string;
    limit?: number;
    offset?: number;
  } = {}) {
    const query = new URLSearchParams();
    if (params.status) query.set("status", params.status);
    if (params.account_uid) query.set("account_uid", params.account_uid);
    if (params.q) query.set("q", params.q);
    if (params.limit) query.set("limit", String(params.limit));
    if (params.offset) query.set("offset", String(params.offset));
    const suffix = query.size ? `?${query.toString()}` : "";
    return request<FineJobChatSessionListEnvelope>(`/api/fine-job/boss-chat/sessions${suffix}`);
  },
  async getFineJobChatSession(sessionId: string) {
    return request<FineJobChatSessionDetail>(
      `/api/fine-job/boss-chat/sessions/${encodeURIComponent(sessionId)}`
    );
  },
  async refreshFineJobChatHistory(sessionId: string) {
    return request<FineJobChatHistoryRefreshResponse>(
      `/api/fine-job/boss-chat/sessions/${encodeURIComponent(sessionId)}/history/refresh`,
      { method: "POST" }
    );
  },
  async linkFineJobReviewItemsChat(payload: {
    status: "pending" | "rejected" | "approved";
    execution_view?: "running";
    decision?: FineJobReviewQuery["decision"];
    query?: string;
    execution_state?: FineJobReviewQuery["execution_state"];
    created_from?: string;
    created_to?: string;
  }) {
    return request<FineJobReviewChatLinkBatchResponse>("/api/fine-job/review-items/link-chat", {
      method: "POST",
      body: JSON.stringify(payload)
    });
  },
  async getFineJobChatBatchSummary() {
    return request<FineJobChatBatchSummary>("/api/fine-job/boss-chat/batch/summary");
  },
  async startFineJobChatBatch(batchSize: number, sessionIds?: string[]) {
    return request<FineJobChatBatchTask>("/api/fine-job/boss-chat/batch", {
      method: "POST",
      body: JSON.stringify({ batch_size: batchSize, session_ids: sessionIds })
    });
  },
  async getFineJobChatBatch(taskId: string) {
    return request<FineJobChatBatchTask>(
      `/api/fine-job/boss-chat/batch/${encodeURIComponent(taskId)}`
    );
  },
  async loadMoreFineJobChatHistory(sessionId: string) {
    return request<FineJobChatHistoryRefreshResponse>(
      `/api/fine-job/boss-chat/sessions/${encodeURIComponent(sessionId)}/history/more`,
      { method: "POST" }
    );
  },
  async getFineJobBossCaptureHistoryJob(historyJobId: string) {
    return request<FineJobBossHistoryJob>(
      `/api/fine-job/boss-capture/history/${encodeURIComponent(historyJobId)}`
    );
  },
  async getFineJobJobJourney(jobId: string) {
    return request<FineJobJobJourney>(
      `/api/fine-job/jobs/${encodeURIComponent(jobId)}/journey`
    );
  },
  async updateFineJobChatJob(sessionId: string) {
    return request<FineJobChatJobUpdateResponse>(
      `/api/fine-job/boss-chat/sessions/${encodeURIComponent(sessionId)}/job/update`,
      { method: "POST" }
    );
  },
  async getFineJobJobHuntRefreshContext() {
    return request<FineJobJobHuntRefreshContext>("/api/fine-job/job-hunt-refresh/context");
  },
  async discoverFineJobJobHuntRefreshScope(
    selectedSinceTime: string,
    sourceMode: "auto" | "local" | "refresh"
  ) {
    return request<FineJobJobHuntRefreshScope>("/api/fine-job/job-hunt-refresh/scopes", {
      method: "POST",
      body: JSON.stringify({ selected_since_time: selectedSinceTime, source_mode: sourceMode })
    });
  },
  async getFineJobJobHuntRefreshScope(scopeId: string) {
    return request<FineJobJobHuntRefreshScope>(
      `/api/fine-job/job-hunt-refresh/scopes/${encodeURIComponent(scopeId)}`
    );
  },
  async createFineJobJobHuntRefreshRun(payload: {
    scope_id: string;
    workflow_options: FineJobJobHuntRefreshWorkflowOptions;
    trigger_source?: string;
  }) {
    return request<FineJobJobHuntRefreshRun>("/api/fine-job/job-hunt-refresh/runs", {
      method: "POST",
      body: JSON.stringify(payload)
    });
  },
  async listFineJobJobHuntRefreshRuns(limit = 10) {
    return request<FineJobJobHuntRefreshRunListEnvelope>(
      `/api/fine-job/job-hunt-refresh/runs?limit=${encodeURIComponent(limit)}`
    );
  },
  async getFineJobJobHuntRefreshRun(runId: string) {
    return request<FineJobJobHuntRefreshRun>(
      `/api/fine-job/job-hunt-refresh/runs/${encodeURIComponent(runId)}`
    );
  },
  async attachFineJobJobHuntRefreshCodexSession(runId: string, codexSessionRef: string) {
    return request<FineJobJobHuntRefreshRun>(
      `/api/fine-job/job-hunt-refresh/runs/${encodeURIComponent(runId)}/codex-session`,
      {
        method: "PATCH",
        body: JSON.stringify({ codex_session_ref: codexSessionRef })
      }
    );
  },
  async markFineJobJobHuntRefreshPromptSubmitted(runId: string) {
    return request<FineJobJobHuntRefreshRun>(
      `/api/fine-job/job-hunt-refresh/runs/${encodeURIComponent(runId)}/prompt-submitted`,
      { method: "POST" }
    );
  },
  async cancelFineJobJobHuntRefreshRun(runId: string) {
    return request<FineJobJobHuntRefreshRun>(
      `/api/fine-job/job-hunt-refresh/runs/${encodeURIComponent(runId)}/cancel`,
      { method: "POST" }
    );
  },
  async setFineJobJobApplicationStatus(
    jobId: string,
    status: "pending_greeting" | "pending_application" | "communicating" | "rejected" | null,
    note = ""
  ) {
    return request(`/api/fine-job/companies/jobs/${encodeURIComponent(jobId)}/application-status`, {
      method: "PUT",
      body: JSON.stringify({ status, note })
    });
  },
  async generateFineJobChatReply(sessionId: string, instruction = "", regenerate = false) {
    const operation = regenerate ? "regenerate" : "generate";
    return request<FineJobChatReplyEnvelope>(
      `/api/fine-job/boss-chat/sessions/${encodeURIComponent(sessionId)}/${operation}`,
      { method: "POST", body: JSON.stringify({ instruction }) }
    );
  },
  async setFineJobChatSessionStatus(
    sessionId: string,
    operation: "take-over" | "resume" | "pause",
    reason: string
  ) {
    return request<{ session: FineJobChatSessionDetail["session"] }>(
      `/api/fine-job/boss-chat/sessions/${encodeURIComponent(sessionId)}/${operation}`,
      { method: "POST", body: JSON.stringify({ reason }) }
    );
  },
  async editFineJobChatReply(taskId: string, finalText: string) {
    return request<FineJobChatReplyEnvelope>(
      `/api/fine-job/boss-chat/reply-tasks/${encodeURIComponent(taskId)}`,
      { method: "PATCH", body: JSON.stringify({ final_text: finalText }) }
    );
  },
  async confirmFineJobChatReply(
    taskId: string,
    payload: { final_text: string; based_on_message_id: string; based_on_session_version: number }
  ) {
    return request<FineJobChatSendActionEnvelope>(
      `/api/fine-job/boss-chat/reply-tasks/${encodeURIComponent(taskId)}/confirm`,
      { method: "POST", body: JSON.stringify(payload) }
    );
  },
  async cancelFineJobChatReply(taskId: string, reason = "用户取消回复") {
    return request<FineJobChatReplyEnvelope>(
      `/api/fine-job/boss-chat/reply-tasks/${encodeURIComponent(taskId)}/cancel`,
      { method: "POST", body: JSON.stringify({ reason }) }
    );
  },
  async listFineJobResumes() {
    return request<FineJobResumeListEnvelope>("/api/fine-job/resumes");
  },
  async getFineJobResume(resumeId: string) {
    return request<FineJobResumeEnvelope>(`/api/fine-job/resumes/${resumeId}`);
  },
  async deleteFineJobResume(resumeId: string) {
    return request<void>(`/api/fine-job/resumes/${resumeId}`, {
      method: "DELETE"
    });
  },
  async createFineJobResumeFromFile(payload: FineJobResumeCreateFromFileRequest) {
    return request<FineJobResumeEnvelope>("/api/fine-job/resumes/from-file", {
      method: "POST",
      body: JSON.stringify(payload)
    });
  },
  async listFineJobResumeFacts(resumeId: string) {
    return request<FineJobResumeFactListEnvelope>(`/api/fine-job/resumes/${resumeId}/facts`);
  },
  async extractFineJobResumeFacts(resumeId: string) {
    return request<FineJobResumeFactListEnvelope>(
      `/api/fine-job/resumes/${resumeId}/facts/extract`,
      { method: "POST" }
    );
  },
  async saveFineJobResumeFacts(resumeId: string, payload: FineJobResumeFactsSaveRequest) {
    return request<FineJobResumeFactListEnvelope>(`/api/fine-job/resumes/${resumeId}/facts`, {
      method: "PUT",
      body: JSON.stringify(payload)
    });
  },
  async getFineJobCodexPermissions() {
    return request<FineJobCodexPermissions>("/api/fine-job/codex/permissions");
  },
  async updateFineJobCodexPermissions(payload: Pick<FineJobCodexPermissions, "enabled" | "permissions">) {
    return request<FineJobCodexPermissions>("/api/fine-job/codex/permissions", {
      method: "PATCH",
      body: JSON.stringify(payload)
    });
  },
  async getFineJobCodexPendingWork() {
    return request<FineJobCodexPendingWork>("/api/fine-job/codex/pending");
  },
  async decideFineJobCodexPending(
    resourceType: "greeting_preview" | "chat_reply",
    resourceId: string,
    operation: "approve" | "reject",
    payload: { expected_version: number; final_text?: string; allow_override?: boolean; note?: string }
  ) {
    return request<Record<string, unknown>>(
      `/api/fine-job/codex/pending/${resourceType}/${encodeURIComponent(resourceId)}/${operation}`,
      { method: "POST", body: JSON.stringify(payload) }
    );
  },
  async runQuickCaptureOcr(imageBase64: string) {
    return request<{ raw_text: string; captured_at: string; warnings: string[] }>(
      "/api/quick-capture/ocr",
      {
        method: "POST",
        body: JSON.stringify({ image_base64: imageBase64 })
      }
    );
  },
  async getReportPrecheck() {
    return request<ReportPrecheckResponse>("/api/report/precheck");
  },
  async createReportRun(weekKey?: string) {
    return request<{ run_id: string; version: number; week_key: string }>("/api/report/runs", {
      method: "POST",
      body: JSON.stringify(weekKey ? { week_key: weekKey } : {})
    });
  },
  async answerQuestion(payload: QAAnswerRequestPayload) {
    return request<QAAnswerResponse>("/api/qa/answer", {
      method: "POST",
      body: JSON.stringify(payload)
    });
  },
  async listQaSessions() {
    return request<QASessionListEnvelope>("/api/qa/sessions");
  },
  async getQaSession(sessionId: string) {
    return request<QASessionDetail>(`/api/qa/sessions/${sessionId}`);
  },
  async deleteQaSession(sessionId: string) {
    return request<{ deleted: boolean }>(`/api/qa/sessions/${sessionId}`, {
      method: "DELETE"
    });
  },
  async getReportVersions(weekKey: string) {
    return request<{ items: ReportVersionSummary[] }>(`/api/reports/${weekKey}/versions`);
  },
  async getReportVersion(weekKey: string, version: number) {
    return request<ReportVersionDetail>(`/api/reports/${weekKey}/versions/${version}`);
  },
  async getResult(snapshotId: string) {
    return request<ResultDetail>(`/api/results/${snapshotId}`);
  },
  async getActiveParseResult(knowledgeItemId: string) {
    return request<ActiveParseResultEnvelope>(`/api/items/${knowledgeItemId}/parse-result`);
  },
  async updateResult(
    snapshotId: string,
    payload: Pick<ResultDetail, "final_category" | "final_tags">
  ) {
    return request<ResultDetail>(`/api/results/${snapshotId}`, {
      method: "PATCH",
      body: JSON.stringify(payload)
    });
  },
  async submitFeedback(snapshotId: string, feedbackValue: FeedbackValue) {
    return request<{ saved: boolean }>(`/api/results/${snapshotId}/feedback`, {
      method: "POST",
      body: JSON.stringify({ feedback_value: feedbackValue })
    });
  }
};

