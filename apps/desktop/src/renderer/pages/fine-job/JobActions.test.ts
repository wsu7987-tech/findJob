// @vitest-environment jsdom

import { beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { defineComponent } from "vue";

import type { FineJobJobActionItem } from "@/types";

const mocks = vi.hoisted(() => ({
  push: vi.fn(),
  store: {
    items: [] as FineJobJobActionItem[],
    snoozedItems: [] as FineJobJobActionItem[],
    summary: { urgent: 1, high: 2, normal: 3, low: 4, snoozed: 5 } as {
      urgent: number;
      high: number;
      normal: number;
      low: number;
      snoozed: number;
    } | null,
    actionType: "",
    priority: "",
    loading: false,
    mutating: false,
    error: null as string | null,
    invalidItemCount: 0,
    batchResult: null as null | { results: Array<Record<string, unknown>> },
    load: vi.fn(),
    refresh: vi.fn(),
    setActionType: vi.fn(),
    setPriority: vi.fn(),
    snooze: vi.fn(),
    dismiss: vi.fn(),
    complete: vi.fn(),
    restore: vi.fn(),
    generateDrafts: vi.fn()
  }
}));

vi.mock("@/stores/fineJobJobActions", () => ({
  useFineJobJobActionsStore: () => mocks.store
}));
vi.mock("vue-router", () => ({
  useRouter: () => ({ push: mocks.push })
}));
vi.mock("element-plus", () => ({
  ElMessage: {
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn()
  },
  ElMessageBox: { confirm: vi.fn().mockResolvedValue("confirm") }
}));

import JobActions from "./JobActions.vue";

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
}) as FineJobJobActionItem;

const ElementStub = defineComponent({
  inheritAttrs: false,
  template: "<div v-bind='$attrs'><slot /><slot name='dropdown' /></div>"
});
const ButtonStub = defineComponent({
  inheritAttrs: false,
  emits: ["click"],
  template: "<button v-bind='$attrs' type='button' @click='$emit(\"click\")'><slot /></button>"
});
const SelectStub = defineComponent({
  inheritAttrs: false,
  props: { modelValue: { type: String, default: "" } },
  emits: ["change"],
  template: "<select v-bind='$attrs' :value='modelValue' @change='$emit(\"change\", $event.target.value)'><slot /></select>"
});
const OptionStub = defineComponent({
  props: { label: { type: String, default: "" }, value: { type: String, default: "" } },
  template: "<option :value='value'>{{ label }}</option>"
});
const CheckboxStub = defineComponent({
  inheritAttrs: false,
  props: { modelValue: { type: Boolean, default: false } },
  emits: ["change"],
  template: "<label><input v-bind='$attrs' type='checkbox' :checked='modelValue' @change='$emit(\"change\", $event.target.checked)' /><slot /></label>"
});
const EmptyStateStub = defineComponent({
  emits: ["action"],
  template: "<div data-testid='empty-state'><h3>今天暂时没有需要处理的岗位</h3><button data-testid='empty-action' @click='$emit(\"action\")'>去求职数据更新</button></div>"
});
const AlertStub = defineComponent({
  props: {
    title: { type: String, default: "" },
    description: { type: String, default: "" }
  },
  template: "<div data-testid='alert'>{{ title }} {{ description }}</div>"
});

const globalStubs = {
  ElAlert: AlertStub,
  ElButton: ButtonStub,
  ElCheckbox: CheckboxStub,
  ElDatePicker: ElementStub,
  ElDialog: ElementStub,
  ElDropdown: ElementStub,
  ElDropdownItem: ElementStub,
  ElDropdownMenu: ElementStub,
  ElOption: OptionStub,
  ElSelect: SelectStub,
  ElTag: ElementStub,
  EmptyState: EmptyStateStub
};

