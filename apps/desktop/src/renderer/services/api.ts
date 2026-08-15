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
  FineJobActionLogListEnvelope,
  FineJobDeliveryCandidateListEnvelope,
  FineJobDeliveryRunCreateRequest,
  FineJobDeliveryRunEnvelope,
  FineJobDeliveryRunListEnvelope,
  FineJobPlatformSession,
  FineJobPlatformSessionEnvelope,
  FineJobPlatformLoginActionEnvelope,
  FineJobPlatformSessionListEnvelope,
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
import type { ProviderConnectivityCheckResponse } from "../types";
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

const request = async <T>(path: string, init?: RequestInit) => {
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
  async listFineJobDeliveryCandidates(runId: string) {
    return request<FineJobDeliveryCandidateListEnvelope>(
      `/api/fine-job/delivery-runs/${runId}/candidates`
    );
  },
  async listFineJobDeliveryRunLogs(runId: string) {
    return request<FineJobActionLogListEnvelope>(`/api/fine-job/delivery-runs/${runId}/logs`);
  },
  async listFineJobRecentActionLogs() {
    return request<FineJobActionLogListEnvelope>("/api/fine-job/delivery-runs/logs/recent");
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
  async listFineJobResumes() {
    return request<FineJobResumeListEnvelope>("/api/fine-job/resumes");
  },
  async getFineJobResume(resumeId: string) {
    return request<FineJobResumeEnvelope>(`/api/fine-job/resumes/${resumeId}`);
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

