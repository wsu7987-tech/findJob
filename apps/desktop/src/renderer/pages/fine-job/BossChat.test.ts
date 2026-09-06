// @vitest-environment jsdom

import { beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, shallowMount } from "@vue/test-utils";

import BossChat from "./BossChat.vue";


const mocks = vi.hoisted(() => ({
  store: {
    attentionFilter: "",
    waitingOnFilter: "",
    batchProgress: null,
    batchSize: 0,
    batchSummary: null,
    currentTask: null,
    detail: null as Record<string, unknown> | null,
    error: null,
    loading: false,
    mutating: false,
    nextOffset: null,
    runtime: null,
    searchQuery: "",
    selectedSessionId: "session-1",
    sessions: [],
    analyzeProgress: vi.fn(),
    cancel: vi.fn(),
    checkNow: vi.fn(),
    confirm: vi.fn(),
    generate: vi.fn(),
    load: vi.fn().mockResolvedValue(undefined),
    loadDetail: vi.fn(),
    loadList: vi.fn(),
    loadMore: vi.fn(),
    loadMoreHistory: vi.fn(),
    refreshFriendList: vi.fn(),
    refreshHistory: vi.fn(),
    rejectJob: vi.fn(),
    startBatchUpdate: vi.fn(),
    stopBatchPolling: vi.fn(),
    updateJob: vi.fn(),
    updateRuntime: vi.fn()
  },
  routeQuery: {} as Record<string, string>,
  push: vi.fn()
}));

vi.mock("vue-router", () => ({
  useRoute: () => ({ query: mocks.routeQuery }),
  useRouter: () => ({ push: mocks.push })
}));
vi.mock("@/stores/fineJobBossChat", () => ({
  useFineJobBossChatStore: () => mocks.store
}));
vi.mock("element-plus", () => ({
  ElMessage: { error: vi.fn(), success: vi.fn(), warning: vi.fn() },
  ElMessageBox: { confirm: vi.fn() }
}));

describe("BossChat 进展操作", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.routeQuery = {};
    mocks.store.attentionFilter = "";
    mocks.store.waitingOnFilter = "";
    mocks.store.detail = {
      session: {
        id: "session-1",
        status: "active",
        job_context_state: "linked",
        job_id: "job-1",
        progress: {
          stage: "rejected",
          waiting_on: "none",
          contact_origin: "finejob_auto",
          followup: { reason_summary: "尚未说明具体原因" },
          outcome: {
            status: "rejected",
            rejection_reason_source: "recruiter_explicit",
            rejection_reason_category: "fit",
            rejection_reason_summary: "暂时不考虑"
          },
          primary_action: { type: "ask_rejection_reason", label: "询问拒绝原因" }
        }
      },
      messages: [],
      reply_tasks: [],
      send_actions: []
    };
  });

  it("拒绝后隐藏人工标记并保留分析与生成入口", async () => {
    const wrapper = shallowMount(BossChat);
    await flushPromises();

    const buttonLabels = wrapper.findAll("el-button").map((button) => button.text());
    expect(buttonLabels).not.toContain("已被拒绝");
    expect(buttonLabels).toContain("分析拒绝原因");
    expect(buttonLabels).toContain("询问拒绝原因");
    expect(buttonLabels).toContain("生成消息");
  });

  it("读取求职分析跳转携带的等待与处理筛选", async () => {
    mocks.routeQuery = {
      waiting_on: "recruiter",
      attention: "needs_followup"
    };

    shallowMount(BossChat);
    await flushPromises();

    expect(mocks.store.waitingOnFilter).toBe("recruiter");
    expect(mocks.store.attentionFilter).toBe("needs_followup");
    expect(mocks.store.load).toHaveBeenCalled();
  });

  it("review_draft 跳转只打开当前 session 中仍待审核的草稿", async () => {
    mocks.routeQuery = {
      session_id: "session-1",
      reply_task_id: "task-1"
    };
    mocks.store.detail = {
      session: {
        id: "session-1",
        status: "active",
        job_context_state: "linked",
        job_id: "job-1",
        progress: null
      },
      messages: [],
      reply_tasks: [{
        id: "task-1",
        session_id: "session-1",
        trigger_source: "manual",
        action_kind: "reply",
        status: "awaiting_review",
        based_on_message_id: "message-1",
        based_on_session_version: 2,
        context: {},
        draft_text: "待审核草稿",
        final_text: "待审核草稿",
        generation_model: "test",
        created_at: "2026-09-06T00:00:00Z",
        updated_at: "2026-09-06T00:00:00Z"
      }],
      send_actions: []
    };
    mocks.store.currentTask = null;

    const wrapper = shallowMount(BossChat);
    await flushPromises();

    expect((wrapper.vm as unknown as { task: { id: string } }).task.id).toBe("task-1");
    expect(mocks.store.loadDetail).toHaveBeenCalledWith("session-1");
  });

  it("从今日行动进入后把当前触发标识交给单条草稿生成", async () => {
    mocks.routeQuery = {
      session_id: "session-1",
      action_kind: "reply",
      action_key: "reply:session-1:message-1"
    };
    mocks.store.generate.mockResolvedValue(undefined);
    const wrapper = shallowMount(BossChat);
    await flushPromises();

    await (wrapper.vm as unknown as {
      generate: (regenerate: boolean, actionKind: "reply") => Promise<void>;
    }).generate(false, "reply");

    expect(mocks.store.generate).toHaveBeenCalledWith(
      "",
      false,
      "reply",
      "reply:session-1:message-1"
    );
  });
});