describe("JobActions", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.store.items = [];
    mocks.store.snoozedItems = [];
    mocks.store.summary = { urgent: 1, high: 2, normal: 3, low: 4, snoozed: 5 };
    mocks.store.actionType = "";
    mocks.store.priority = "";
    mocks.store.loading = false;
    mocks.store.mutating = false;
    mocks.store.error = null;
    mocks.store.invalidItemCount = 0;
    mocks.store.batchResult = null;
    mocks.store.load.mockResolvedValue(undefined);
    mocks.store.refresh.mockResolvedValue(undefined);
    mocks.store.setActionType.mockResolvedValue(undefined);
    mocks.store.setPriority.mockResolvedValue(undefined);
    mocks.store.snooze.mockResolvedValue(undefined);
    mocks.store.dismiss.mockResolvedValue(undefined);
    mocks.store.complete.mockResolvedValue(undefined);
    mocks.store.restore.mockResolvedValue(undefined);
    mocks.store.generateDrafts.mockResolvedValue({ results: [] });
  });

  it("加载页面并显示 P0 summary，同时尊重后端返回顺序", async () => {
    mocks.store.items = [
      actionItem({ action_key: "first", title: "第一岗位" }),
      actionItem({ action_key: "second", title: "第二岗位" })
    ];
    const wrapper = mount(JobActions, { global: { stubs: globalStubs } });
    await flushPromises();

    expect(mocks.store.refresh).toHaveBeenCalled();
    expect(wrapper.get('[data-testid="summary-urgent"]').text()).toBe("1");
    expect(wrapper.get('[data-testid="summary-high"]').text()).toBe("2");
    expect(wrapper.get('[data-testid="summary-normal"]').text()).toBe("3");
    expect(wrapper.get('[data-testid="summary-snoozed"]').text()).toBe("5");
    expect(wrapper.findAll(".job-action-card h3").map((item) => item.text())).toEqual([
      "第一岗位",
      "第二岗位"
    ]);
  });

  it("显示 loading 和 error 状态", () => {
    mocks.store.loading = true;
    mocks.store.summary = null;
    mocks.store.error = "行动接口不可用";
    const wrapper = mount(JobActions, { global: { stubs: globalStubs } });

    expect(wrapper.attributes("aria-busy")).toBe("true");
    expect(wrapper.text()).toContain("行动接口不可用");
  });

  it("没有 active Action 时显示空状态和两个入口", async () => {
    const wrapper = mount(JobActions, { global: { stubs: globalStubs } });
    await flushPromises();

    expect(wrapper.text()).toContain("今天暂时没有需要处理的岗位");
    expect(wrapper.text()).toContain("查看求职分析");
    await wrapper.get('[data-testid="empty-action"]').trigger("click");
    expect(mocks.push).toHaveBeenCalledWith({ name: "fine-job-refresh" });
  });

  it("Action 类型和优先级筛选交给 Store", async () => {
    const wrapper = mount(JobActions, { global: { stubs: globalStubs } });
    const selects = wrapper.findAll("select");

    await selects[0].setValue("review_draft");
    await selects[1].setValue("urgent");

    expect(mocks.store.setActionType).toHaveBeenCalledWith("review_draft");
    expect(mocks.store.setPriority).toHaveBeenCalledWith("urgent");
  });

  it("开始处理和主按钮都进入当前 Action 的自动代聊上下文", async () => {
    mocks.store.items = [actionItem()];
    const wrapper = mount(JobActions, { global: { stubs: globalStubs } });

    await wrapper.get('[data-testid="start-processing-button"]').trigger("click");
    await wrapper.get('[data-testid="action-primary"]').trigger("click");

    expect(mocks.push).toHaveBeenNthCalledWith(1, {
      name: "fine-job-chat",
      query: {
        session_id: "session-1",
        job_id: "job-1",
        action_type: "reply_recruiter",
        action_kind: "reply",
        action_key: "reply:session-1:message-1"
      }
    });
  });

  it("review_draft 仅在待审核草稿仍与当前 session 对应时传递 task id", async () => {
    const reviewTask = {
      id: "task-1",
      action_kind: "reply",
      status: "awaiting_review",
      based_on_message_id: "message-1",
      based_on_session_version: 2,
      draft_text: "草稿",
      final_text: "草稿",
      updated_at: "2026-09-06T00:00:00Z"
    } as const;
    mocks.store.items = [actionItem({
      action_key: "review_draft:task-1",
      action_type: "review_draft",
      primary_action: {
        type: "open_chat",
        label: "审核草稿",
        route_name: "fine-job-chat",
        query: { session_id: "session-1" },
        action_kind: "reply",
        reply_task_id: "task-1"
      },
      reply_task: reviewTask
    })];
    const wrapper = mount(JobActions, { global: { stubs: globalStubs } });

    await wrapper.get('[data-testid="action-primary"]').trigger("click");
    expect(mocks.push).toHaveBeenCalledWith(expect.objectContaining({
      query: expect.objectContaining({ reply_task_id: "task-1" })
    }));

    wrapper.unmount();
    mocks.push.mockReset();
    mocks.store.items = [actionItem({
      action_type: "review_draft",
      primary_action: {
        type: "open_chat",
        label: "审核草稿",
        route_name: "fine-job-chat",
        query: { session_id: "session-1" },
        reply_task_id: "task-1"
      },
      reply_task: { ...reviewTask, status: "stale" }
    }) as unknown as FineJobJobActionItem];
    const invalidWrapper = mount(JobActions, { global: { stubs: globalStubs } });
    await invalidWrapper.get('[data-testid="action-primary"]').trigger("click");
    expect(mocks.push).toHaveBeenCalledWith(expect.objectContaining({
      query: expect.not.objectContaining({ reply_task_id: "task-1" })
    }));
  });

  it("快捷稍后处理、忽略、完成和恢复都调用对应 Store 操作", async () => {
    const item = actionItem();
    const wrapper = mount(JobActions, { global: { stubs: globalStubs } });
    const vm = wrapper.vm as unknown as {
      handleActionCommand: (value: { command: string; item: FineJobJobActionItem }) => Promise<unknown>;
    };

    await vm.handleActionCommand({ command: "snooze-1", item });
    await vm.handleActionCommand({ command: "dismiss", item });
    await vm.handleActionCommand({ command: "complete", item });
    await vm.handleActionCommand({ command: "restore", item });

    expect(mocks.store.snooze).toHaveBeenCalledWith(item.action_key, expect.any(Date));
    expect(mocks.store.dismiss).toHaveBeenCalledWith(item.action_key);
    expect(mocks.store.complete).toHaveBeenCalledWith(item.action_key);
    expect(mocks.store.restore).toHaveBeenCalledWith(item.action_key);
  });

  it("大量 Action 使用独立滚动列表容器", async () => {
    mocks.store.items = Array.from({ length: 120 }, (_, index) => actionItem({
      action_key: `reply:session-1:message-${index}`,
      title: `岗位 ${index + 1}`
    }));
    const wrapper = mount(JobActions, { global: { stubs: globalStubs } });
    await flushPromises();

    expect(wrapper.find('[data-testid="active-action-list"]').classes()).toContain("job-actions-list");
    expect(wrapper.findAll(".job-action-card")).toHaveLength(120);
  });

  it("全选当前可生成项，确认安全提示后按服务端结果刷新", async () => {
    const { ElMessageBox } = await import("element-plus");
    mocks.store.items = [
      actionItem(),
      actionItem({
        action_key: "resume:job-2:event-2",
        job_id: "job-2",
        action_type: "send_resume",
        primary_action: {
          type: "open_chat",
          label: "查看并处理",
          route_name: "fine-job-chat",
          query: { session_id: "session-2" },
          action_kind: null,
          reply_task_id: null
        }
      })
    ];
    const wrapper = mount(JobActions, { global: { stubs: globalStubs } });

    await wrapper.get('[data-testid="select-all-current"]').setValue(true);
    await wrapper.get('[data-testid="batch-generate-button"]').trigger("click");
    await flushPromises();

    expect(ElMessageBox.confirm).toHaveBeenCalledWith(
      "将为 1 个岗位生成草稿\n不会自动发送",
      "批量生成草稿",
      expect.objectContaining({ confirmButtonText: "开始生成" })
    );
    expect(mocks.store.generateDrafts).toHaveBeenCalledWith([
      "reply:session-1:message-1"
    ]);
  });

  it("展示后端批量结果统计", () => {
    mocks.store.batchResult = {
      results: [
        { action_key: "a", status: "created" },
        { action_key: "b", status: "already_exists" },
        { action_key: "c", status: "skipped" },
        { action_key: "d", status: "failed" }
      ]
    };
    const wrapper = mount(JobActions, { global: { stubs: globalStubs } });

    expect(wrapper.get('[data-testid="batch-result"]').text()).toContain(
      "新生成 1　已有草稿 1　跳过 1　失败 1"
    );
  });
});
