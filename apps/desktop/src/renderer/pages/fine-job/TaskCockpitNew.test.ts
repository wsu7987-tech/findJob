// @vitest-environment jsdom

import { beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { defineComponent } from "vue";

const mocks = vi.hoisted(() => ({
  restoreLatest: vi.fn(),
  create: vi.fn(),
  pause: vi.fn(),
  resume: vi.fn(),
  cancel: vi.fn(),
  decideChild: vi.fn(),
  resumeChild: vi.fn(),
  retryChild: vi.fn(),
  push: vi.fn(),
  listFilters: vi.fn(),
  listRecommendations: vi.fn(),
  getConfig: vi.fn(),
  listModels: vi.fn(),
  run: null as Record<string, unknown> | null
}));

vi.mock("@/stores/fineJobWorkflowRun", () => ({
  useFineJobWorkflowRunStore: () => ({
    get currentRun() { return mocks.run; },
    loading: false,
    advancing: false,
    restoreLatest: mocks.restoreLatest,
    create: mocks.create,
    pause: mocks.pause,
    resume: mocks.resume,
    cancel: mocks.cancel,
    decideChild: mocks.decideChild,
    resumeChild: mocks.resumeChild,
    retryChild: mocks.retryChild
  })
}));

vi.mock("@/services/api", () => ({
  ApiError: class ApiError extends Error {},
  api: {
    listFineJobFilterStrategies: mocks.listFilters,
    listFineJobRecommendationStrategies: mocks.listRecommendations,
    getConfig: mocks.getConfig,
    listCodexModels: mocks.listModels,
    getFineJobActiveCollectionTask: vi.fn().mockResolvedValue({ active_task: null })
  }
}));

vi.mock("vue-router", () => ({ useRouter: () => ({ push: mocks.push }) }));

import TaskCockpitNew from "./TaskCockpitNew.vue";

const ButtonStub = defineComponent({
  inheritAttrs: false,
  emits: ["click"],
  template: '<button v-bind="$attrs" type="button" @click="$emit(\'click\')"><slot /></button>'
});
const ContainerStub = defineComponent({ template: "<div><slot /></div>" });
const DrawerStub = defineComponent({ props: ["modelValue"], template: '<div v-if="modelValue" data-testid="config-drawer"><slot /></div>' });
const CheckboxGroupStub = defineComponent({ template: "<div><slot /></div>" });
const CheckboxStub = defineComponent({ template: "<label><slot /></label>" });

const child = (updates: Record<string, unknown> = {}) => ({
  child_relation_id: "child-1",
  child_type: "smart_capture",
  child_ref: "capture-1",
  status: "running",
  control_state: "active",
  waiting_reason: "",
  control_cause: "",
  capabilities: { resume: false, retry: false },
  result_summary: { short_summary: "已采集 8 个候选" },
  started_at: "2026-09-22T12:00:00Z",
  completed_at: null,
  ...updates
});

const run = (updates: Record<string, unknown> = {}) => ({
  workflow_run_id: "run-1",
  status: "running",
  control_state: "active",
  waiting_reason: "",
  next_action_reason: "正在执行岗位采集",
  children: [child()],
  ...updates
});

const mountPage = () => mount(TaskCockpitNew, { global: { stubs: {
  ElCard: ContainerStub, ElAlert: ContainerStub, ElTag: ContainerStub, ElTimeline: ContainerStub,
  ElTimelineItem: ContainerStub, ElEmpty: ContainerStub, ElButton: ButtonStub, ElDrawer: DrawerStub,
  ElCheckboxGroup: CheckboxGroupStub, ElCheckbox: CheckboxStub, SmartCaptureConfigForm: ContainerStub
} } });

describe("TaskCockpitNew", () => {
  beforeEach(() => {
    mocks.run = null;
    mocks.restoreLatest.mockReset().mockResolvedValue(null);
    mocks.create.mockReset(); mocks.pause.mockReset(); mocks.resume.mockReset(); mocks.cancel.mockReset();
    mocks.decideChild.mockReset(); mocks.resumeChild.mockReset(); mocks.retryChild.mockReset(); mocks.push.mockReset();
    mocks.listFilters.mockReset().mockResolvedValue({ strategies: [] });
    mocks.listRecommendations.mockReset().mockResolvedValue({ strategies: [] });
    mocks.getConfig.mockReset().mockResolvedValue({ codex_model: "", codex_reasoning_effort: "medium" });
    mocks.listModels.mockReset().mockResolvedValue({ models: [] });
  });

  it("idle 与父终态显示可编辑编排区，并从右侧 Drawer 配置子任务", async () => {
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.get('[data-testid="orchestration-panel"]').text()).toContain("岗位采集");
    expect(wrapper.findAll("button").find((button) => button.text().includes("建立并自动推进"))?.attributes("disabled")).toBeDefined();
    await wrapper.findComponent(CheckboxGroupStub).vm.$emit("change", ["smart_capture"]);
    await flushPromises();
    expect(wrapper.find('[data-testid="config-drawer"]').exists()).toBe(true);

    mocks.run = run({ status: "completed", control_state: "active" });
    const terminalWrapper = mountPage();
    await flushPromises();
    expect(terminalWrapper.find('[data-testid="orchestration-panel"]').exists()).toBe(true);
    expect(terminalWrapper.get('[data-testid="child-timeline"]').text()).toContain("已采集 8 个候选");
  });

  it("running 与等待决策隐藏编排区，并只显示合法父层操作", async () => {
    mocks.run = run();
    const runningWrapper = mountPage();
    await flushPromises();
    expect(runningWrapper.find('[data-testid="orchestration-panel"]').exists()).toBe(false);
    expect(runningWrapper.text()).toContain("暂停");
    expect(runningWrapper.text()).toContain("停止任务");

    mocks.run = run({
      status: "waiting_for_user",
      control_state: "child_failed_waiting_decision",
      children: [child({ status: "failed", control_state: "child_failed_waiting_decision", capabilities: { resume: false, retry: false } })]
    });
    const waitingWrapper = mountPage();
    await flushPromises();
    expect(waitingWrapper.find('[data-testid="orchestration-panel"]').exists()).toBe(false);
    expect(waitingWrapper.text()).toContain("跳过该子任务继续");
    expect(waitingWrapper.text()).toContain("结束父任务");
    expect(waitingWrapper.text()).not.toContain("重试子任务");
    await waitingWrapper.findAll("button").find((button) => button.text() === "跳过该子任务继续")!.trigger("click");
    expect(mocks.decideChild).toHaveBeenCalledWith("skip");
  });

  it("时间线只使用 child 摘要，跳转岗位采集不携带 ID，并按 capability 显示恢复", async () => {
    mocks.run = run({
      status: "waiting_for_user",
      control_state: "waiting_child_interrupted",
      children: [child({ control_state: "waiting_child_interrupted", waiting_reason: "browser", capabilities: { resume: true, retry: true } })]
    });
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.get('[data-testid="child-timeline"]').text()).toContain("已采集 8 个候选");
    await wrapper.findAll("button").find((button) => button.text() === "查看岗位采集")!.trigger("click");
    expect(mocks.push).toHaveBeenCalledWith({ name: "fine-job-capture" });
    expect(wrapper.text()).toContain("恢复子任务");
    expect(wrapper.text()).toContain("重试子任务");
  });
});
