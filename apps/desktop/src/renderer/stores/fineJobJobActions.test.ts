// @vitest-environment jsdom

import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";

const apiMocks = vi.hoisted(() => ({
  list: vi.fn(),
  snooze: vi.fn(),
  dismiss: vi.fn(),
  complete: vi.fn(),
  restore: vi.fn(),
  generateDrafts: vi.fn()
}));

vi.mock("@/services/api", () => ({
  ApiError: class extends Error {},
  NetworkError: class extends Error {},
  api: {
    listFineJobJobActions: apiMocks.list,
    snoozeFineJobJobAction: apiMocks.snooze,
    dismissFineJobJobAction: apiMocks.dismiss,
    completeFineJobJobAction: apiMocks.complete,
    restoreFineJobJobAction: apiMocks.restore,
    generateFineJobJobActionDrafts: apiMocks.generateDrafts
  }
}));

import { useFineJobJobActionsStore } from "./fineJobJobActions";

const summary = {
  urgent: 2,
  high: 3,
  normal: 4,
  low: 1,
  snoozed: 5
};

const actionItem = (overrides: Record<string, unknown> = {}) => ({
  action_key: "reply:session-1:message-1",
  job_id: "job-1",
  session_id: "session-1",
  action_type: "reply_recruiter",
  priority_tier: "high",
  title: "后端工程师",
  company_name: "示例公司",
  stage: "communicating",
  waiting_on: "candidate",
  waiting_since_at: "2026-09-05T00:00:00Z",
  due_at: null,
  overdue_seconds: 0,
  reason_code: "needs_reply",
  reason_summary: "招聘方发送了新消息",
  evidence: {
    trigger_type: "message",
    trigger_id: "message-1",
    message_ids: ["message-1"],
    activity_event_ids: [],
    attention_insight_id: null
  },
  reply_task: null,
  primary_action: {
    type: "open_chat",
    label: "生成回复",
    route_name: "fine-job-chat",
    query: { session_id: "session-1" },
    action_kind: "reply",
    reply_task_id: null
  },
  secondary_actions: ["snooze", "dismiss", "complete"],
  state: "active",
  snoozed_until: null,
  ...overrides
});

const activeResponse = (items = [actionItem()]) => ({
  summary,
  items,
  generated_at: "2026-09-06T00:00:00Z"
});

const snoozedResponse = (items = []) => ({
  summary: { urgent: 99, high: 99, normal: 99, low: 99, snoozed: 99 },
  items,
  generated_at: "2026-09-06T00:00:00Z"
});

describe("fineJobJobActions store", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
    apiMocks.list.mockImplementation((params: { status?: string }) =>
      Promise.resolve(params.status === "snoozed" ? snoozedResponse() : activeResponse())
    );
    apiMocks.snooze.mockResolvedValue({});
    apiMocks.dismiss.mockResolvedValue({});
    apiMocks.complete.mockResolvedValue({});
    apiMocks.restore.mockResolvedValue({});
    apiMocks.generateDrafts.mockResolvedValue({
      results: [{
        action_key: "reply:session-1:message-1",
        status: "created",
        reply_task_id: "task-1",
        error: null
      }]
    });
  });

  it("并行加载 active 和 snoozed，但 summary 只采用 active 响应", async () => {
    const store = useFineJobJobActionsStore();

    await store.load();

    expect(store.summary).toEqual(summary);
    expect(store.items.map((item) => item.action_key)).toEqual(["reply:session-1:message-1"]);
    expect(store.snoozedItems).toEqual([]);
    expect(apiMocks.list).toHaveBeenCalledWith({ status: "active" });
    expect(apiMocks.list).toHaveBeenCalledWith({ status: "snoozed" });
  });

  it("跳过缺少核心字段的 Action，并为缺少展示字段的 Action 提供占位", async () => {
    apiMocks.list.mockImplementation((params: { status?: string }) =>
      Promise.resolve(params.status === "snoozed"
        ? snoozedResponse()
        : activeResponse([
          actionItem({ action_key: "" }),
          actionItem({ title: "", company_name: "", reason_summary: "" })
        ]))
    );
    const store = useFineJobJobActionsStore();

    await store.load();

    expect(store.items).toHaveLength(1);
    expect(store.items[0].title).toBe("岗位名称待补充");
    expect(store.items[0].company_name).toBe("公司名称待补充");
    expect(store.invalidItemCount).toBe(1);
    expect(store.error).toContain("已跳过异常项");
  });

  it("筛选只作为 API 查询条件，不改变服务端返回顺序", async () => {
    const store = useFineJobJobActionsStore();

    await store.setActionType("followup_recruiter");
    await store.setPriority("urgent");

    expect(apiMocks.list).toHaveBeenLastCalledWith({
      status: "snoozed",
      priority: "urgent",
      action_type: "followup_recruiter"
    });
    expect(store.items[0].action_key).toBe("reply:session-1:message-1");
  });

  it("snooze 接受自定义 Date 并交给后端，成功后重新加载", async () => {
    const store = useFineJobJobActionsStore();
    const customTime = new Date("2026-09-09T08:00:00.000Z");

    await store.snooze("reply:session-1:message-1", customTime);

    expect(apiMocks.snooze).toHaveBeenCalledWith(
      "reply:session-1:message-1",
      "2026-09-09T08:00:00.000Z"
    );
    expect(apiMocks.list).toHaveBeenCalledWith({ status: "active" });
    expect(apiMocks.list).toHaveBeenCalledWith({ status: "snoozed" });
  });

  it("dismiss、complete、restore 都以服务端结果为准并刷新列表", async () => {
    const store = useFineJobJobActionsStore();

    await store.dismiss("reply:session-1:message-1");
    await store.complete("reply:session-1:message-1");
    await store.restore("reply:session-1:message-1");

    expect(apiMocks.dismiss).toHaveBeenCalledWith("reply:session-1:message-1");
    expect(apiMocks.complete).toHaveBeenCalledWith("reply:session-1:message-1");
    expect(apiMocks.restore).toHaveBeenCalledWith("reply:session-1:message-1");
    expect(apiMocks.list).toHaveBeenCalledTimes(6);
  });

  it("API 失败时保存 error 并结束 loading", async () => {
    apiMocks.list.mockRejectedValue(new Error("行动接口不可用"));
    const store = useFineJobJobActionsStore();

    await expect(store.load()).rejects.toThrow("行动接口不可用");

    expect(store.loading).toBe(false);
    expect(store.error).toBe("行动接口不可用");
  });

  it("批量生成采用服务端逐项结果并在完成后重新加载", async () => {
    const store = useFineJobJobActionsStore();
    const keys = ["reply:session-1:message-1"];

    const result = await store.generateDrafts(keys);

    expect(apiMocks.generateDrafts).toHaveBeenCalledWith(keys);
    expect(result.results[0].status).toBe("created");
    expect(store.batchResult).toEqual(result);
    expect(apiMocks.list).toHaveBeenCalledWith({ status: "active" });
    expect(apiMocks.list).toHaveBeenCalledWith({ status: "snoozed" });
  });
});
