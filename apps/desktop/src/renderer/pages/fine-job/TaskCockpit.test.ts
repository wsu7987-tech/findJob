// @vitest-environment jsdom

import { beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { defineComponent } from "vue";

const mocks = vi.hoisted(() => ({
  push: vi.fn(),
  getSnapshot: vi.fn(),
  getRun: vi.fn(),
  listStrategies: vi.fn()
}));

vi.mock("@/services/api", () => ({
  api: {
    getFineJobWorkflowContextSnapshot: mocks.getSnapshot,
    getFineJobWorkflowRun: mocks.getRun,
    listFineJobFilterStrategies: mocks.listStrategies
  }
}));

vi.mock("vue-router", () => ({
  useRouter: () => ({ push: mocks.push })
}));

import TaskCockpit from "./TaskCockpit.vue";

const ButtonStub = defineComponent({
  inheritAttrs: false,
  emits: ["click"],
  template: "<button v-bind=\"$attrs\" type=\"button\" @click=\"$emit('click')\"><slot /></button>"
});

const InputStub = defineComponent({
  inheritAttrs: false,
  props: { modelValue: { type: String, default: "" } },
  emits: ["update:modelValue"],
  template: "<input v-bind=\"$attrs\" :value=\"modelValue\" @input=\"$emit('update:modelValue', $event.target.value)\" />"
});

const GenericStub = defineComponent({ template: "<div><slot /></div>" });
const TableColumnStub = defineComponent({ template: "<div><slot :row=\"{}\" /></div>" });

const snapshot = {
  context_snapshot_id: "snapshot-1",
  channel: "deep_job_search",
  sections: [],
  context_characters: 10,
  estimated_tokens: 4,
  soft_budget_characters: 12000,
  hard_budget_characters: 1000000,
  status: "ready" as const,
  blocker_reason: "",
  generated_at: "2026-09-11T00:00:00Z"
};

const run = (status: string) => ({
  workflow_run_id: "workflow-run-1",
  workflow_type: "deep_job_search" as const,
  status,
  completed_count: 0,
  remaining_count: 2,
  current_step: status,
  next_action: "codex_analysis",
  next_action_reason: "等待分析",
  waiting_for_user: false,
  stop_reason: "",
  telemetry: { fresh_candidates: 3 },
  completion_contract: { target_count: 2 }
});

const mountCockpit = () => mount(TaskCockpit, {
  global: {
    stubs: {
      ElAlert: GenericStub,
      ElButton: ButtonStub,
      ElCard: GenericStub,
      ElCheckbox: GenericStub,
      ElCheckboxGroup: GenericStub,
      ElCollapse: GenericStub,
      ElCollapseItem: GenericStub,
      ElDescriptions: GenericStub,
      ElDescriptionsItem: GenericStub,
      ElForm: GenericStub,
      ElFormItem: GenericStub,
      ElInput: InputStub,
      ElInputNumber: GenericStub,
      ElOption: GenericStub,
      ElSelect: GenericStub,
      ElTable: GenericStub,
      ElTableColumn: TableColumnStub,
      ElTag: GenericStub
    }
  }
});

describe("TaskCockpit", () => {
  beforeEach(() => {
    mocks.push.mockReset().mockResolvedValue(undefined);
    mocks.getSnapshot.mockReset().mockResolvedValue(snapshot);
    mocks.getRun.mockReset().mockResolvedValue(run("waiting_codex"));
    mocks.listStrategies.mockReset().mockResolvedValue({ strategies: [] });
  });

  it("仅 waiting_codex 显示并交接到现有 Codex 工作台", async () => {
    const wrapper = mountCockpit();
    await flushPromises();
    await wrapper.find("input").setValue("workflow-run-1");
    await wrapper.findAll("button").find((item) => item.text() === "查看本轮上下文")!.trigger("click");
    await flushPromises();

    const handoff = wrapper.findAll("button").find((item) => item.text() === "交给 Codex 分析");
    expect(handoff).toBeDefined();
    await handoff!.trigger("click");
    expect(mocks.push).toHaveBeenCalledWith({
      name: "fine-job-codex",
      query: { task: "deep-job-search", workflow_run_id: "workflow-run-1" }
    });
  });

  it("非 waiting_codex 状态不显示分析入口", async () => {
    mocks.getRun.mockResolvedValue(run("running"));
    const wrapper = mountCockpit();
    await flushPromises();
    await wrapper.find("input").setValue("workflow-run-1");
    await wrapper.findAll("button").find((item) => item.text() === "查看本轮上下文")!.trigger("click");
    await flushPromises();

    expect(wrapper.findAll("button").some((item) => item.text() === "交给 Codex 分析")).toBe(false);
  });
});
