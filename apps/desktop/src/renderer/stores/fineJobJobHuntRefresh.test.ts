// @vitest-environment jsdom

import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";
import { flushPromises } from "@vue/test-utils";

import type { FineJobJobHuntRefreshRun } from "@/types";


const apiMocks = vi.hoisted(() => ({
  context: vi.fn(),
  listRuns: vi.fn(),
  getRun: vi.fn(),
  getScope: vi.fn(),
  discoverScope: vi.fn(),
  createRun: vi.fn(),
  attach: vi.fn(),
  markSubmitted: vi.fn(),
  cancelRun: vi.fn()
}));

vi.mock("@/services/api", () => ({
  ApiError: class extends Error {},
  NetworkError: class extends Error {},
  api: {
    getFineJobJobHuntRefreshContext: apiMocks.context,
    listFineJobJobHuntRefreshRuns: apiMocks.listRuns,
    getFineJobJobHuntRefreshRun: apiMocks.getRun,
    getFineJobJobHuntRefreshScope: apiMocks.getScope,
    discoverFineJobJobHuntRefreshScope: apiMocks.discoverScope,
    createFineJobJobHuntRefreshRun: apiMocks.createRun,
    attachFineJobJobHuntRefreshCodexSession: apiMocks.attach,
    markFineJobJobHuntRefreshPromptSubmitted: apiMocks.markSubmitted,
    cancelFineJobJobHuntRefreshRun: apiMocks.cancelRun
  }
}));

import { useFineJobJobHuntRefreshStore } from "./fineJobJobHuntRefresh";

const scope = {
  id: "refresh-scope-1",
  selected_since_time: "2026-09-04T00:00:00Z",
  requested_source_mode: "auto" as const,
  scope_source: "local" as const,
  account_uid: "candidate",
  source_url: "test",
  friend_list_synced_at: "2026-09-05T00:00:00Z",
  chat_list_synced_at: "2026-09-05T00:00:00Z",
  scope_generated_at: "2026-09-05T00:00:00Z",
  latest_local_message_at: null,
  session_ids_in_scope: [],
  session_ids_to_sync: [],
  new_session_ids: [],
  related_jobs: [],
  related_job_ids: [],
  encrypt_job_ids: [],
  jobs_to_collect: [],
  jobs_missing_jd: [],
  jobs_missing_evaluation: [],
  unresolved_session_ids: [],
  counts: {
    refreshed_sessions: 0,
    sessions_in_scope: 0,
    sessions_to_sync: 0,
    new_sessions_to_sync: 0,
    related_jobs: 0,
    chat_update_jobs: 0,
    extra_jobs: 0,
    jobs_to_update: 0,
    jobs_to_collect: 0,
    jobs_missing_jd: 0,
    jobs_missing_evaluation: 0,
    unresolved_relations: 0
  },
  friend_list_result: {
    account_uid: "candidate", count: 0, created_count: 0, changed_count: 0,
    source_url: "test", synced_at: "2026-09-05T00:00:00Z"
  },
  created_at: "2026-09-05T00:00:00Z"
};

const run = (status: FineJobJobHuntRefreshRun["status"]): FineJobJobHuntRefreshRun => ({
  id: "refresh-run-1",
  scope_id: scope.id,
  scope_generated_at: scope.scope_generated_at,
  status,
  selected_since_time: "2026-09-04T00:00:00Z",
  latest_local_message_at: null,
  workflow_options: {
    refresh_chat_list: true,
    refresh_chat_messages: true,
    refresh_related_jobs: true,
    analyze_conversations: false,
    generate_missing_suggestions: false
  },
  estimated_sessions: 2,
  estimated_update_sessions: 1,
  estimated_jobs: 2,
  estimated_refresh_jobs: 1,
  estimated_missing_jd: 1,
  estimated_missing_suggestions: 2,
  processed_sessions: 0,
  processed_jobs: 0,
  failed_sessions: 0,
  failed_jobs: 0,
  chat_list_status: "succeeded",
  chat_list_retryable: false,
  current_step: "waiting_codex",
  trigger_source: "page",
  summary: {},
  created_at: "2026-09-05T00:00:00Z",
  updated_at: "2026-09-05T00:00:00Z",
  items: [],
  progress: {
    chat_list: { status: "succeeded" },
    chat_messages: { total: 0, completed: 0, succeeded: 0, failed: 0 },
    related_jobs: { total: 0, completed: 0, succeeded: 0, failed: 0 }
  },
  resume_available: status === "pending" || status === "running",
  scope
});

describe("fineJobJobHuntRefresh store", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.useFakeTimers();
    Object.values(apiMocks).forEach((mock) => mock.mockReset());
    apiMocks.context.mockResolvedValue({
      timezone: "Asia/Shanghai",
      latest_local_message_at: null,
      last_successful_completed_at: null,
      default_since_time: "2026-09-04T00:00:00Z"
    });
  });

  afterEach(() => vi.useRealTimers());

  it("仅在 pending/running 状态每两秒读取进度，终态后立即停止", async () => {
    apiMocks.listRuns.mockResolvedValue({ runs: [run("pending")] });
    apiMocks.getRun.mockResolvedValue(run("completed_with_errors"));
    const store = useFineJobJobHuntRefreshStore();

    await store.load();
    await vi.advanceTimersByTimeAsync(2_000);
    await flushPromises();
    expect(apiMocks.getRun).toHaveBeenCalledTimes(1);
    expect(apiMocks.context).toHaveBeenCalledTimes(2);

    await vi.advanceTimersByTimeAsync(4_000);
    await flushPromises();
    expect(apiMocks.getRun).toHaveBeenCalledTimes(1);
  });

  it("cancelled Run 不启动进度读取", async () => {
    apiMocks.listRuns.mockResolvedValue({ runs: [run("cancelled")] });
    const store = useFineJobJobHuntRefreshStore();

    await store.load();
    await vi.advanceTimersByTimeAsync(4_000);
    await flushPromises();

    expect(apiMocks.getRun).not.toHaveBeenCalled();
  });

  it("Scope Discovery 与 Run 创建使用同一个持久化 scope_id", async () => {
    apiMocks.listRuns.mockResolvedValue({ runs: [] });
    apiMocks.discoverScope.mockResolvedValue(scope);
    apiMocks.createRun.mockResolvedValue(run("pending"));
    const store = useFineJobJobHuntRefreshStore();

    await store.load();
    await store.discoverScope();
    await store.createRun();

    expect(apiMocks.discoverScope).toHaveBeenCalledWith(
      "2026-09-04T00:00:00Z",
      "auto"
    );
    expect(apiMocks.context).toHaveBeenCalledTimes(2);
    expect(apiMocks.createRun).toHaveBeenCalledWith(expect.objectContaining({
      scope_id: "refresh-scope-1"
    }));
    store.stopProgressReading();
  });

  it("页面重新进入时恢复最新未使用的持久化 Scope", async () => {
    apiMocks.context.mockResolvedValue({
      timezone: "Asia/Shanghai",
      latest_local_message_at: null,
      last_successful_completed_at: null,
      default_since_time: "2026-09-05T00:00:00Z",
      latest_unconsumed_scope_id: scope.id
    });
    apiMocks.listRuns.mockResolvedValue({ runs: [] });
    apiMocks.getScope.mockResolvedValue(scope);
    const store = useFineJobJobHuntRefreshStore();

    await store.load();

    expect(apiMocks.getScope).toHaveBeenCalledWith(scope.id);
    expect(store.scope?.id).toBe(scope.id);
    expect(store.selectedSinceTime).toBe(scope.selected_since_time);
  });
});
