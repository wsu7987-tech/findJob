import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";

const profileApiMock = vi.hoisted(() => ({
  runAnalysis: vi.fn(),
  getAnalysisRun: vi.fn(),
  listAnalysisItems: vi.fn(),
  listProfiles: vi.fn(),
  listSources: vi.fn(),
  listFacts: vi.fn(),
  listQuestions: vi.fn(),
  listResumeVersions: vi.fn(),
  listCampaigns: vi.fn(),
  listResumeFamilies: vi.fn(),
  listIssues: vi.fn(),
  listQATemplates: vi.fn(),
  listResumeIssues: vi.fn(),
  listResumeStrategies: vi.fn(),
  listResumeSearchKeywords: vi.fn(),
  getLatestAnalysisRun: vi.fn(),
  autoApplyAnalysisFacts: vi.fn()
}));

vi.mock("@/services/api", () => ({
  ApiError: class ApiError extends Error {},
  NetworkError: class NetworkError extends Error {}
}));

vi.mock("@/services/profile-api", () => ({ profileApi: profileApiMock }));

import { useFineJobProfilesStore } from "./fineJobProfiles";

const versions = {
  sources_version: 1,
  facts_version: 1,
  questions_version: 1,
  answers_version: 1,
  strategy_version: 1,
  context_version: 1
};

const profile = { id: "profile-1", display_name: "默认档案", versions };
const pendingRun = {
  id: "run-1",
  profile_id: "profile-1",
  source_ids: ["source-1"],
  input_versions: versions,
  ai_model: "stub",
  prompt_version: "v1",
  status: "pending",
  quality: {},
  error_category: null,
  error_message: null,
  started_at: null,
  completed_at: null,
  created_at: "2026-08-29T00:00:00Z",
  updated_at: "2026-08-29T00:00:00Z"
};

describe("fineJobProfiles store", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    setActivePinia(createPinia());
    Object.values(profileApiMock).forEach((mock) => mock.mockReset());
    profileApiMock.runAnalysis.mockResolvedValue({ analysis_run: pendingRun });
    profileApiMock.getAnalysisRun.mockResolvedValue({
      analysis_run: { ...pendingRun, status: "needs_confirmation" }
    });
    profileApiMock.listAnalysisItems.mockResolvedValue({ items: [] });
    profileApiMock.listProfiles.mockResolvedValue({ profiles: [profile] });
    profileApiMock.listSources.mockResolvedValue({ sources: [] });
    profileApiMock.listFacts.mockResolvedValue({ facts: [], facts_version: 1 });
    profileApiMock.listQuestions.mockResolvedValue({ questions: [], questions_version: 1 });
    profileApiMock.listResumeVersions.mockResolvedValue({ resume_versions: [] });
    profileApiMock.listCampaigns.mockResolvedValue({ campaigns: [] });
    profileApiMock.listResumeFamilies.mockResolvedValue({ resume_families: [] });
    profileApiMock.listIssues.mockResolvedValue({ issues: [] });
    profileApiMock.listQATemplates.mockResolvedValue({ templates: [] });
    profileApiMock.listResumeIssues.mockResolvedValue({ issues: [] });
    profileApiMock.listResumeStrategies.mockResolvedValue({ strategies: [] });
    profileApiMock.listResumeSearchKeywords.mockResolvedValue({ keywords: [] });
    profileApiMock.getLatestAnalysisRun.mockResolvedValue({ analysis_run: { ...pendingRun, status: "needs_confirmation" } });
    profileApiMock.autoApplyAnalysisFacts.mockResolvedValue({ analysis_run: { ...pendingRun, status: "needs_confirmation" } });
  });

  afterEach(() => vi.useRealTimers());

  it("轮询异步分析任务并在等待确认后刷新资料", async () => {
    const store = useFineJobProfilesStore();
    store.selectedProfile = profile as never;

    const operation = store.analyzeSources(["source-1"]);
    await vi.advanceTimersByTimeAsync(600);
    await operation;

    expect(profileApiMock.runAnalysis).toHaveBeenCalledWith("profile-1", ["source-1"]);
    expect(profileApiMock.getAnalysisRun).toHaveBeenCalledWith("run-1");
    expect(store.analysisRun?.status).toBe("needs_confirmation");
    expect(store.analyzing).toBe(false);
  });

  it("显示分析失败的错误类别和具体原因", async () => {
    profileApiMock.getAnalysisRun.mockResolvedValue({
      analysis_run: {
        ...pendingRun,
        status: "failed",
        error_category: "CODEX_OUTPUT_SCHEMA_INVALID",
        error_message: "Codex rejected the output schema"
      }
    });
    const store = useFineJobProfilesStore();
    store.selectedProfile = profile as never;

    const operation = store.analyzeSources(["source-1"]);
    const rejection = expect(operation).rejects.toThrow("错误类别：Codex 输出 Schema 不兼容；原因：Codex rejected the output schema");
    await vi.advanceTimersByTimeAsync(600);
    await rejection;
    expect(store.error).toContain("Codex 输出 Schema 不兼容");
    expect(store.error).toContain("Codex rejected the output schema");
    expect(store.analyzing).toBe(false);
  });
});
