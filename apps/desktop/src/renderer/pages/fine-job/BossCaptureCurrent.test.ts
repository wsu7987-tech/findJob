// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, shallowMount, type VueWrapper } from "@vue/test-utils";

import BossCapture from "./BossCapture.vue";

const mocks = vi.hoisted(() => ({
  getCurrent: vi.fn(),
  getCapture: vi.fn(),
  createCapture: vi.fn(),
  startCapture: vi.fn(),
  retryCapture: vi.fn(),
  getRun: vi.fn(),
  getContext: vi.fn(),
  getAnalysisItems: vi.fn(),
  getAnalysisSnapshot: vi.fn(),
  triggerSmartHandoff: vi.fn(),
  setRun: vi.fn(),
  currentRun: null as unknown,
  pauseWorkflow: vi.fn(),
  resumeWorkflow: vi.fn(),
  cancelWorkflow: vi.fn(),
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
    startFineJobSmartCapture: mocks.startCapture,
    retryFineJobSmartCapture: mocks.retryCapture,
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
    get currentRun() { return mocks.currentRun; }, loading: false, advancing: false, pollingActive: false,
    setRun: mocks.setRun, startPolling: mocks.startPolling, stopPolling: mocks.stopPolling,
    pause: mocks.pauseWorkflow, resume: mocks.resumeWorkflow, cancel: mocks.cancelWorkflow, refresh: vi.fn(), advance: vi.fn()
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
  workflow_run_id: id, workflow_type: "deep_job_search", status: "running", control_state: "active", control_cause: "", state_version: 3,
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
    mocks.currentRun = null;
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
    mocks.getRun.mockImplementation((id: string) => Promise.resolve(workflowRun(id)));
    const mounted = mountCapture();
    wrapper = mounted;
    await flushPromises();

    expect(mocks.getCurrent).toHaveBeenCalledTimes(1);
    expect(mocks.getCapture).not.toHaveBeenCalled();
    expect(mocks.getRun).toHaveBeenCalledWith("workflow-A");
    expect(mocks.setRun).toHaveBeenCalledWith(expect.objectContaining({ workflow_run_id: "workflow-A" }));

    (mounted.vm as unknown as { smartRunLookupId: string }).smartRunLookupId = "workflow-B";
    await (mounted.vm as unknown as { restoreSmartWorkflowRun: () => Promise<void> }).restoreSmartWorkflowRun();

    expect(mocks.getRun).toHaveBeenCalledWith("workflow-B");
    expect(mocks.setRun).not.toHaveBeenCalledWith(expect.objectContaining({ workflow_run_id: "workflow-B" }));
    expect(mocks.getCapture).not.toHaveBeenCalled();

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
    const independentCapture = {
      ...capture(null),
      search_combinations: [{
        id: "combination-1", keyword: "Python", city: "东京", platform_filters_json: "{}",
        status: "running", sequence: 0, transition_action: "SWITCH_COMBINATION",
        transition_reason: "baseline", selected_axis: "", evidence_json: "{}", stop_reason: "",
        jobs_seen: 8, run_fresh_jobs: 5, historical_duplicates: 3, strategy_reject: 1,
        qualified_fresh_jobs: 4
      }],
      candidate_pool: [{ id: "candidate-1" }],
      prefetch: {
        prefetch_batch_id: "prefetch-1", source_analysis_batch_id: "analysis-1", status: "ready",
        target_count: 3, pending_count: 0, collecting_count: 0, ready_count: 3, failed_count: 0
      }
    };
    mocks.getCurrent.mockResolvedValue({ smart_capture: independentCapture });
    const mounted = mountCapture();
    wrapper = mounted;
    await flushPromises();

    expect(mocks.getCurrent).toHaveBeenCalledTimes(1);
    expect(mocks.getCapture).not.toHaveBeenCalled();
    expect(mocks.getRun).not.toHaveBeenCalled();
    expect(mocks.setRun).toHaveBeenCalledWith(null);
    expect(mounted.text()).toContain("待分析岗位队列");
    expect(mounted.text()).toContain("历史 / Context 检查工具");
    expect(mounted.text()).toContain("智能搜索策略");
    expect(mounted.text()).toContain("3 / 3 已准备");
    expect(mounted.text()).not.toContain("关联父任务镜像");
    expect(mounted.text()).not.toContain("立即推进");
    expect((mounted.vm as unknown as { smartManualAnalysisAvailable: boolean }).smartManualAnalysisAvailable).toBe(false);
  });

  it("pending 与 interrupted 保留 Smart Capture 语义化动作", async () => {
    const pendingCapture = {
      ...capture(null),
      status: "pending",
      stage: "pending",
      capabilities: { start: true, pause: false, resume: false, retry: false, stop: true }
    };
    mocks.getCurrent.mockResolvedValue({ smart_capture: pendingCapture });
    mocks.getCapture.mockResolvedValue(pendingCapture);
    mocks.startCapture.mockResolvedValue({ ...capture(null), state_version: 8 });
    const mounted = mountCapture();
    wrapper = mounted;
    await flushPromises();

    expect(mounted.text()).toContain("启动当前采集");
    await (mounted.vm as unknown as { startCurrentSmartCapture: () => Promise<void> }).startCurrentSmartCapture();
    expect(mocks.startCapture).toHaveBeenCalledWith("capture-current");

    const interruptedCapture = {
      ...capture(null),
      status: "interrupted",
      stage: "capture_interrupted",
      state_version: 9,
      capabilities: { start: false, pause: false, resume: true, retry: true, stop: true }
    };
    await (mounted.vm as unknown as {
      applyCurrentSmartCapture: (capture: unknown) => Promise<void>;
    }).applyCurrentSmartCapture(interruptedCapture);
    mocks.retryCapture.mockResolvedValue({ ...interruptedCapture, state_version: 10, status: "running" });
    await flushPromises();

    expect(mounted.text()).toContain("继续采集");
    expect(mounted.text()).toContain("重试采集");
    await (mounted.vm as unknown as { retryCurrentSmartCapture: () => Promise<void> }).retryCurrentSmartCapture();
    expect(mocks.retryCapture).toHaveBeenCalledWith("capture-current");
  });

  it("linked pending 不绕过父编排直接启动 child", async () => {
    const linkedPendingCapture = {
      ...capture("workflow-A"),
      status: "pending",
      stage: "pending",
      capabilities: { start: true, pause: false, resume: false, retry: false, stop: true }
    };
    mocks.getCurrent.mockResolvedValue({ smart_capture: linkedPendingCapture });
    mocks.getRun.mockResolvedValue(workflowRun("workflow-A"));
    mocks.currentRun = workflowRun("workflow-A");
    const mounted = mountCapture();
    wrapper = mounted;
    await flushPromises();

    expect(mounted.text()).not.toContain("启动当前采集");
    await (mounted.vm as unknown as { startCurrentSmartCapture: () => Promise<void> }).startCurrentSmartCapture();
    expect(mocks.startCapture).not.toHaveBeenCalled();
  });

  it("linked current 显示父镜像，历史工具独立于父控制区域", async () => {
    mocks.getCurrent.mockResolvedValue({ smart_capture: capture("workflow-A") });
    mocks.getRun.mockResolvedValue(workflowRun("workflow-A"));
    mocks.currentRun = workflowRun("workflow-A");
    const mounted = mountCapture();
    wrapper = mounted;
    await flushPromises();

    expect(mounted.text()).toContain("关联父任务镜像");
    expect(mounted.text()).toContain("历史 / Context 检查工具");
    expect(mounted.text()).toContain("返回驾驶舱");
    expect(mounted.text()).not.toContain("立即推进");
  });

  it("父镜像控制按 control_state 显示，子任务中断时不误显示父 resume", async () => {
    const interruptedParent = {
      ...workflowRun("workflow-A"),
      status: "waiting_for_user",
      control_state: "waiting_child_interrupted",
      control_cause: "recovery",
      stop_reason: "capture_interrupted"
    };
    mocks.getCurrent.mockResolvedValue({ smart_capture: capture("workflow-A") });
    mocks.getRun.mockResolvedValue(interruptedParent);
    mocks.currentRun = interruptedParent;
    const mounted = mountCapture();
    wrapper = mounted;
    await flushPromises();

    expect(mounted.text()).not.toContain("暂停父任务");
    expect(mounted.text()).not.toContain("继续父任务");
    expect(mounted.text()).toContain("停止父任务");
  });

  it("历史 Context 切换类型后仍刷新 inspected Run，而不改变 current identity", async () => {
    mocks.getCurrent.mockResolvedValue({ smart_capture: null });
    mocks.getRun.mockResolvedValue(workflowRun("workflow-B"));
    const mounted = mountCapture();
    wrapper = mounted;
    await flushPromises();

    mocks.getContext.mockClear();
    const view = mounted.vm as unknown as {
      smartRunLookupId: string;
      smartContextChannel: string;
      restoreSmartWorkflowRun: () => Promise<void>;
      loadSelectedContextSnapshots: () => Promise<void>;
    };
    view.smartRunLookupId = "workflow-B";
    await view.restoreSmartWorkflowRun();
    expect(mocks.getContext).toHaveBeenCalledWith("workflow-B", "deep_job_search");

    view.smartContextChannel = "candidate_analysis";
    await view.loadSelectedContextSnapshots();
    expect(mocks.getContext).toHaveBeenCalledWith("workflow-B", "candidate_analysis");
    expect(mocks.getCapture).not.toHaveBeenCalled();
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
    const mounted = mountCapture();
    wrapper = mounted;
    await flushPromises();

    expect((mounted.vm as unknown as { smartManualAnalysisAvailable: boolean }).smartManualAnalysisAvailable).toBe(true);
    expect(mounted.text()).toContain("最近任务结果");
    expect(mounted.text()).toContain("采集控制已关闭");
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
    expect(mocks.getCapture).not.toHaveBeenCalled();
  });

  it("同一 current 的旧 state_version 不覆盖已展示快照", async () => {
    mocks.getCurrent
      .mockResolvedValueOnce({ smart_capture: capture(null) })
      .mockResolvedValueOnce({ smart_capture: { ...capture(null), state_version: 6, status: "paused" } });
    const mounted = mountCapture();
    wrapper = mounted;
    await flushPromises();

    await (mounted.vm as unknown as { refreshCurrentSmartCapture: () => Promise<void> }).refreshCurrentSmartCapture();
    const current = (mounted.vm as unknown as { currentSmartCapture: { state_version: number; status: string } }).currentSmartCapture;
    expect(current).toEqual(expect.objectContaining({ state_version: 7, status: "running" }));
  });

  it("通过 Smart Capture 详情 SSE 更新 current，页面不启动定时器", async () => {
    mocks.getCurrent.mockResolvedValue({ smart_capture: capture(null) });
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

      mocks.getContext.mockClear();
      mocks.getAnalysisItems.mockClear();
      detailSource?.emit(JSON.stringify({ ...capture(null), state_version: 8 }));
      await flushPromises();
      expect(mocks.getContext).not.toHaveBeenCalled();
      expect(mocks.getAnalysisItems).not.toHaveBeenCalled();

      detailSource?.emit(JSON.stringify({ ...capture(null), state_version: 9, status: "paused" }));
      await flushPromises();

      expect((mounted.vm as unknown as { currentSmartCapture: { state_version: number; status: string } }).currentSmartCapture)
        .toEqual(expect.objectContaining({ state_version: 9, status: "paused" }));
      expect(mocks.getContext).toHaveBeenCalledTimes(1);
      expect(mocks.getAnalysisItems).toHaveBeenCalledTimes(1);
    } finally {
      timerSpy.mockRestore();
    }
  });
});
