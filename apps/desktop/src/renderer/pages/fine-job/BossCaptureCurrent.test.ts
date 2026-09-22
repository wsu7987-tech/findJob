// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, shallowMount, type VueWrapper } from "@vue/test-utils";

import BossCapture from "./BossCapture.vue";

const mocks = vi.hoisted(() => ({
  getCurrent: vi.fn(),
  getCapture: vi.fn(),
  createCapture: vi.fn(),
  getRun: vi.fn(),
  getContext: vi.fn(),
  getAnalysisItems: vi.fn(),
  getAnalysisSnapshot: vi.fn(),
  triggerSmartHandoff: vi.fn(),
  setRun: vi.fn(),
  pauseWorkflow: vi.fn(),
  startPolling: vi.fn(),
  stopPolling: vi.fn(),
  eventSources: [] as Array<{
    url: string;
    emit: (data: string) => void;
    close: ReturnType<typeof vi.fn>;
  }>
}));

class MockEventSource {
  onmessage: ((event: { data: string }) => void) | null = null;
  close = vi.fn();

  constructor(readonly url: string) {
    mocks.eventSources.push(this);
  }

  emit(data: string) {
    this.onmessage?.({ data });
  }
}

vi.mock("vue-router", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("element-plus", () => ({
  ElMessage: { success: vi.fn(), warning: vi.fn(), error: vi.fn() },
  ElMessageBox: { alert: vi.fn() }
}));
vi.mock("@/services/api", () => ({
  ApiError: class extends Error {},
  api: {
    getCurrentFineJobSmartCapture: mocks.getCurrent,
    getFineJobSmartCapture: mocks.getCapture,
    createFineJobSmartCapture: mocks.createCapture,
    getFineJobWorkflowRun: mocks.getRun,
    getFineJobSmartCaptureContextSnapshot: mocks.getContext,
    getFineJobWorkflowContextSnapshot: mocks.getContext,
    listFineJobSmartCaptureAnalysisItems: mocks.getAnalysisItems,
    getFineJobSmartCaptureAnalysisSnapshot: mocks.getAnalysisSnapshot,
    getFineJobActiveCollectionTask: vi.fn().mockResolvedValue({ active_task: null }),
    getConfig: vi.fn().mockResolvedValue({}),
    listCodexModels: vi.fn().mockResolvedValue({ models: [] })
  },
  getBackendOrigin: vi.fn().mockResolvedValue("http://127.0.0.1:8000")
}));
vi.mock("@/stores/fineJobBossCapture", () => ({
  useFineJobBossCaptureStore: () => ({
    loadStatus: vi.fn(), loadCities: vi.fn(), resumePolling: vi.fn(), stopPolling: vi.fn(),
    task: null, cities: [], clearTask: vi.fn()
  })
}));
vi.mock("@/stores/fineJobBossExecutor", () => ({ useFineJobBossExecutorStore: () => ({}) }));
vi.mock("@/stores/fineJobCodex", () => ({ useFineJobCodexStore: () => ({ status: "idle", sessionRef: null }) }));
vi.mock("@/stores/fineJobPlatformSessions", () => ({ useFineJobPlatformSessionsStore: () => ({ load: vi.fn() }) }));
vi.mock("@/stores/fineJobStrategies", () => ({
  useFineJobStrategiesStore: () => ({ load: vi.fn(), filters: [], recommendations: [] })
}));
vi.mock("@/stores/fineJobWorkflowRun", () => ({
  useFineJobWorkflowRunStore: () => ({
    currentRun: null, loading: false, advancing: false, pollingActive: false,
    setRun: mocks.setRun, startPolling: mocks.startPolling, stopPolling: mocks.stopPolling,
    pause: mocks.pauseWorkflow, resume: vi.fn(), cancel: vi.fn(), refresh: vi.fn(), advance: vi.fn()
  })
}));
vi.mock("@/services/workflowCodexHandoff", () => ({
  resubmitSmartCaptureCodexSubmit: vi.fn(),
  retrySmartCaptureCodexHandoff: vi.fn(),
  triggerSmartCaptureCodexHandoff: mocks.triggerSmartHandoff
}));

const capture = (workflowRunId: string | null) => ({
  smart_capture_id: "capture-current", source: "boss_capture", workflow_run_id: workflowRunId,
  status: "running", stage: "capturing", waiting_reason: "", control_cause: "",
  state_version: 7, capabilities: { start: false, pause: true, resume: false, retry: false, stop: true },
  search_config: {}, execution_config: {}, progress: {}, result_summary: {}, message: "采集中",
  created_at: "2026-09-23T00:00:00Z", updated_at: "2026-09-23T00:00:00Z", batches: [], jobs: []
});

const workflowRun = (id: string) => ({
  workflow_run_id: id, workflow_type: "deep_job_search", status: "running", state_version: 3,
  progress: {}, telemetry: {}, tasks: [], children: [], next_action_reason: "等待子任务"
});

const elementComponentNames = [
  "el-alert", "el-button", "el-checkbox", "el-checkbox-group", "el-collapse", "el-collapse-item",
  "el-collapse-transition", "el-descriptions", "el-descriptions-item", "el-dialog", "el-divider", "el-drawer",
  "el-empty", "el-form", "el-form-item", "el-input", "el-input-number", "el-link", "el-option", "el-progress",
  "el-radio", "el-radio-group", "el-select", "el-switch", "el-tab-pane", "el-tabs", "el-tag", "el-tooltip"
];
const elementStubs = Object.fromEntries(elementComponentNames.map((name) => [name, { template: "<div><slot /></div>" }]));

const mountCapture = () => shallowMount(BossCapture, {
  global: {
    stubs: {
      ...elementStubs,
      "el-table": { template: "<div />" },
      "el-table-column": { template: "<div />" }
    }
  }
});

describe("BossCapture current 与历史状态隔离", () => {
  let wrapper: VueWrapper | null = null;

  beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
    mocks.eventSources.length = 0;
    vi.stubGlobal("EventSource", MockEventSource);
    mocks.getContext.mockResolvedValue({ channel: "deep_job_search", status: "ready", sections: [] });
    mocks.getAnalysisItems.mockResolvedValue({ items: [], analysis_batch_id: "batch-1" });
    mocks.getAnalysisSnapshot.mockResolvedValue({
      smart_capture_id: "capture-current",
      analysis_batch_id: "batch-1",
      items: []
    });
    mocks.triggerSmartHandoff.mockResolvedValue({ status: "submitted", message: "已提交" });
  });

  afterEach(() => {
    wrapper?.unmount();
    wrapper = null;
    mocks.eventSources.length = 0;
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("以 current Smart Capture 关联 A，读取历史 B 不替换关联父镜像", async () => {
    mocks.getCurrent.mockResolvedValue({ smart_capture: capture("workflow-A") });
    mocks.getCapture.mockResolvedValue(capture("workflow-A"));
    mocks.getRun.mockImplementation((id: string) => Promise.resolve(workflowRun(id)));
    const mounted = mountCapture();
    wrapper = mounted;
    await flushPromises();

    expect(mocks.getCurrent).toHaveBeenCalledTimes(1);
    expect(mocks.getCapture).toHaveBeenCalledWith("capture-current");
    expect(mocks.getRun).toHaveBeenCalledWith("workflow-A");
    expect(mocks.setRun).toHaveBeenCalledWith(expect.objectContaining({ workflow_run_id: "workflow-A" }));

    (mounted.vm as unknown as { smartRunLookupId: string }).smartRunLookupId = "workflow-B";
    await (mounted.vm as unknown as { restoreSmartWorkflowRun: () => Promise<void> }).restoreSmartWorkflowRun();

    expect(mocks.getRun).toHaveBeenCalledWith("workflow-B");
    expect(mocks.setRun).not.toHaveBeenCalledWith(expect.objectContaining({ workflow_run_id: "workflow-B" }));
    expect(mocks.getCapture).toHaveBeenLastCalledWith("capture-current");

    await (mounted.vm as unknown as { pauseSmartWorkflow: () => Promise<void> }).pauseSmartWorkflow();
    expect(mocks.pauseWorkflow).toHaveBeenCalledTimes(1);
    expect(mocks.getRun.mock.calls.filter(([id]) => id === "workflow-B")).toHaveLength(1);

    await (mounted.vm as unknown as {
      openSmartWorkflowCodex: (action: "submit") => Promise<void>;
    }).openSmartWorkflowCodex("submit");
    expect(mocks.getAnalysisSnapshot).toHaveBeenCalledWith("capture-current");
    expect(mocks.triggerSmartHandoff).toHaveBeenCalledTimes(1);
  });

  it("independent current 不查询 latest Workflow，也不建立关联父镜像", async () => {
    mocks.getCurrent.mockResolvedValue({ smart_capture: capture(null) });
    mocks.getCapture.mockResolvedValue(capture(null));
    const mounted = mountCapture();
    wrapper = mounted;
    await flushPromises();

    expect(mocks.getCurrent).toHaveBeenCalledTimes(1);
    expect(mocks.getCapture).toHaveBeenCalledWith("capture-current");
    expect(mocks.getRun).not.toHaveBeenCalled();
    expect(mocks.setRun).toHaveBeenCalledWith(null);
    expect(mounted.text()).toContain("待分析岗位队列");
    expect((mounted.vm as unknown as { smartManualAnalysisAvailable: boolean }).smartManualAnalysisAvailable).toBe(false);
  });

  it("independent completed 使用 Smart Capture Manual Analysis，不开放 custom 岗位操作", async () => {
    const completedCapture = {
      ...capture(null),
      status: "completed",
      state_version: 8,
      jobs: [{
        id: "history-job-1",
        history_record_id: "history-job-1",
        job_id: "job-1",
        title: "后端工程师",
        company: "示例公司",
        detail_status: "completed"
      }]
    };
    mocks.getCurrent.mockResolvedValue({
      smart_capture: completedCapture
    });
    mocks.getCapture.mockResolvedValue(completedCapture);
    const mounted = mountCapture();
    wrapper = mounted;
    await flushPromises();

    expect((mounted.vm as unknown as { smartManualAnalysisAvailable: boolean }).smartManualAnalysisAvailable).toBe(true);
    expect(mounted.text()).toContain("交给 Codex 批量生成建议");
    expect(mounted.text()).not.toContain("采集选中的");
    expect(mounted.text()).not.toContain("AI 初筛详情岗位");

    (mounted.vm as unknown as { activeCaptureConditionTab: "smart" | "custom" }).activeCaptureConditionTab = "custom";
    await flushPromises();
    expect((mounted.vm as unknown as { displayingCurrentSmartCapture: boolean }).displayingCurrentSmartCapture).toBe(false);
  });

  it("创建后通过 current API 确认页面身份", async () => {
    mocks.getCurrent
      .mockResolvedValueOnce({ smart_capture: null })
      .mockResolvedValueOnce({ smart_capture: capture(null) });
    mocks.getCapture.mockResolvedValue(capture(null));
    mocks.createCapture.mockResolvedValue(capture(null));
    const mounted = mountCapture();
    wrapper = mounted;
    await flushPromises();

    const view = mounted.vm as unknown as {
      smartFilterStrategyId: string;
      smartSelectedKeywords: string[];
      smartSelectedCities: string[];
      startSmartCapture: () => Promise<void>;
    };
    view.smartFilterStrategyId = "filter-1";
    view.smartSelectedKeywords = ["Python"];
    view.smartSelectedCities = ["北京"];
    await view.startSmartCapture();

    expect(mocks.createCapture).toHaveBeenCalledTimes(1);
    expect(mocks.getCurrent).toHaveBeenCalledTimes(2);
    expect(mocks.getCapture).toHaveBeenCalledWith("capture-current");
  });

  it("同一 current 的旧 state_version 不覆盖已展示快照", async () => {
    mocks.getCurrent.mockResolvedValue({ smart_capture: capture(null) });
    mocks.getCapture
      .mockResolvedValueOnce(capture(null))
      .mockResolvedValueOnce({ ...capture(null), state_version: 6, status: "paused" });
    const mounted = mountCapture();
    wrapper = mounted;
    await flushPromises();

    await (mounted.vm as unknown as { refreshCurrentSmartCapture: () => Promise<void> }).refreshCurrentSmartCapture();
    const current = (mounted.vm as unknown as { currentSmartCapture: { state_version: number; status: string } }).currentSmartCapture;
    expect(current).toEqual(expect.objectContaining({ state_version: 7, status: "running" }));
  });

  it("通过 Smart Capture 详情 SSE 更新 current，页面不启动定时器", async () => {
    mocks.getCurrent.mockResolvedValue({ smart_capture: capture(null) });
    mocks.getCapture.mockResolvedValue(capture(null));
    const timerSpy = vi.spyOn(globalThis, "setInterval");
    try {
      const mounted = mountCapture();
      wrapper = mounted;
      await flushPromises();

      const detailSource = mocks.eventSources.find((source) => source.url.endsWith("/capture-current/events"));
      expect(mocks.eventSources.map((source) => source.url)).toEqual([
        "http://127.0.0.1:8000/api/fine-job/smart-captures/current/events",
        "http://127.0.0.1:8000/api/fine-job/smart-captures/capture-current/events"
      ]);
      expect(detailSource).toBeDefined();
      expect(timerSpy).not.toHaveBeenCalled();

      detailSource?.emit(JSON.stringify({ ...capture(null), state_version: 8, status: "paused" }));
      await flushPromises();

      expect((mounted.vm as unknown as { currentSmartCapture: { state_version: number; status: string } }).currentSmartCapture)
        .toEqual(expect.objectContaining({ state_version: 8, status: "paused" }));
    } finally {
      timerSpy.mockRestore();
    }
  });
});
