// @vitest-environment jsdom

import { beforeEach, describe, expect, it, vi } from "vitest";
import { config, flushPromises, shallowMount } from "@vue/test-utils";

import BossCaptureHistory from "./BossCaptureHistory.vue";


const mocks = vi.hoisted(() => ({
  routeQuery: { history_id: "job-1" } as Record<string, string>,
  getJob: vi.fn(),
  getJourney: vi.fn(),
  loadHistory: vi.fn(),
  stopDetailPolling: vi.fn(),
  analyzeProgress: vi.fn(),
  generateReply: vi.fn(),
  push: vi.fn()
}));

vi.mock("vue-router", () => ({
  useRoute: () => ({ query: mocks.routeQuery }),
  useRouter: () => ({ push: mocks.push })
}));
vi.mock("@/services/api", () => ({
  ApiError: class extends Error {},
  api: {
    getFineJobBossCaptureHistoryJob: mocks.getJob,
    getFineJobJobJourney: mocks.getJourney,
    analyzeFineJobChatProgress: mocks.analyzeProgress,
    generateFineJobChatReply: mocks.generateReply
  }
}));
vi.mock("element-plus", () => ({
  ElMessage: { error: vi.fn(), success: vi.fn(), warning: vi.fn() },
  ElMessageBox: { confirm: vi.fn() }
}));
vi.mock("@/stores/fineJobBossCapture", () => ({
  useFineJobBossCaptureStore: () => ({
    status: null,
    cities: [],
    loadCities: vi.fn(),
    loadStatus: vi.fn()
  })
}));
vi.mock("@/stores/fineJobBossExecutor", () => ({
  useFineJobBossExecutorStore: () => ({ openingJobId: null, error: null, openJob: vi.fn() })
}));
vi.mock("@/stores/fineJobBossHistory", () => ({
  useFineJobBossHistoryStore: () => ({
    items: [],
    total: 0,
    page: 1,
    pageSize: 20,
    loading: false,
    error: null,
    detailTask: null,
    deliveryJobId: null,
    detailJobId: null,
    load: mocks.loadHistory,
    stopDetailPolling: mocks.stopDetailPolling,
    clearDetailTask: vi.fn(),
    captureDetails: vi.fn(),
    evaluateDelivery: vi.fn()
  })
}));
vi.mock("@/stores/fineJobStrategies", () => ({
  useFineJobStrategiesStore: () => ({
    filters: [],
    recommendations: [],
    load: vi.fn()
  })
}));

const job = {
  id: "job-1",
  title: "Python 开发",
  boss_name: "示例科技",
  company_scale: "20-99人",
  location: "广州",
  salary: "20-30K",
  collect_count: 1,
  detail_status: "completed",
  application_status: "pending_application",
  first_collected_at: "2026-09-05T09:00:00Z",
  last_collected_at: "2026-09-05T10:00:00Z",
  search_keyword: "Python"
};

