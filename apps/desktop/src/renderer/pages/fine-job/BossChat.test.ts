// @vitest-environment jsdom

import { beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, shallowMount } from "@vue/test-utils";

import BossChat from "./BossChat.vue";


const mocks = vi.hoisted(() => ({
  store: {
    attentionFilter: "",
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
  push: vi.fn()
}));

vi.mock("vue-router", () => ({
  useRoute: () => ({ query: {} }),
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
});