describe("BossCaptureHistory 求职链路", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    config.global.renderStubDefaultSlot = true;
    mocks.routeQuery.history_id = "job-1";
    mocks.getJob.mockResolvedValue(job);
    mocks.analyzeProgress.mockResolvedValue({ reply_task_created: true });
    mocks.generateReply.mockResolvedValue({ reply_task: { id: "reply-1" } });
  });

  it("显示统一求职进展、执行证据与状态校正", async () => {
    mocks.getJourney.mockResolvedValue({
      job_id: "job-1",
      legacy_application: { id: "application-1", status: "pending_application", source: "boss_action", applied_at: "", updated_at: "" },
      pipeline: {
        job_id: "job-1",
        stage: "greeted",
        stage_source: "executor",
        stage_event_id: "event-1",
        stage_updated_at: "2026-09-05T10:00:00Z",
        waiting_on: "recruiter",
        waiting_since_at: "2026-09-05T10:00:00Z",
        contact_origin: "finejob_auto",
        rejection_reason_source: "unknown",
        rejection_reason_category: "unknown",
        rejection_reason_summary: "",
        projection_version: 2,
        created_at: "2026-09-05T10:00:00Z",
        updated_at: "2026-09-05T10:00:00Z"
      },
      progress: {
        job_id: "job-1",
        session_id: "session-1",
        stage: "greeted",
        stage_updated_at: "2026-09-05T10:00:00Z",
        waiting_on: "recruiter",
        waiting_since_at: "2026-09-05T10:00:00Z",
        contact_origin: "finejob_auto",
        latest_activity: null,
        followup: { decision: "wait", reason_code: "recruiter_owes_reply", reason_summary: "当前等待招聘方回复", recommended_at: null, recommended_action: "wait_for_recruiter", draft_message: "", draft_task_id: null },
        outcome: { status: "ongoing", rejection_reason_source: "unknown", rejection_reason_category: "unknown", rejection_reason_summary: "" },
        primary_action: null,
        analysis_updated_at: "2026-09-05T10:00:00Z"
      },
      activities: [{
        id: "event-1",
        job_id: "job-1",
        event_type: "greeting_sent",
        occurred_at: "2026-09-05T10:00:00Z",
        source: "executor",
        source_ref_type: "automation_action",
        source_ref_id: "action-1",
        confidence: 1,
        evidence_level: "direct",
        payload: {},
        created_at: "2026-09-05T10:00:00Z"
      }],
      executions: [{
        action_ref_type: "chat_send_action",
        action_ref_id: "send-1",
        action_type: "BOSS_CHAT_SEND",
        dedupe_identity: "reply-1",
        raw_status: "unknown",
        canonical_status: "succeeded",
        canonical_reason: "observed matching outbound message",
        status_code: "dispatch_result_timeout",
        error_message: "",
        executor_id: "executor-1",
        leader_tab_id: "tab-1",
        execution_epoch: 1,
        attempt_count: 1,
        created_at: "2026-09-05T10:00:00Z",
        dispatch_started_at: "2026-09-05T10:00:00Z",
        evidence: [{
          id: "evidence-1",
          action_ref_type: "chat_send_action",
          action_ref_id: "send-1",
          evidence_type: "outbound_message_observed",
          source: "chat",
          source_ref_type: "chat_message",
          source_ref_id: "message-1",
          observed_at: "2026-09-05T10:01:00Z",
          confidence: 1,
          evidence_level: "direct",
          payload: {},
          created_at: "2026-09-05T10:01:00Z"
        }],
        reconciliations: [{
          id: "reconciliation-1",
          previous_status: "unknown",
          new_status: "succeeded",
          reconciled_at: "2026-09-05T10:01:00Z",
          reconciliation_reason: "observed matching outbound message",
          evidence_id: "evidence-1",
          evidence_level: "direct"
        }]
      }]
    });

    const wrapper = shallowMount(BossCaptureHistory, {
      global: {
        directives: { loading: () => undefined },
        stubs: { "el-table-column": { template: "<div />" } }
      }
    });
    await flushPromises();

    expect(wrapper.text()).toContain("当前阶段");
    expect(wrapper.text()).toContain("已打招呼");
    expect(wrapper.text()).toContain("等招聘方回复");
    expect(wrapper.text()).toContain("FineJob 自动打招呼");
    expect(wrapper.text()).toContain("outbound_message_observed");
    expect(wrapper.text()).toContain("unknown → succeeded");
  });

  it("没有新链路数据时显示 empty state", async () => {
    mocks.getJourney.mockResolvedValue({
      job_id: "job-1",
      pipeline: null,
      legacy_application: null,
      activities: [],
      executions: []
    });
    const wrapper = shallowMount(BossCaptureHistory, {
      global: {
        directives: { loading: () => undefined },
        stubs: { "el-table-column": { template: "<div />" } }
      }
    });
    await flushPromises();

    expect(wrapper.text()).toContain("暂无新链路数据");
  });

  it("拒绝原因未知时可分析原因并生成询问草稿", async () => {
    mocks.getJourney.mockResolvedValue({
      job_id: "job-1",
      pipeline: { stage: "rejected" },
      legacy_application: { status: "rejected" },
      progress: {
        job_id: "job-1",
        session_id: "session-1",
        stage: "rejected",
        stage_updated_at: "2026-09-05T10:00:00Z",
        waiting_on: "none",
        contact_origin: "finejob_auto",
        followup: { decision: "follow", reason_code: "rejected_no_reason", reason_summary: "尚未说明原因", recommended_action: "ask_rejection_reason", draft_message: "", draft_task_id: null },
        outcome: { status: "rejected", rejection_reason_source: "unknown", rejection_reason_category: "unknown", rejection_reason_summary: "" },
        primary_action: { type: "ask_rejection_reason", label: "询问拒绝原因" }
      },
      activities: [],
      executions: []
    });
    const wrapper = shallowMount(BossCaptureHistory, {
      global: {
        directives: { loading: () => undefined },
        stubs: { "el-table-column": { template: "<div />" } }
      }
    });
    await flushPromises();

    expect(wrapper.text()).toContain("分析拒绝原因");
    expect(wrapper.text()).toContain("生成原因询问");
    const buttons = wrapper.findAll("el-button");
    await buttons.find((button) => button.text() === "分析拒绝原因")?.trigger("click");
    await flushPromises();
    expect(mocks.analyzeProgress).toHaveBeenCalledWith("session-1");

    await buttons.find((button) => button.text() === "生成原因询问")?.trigger("click");
    await flushPromises();
    expect(mocks.generateReply).toHaveBeenCalledWith(
      "session-1", "", false, "ask_rejection_reason"
    );
    expect(mocks.push).toHaveBeenCalledWith({
      name: "fine-job-chat",
      query: { session_id: "session-1" }
    });
  });
});
