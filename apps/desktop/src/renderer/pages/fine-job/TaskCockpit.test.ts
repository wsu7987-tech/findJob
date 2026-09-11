// @vitest-environment jsdom

import { beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { defineComponent } from "vue";

const mocks = vi.hoisted(() => ({
  push: vi.fn(),
  getSnapshot: vi.fn(),
  getRun: vi.fn(),
  getLatestRun: vi.fn(),
  advance: vi.fn(),
  listStrategies: vi.fn(),
  listRecommendations: vi.fn(),
  getConfig: vi.fn(),
  listModels: vi.fn(),
  listItems: vi.fn(),
  getItemContext: vi.fn()
}));

vi.mock("@/services/api", () => ({
  api: {
    getFineJobWorkflowContextSnapshot: mocks.getSnapshot,
    getFineJobWorkflowRun: mocks.getRun,
    getLatestFineJobWorkflowRun: mocks.getLatestRun,
    advanceFineJobWorkflowRun: mocks.advance,
    listFineJobFilterStrategies: mocks.listStrategies,
    listFineJobRecommendationStrategies: mocks.listRecommendations,
    getConfig: mocks.getConfig,
    listCodexModels: mocks.listModels,
    listFineJobWorkflowAnalysisItems: mocks.listItems,
    getFineJobWorkflowAnalysisItemContext: mocks.getItemContext
  }
}));

vi.mock("@/stores/fineJobWorkflowRun", async () => {
  const { ref } = await import("vue");
  const currentRun = ref<ReturnType<typeof run> | null>(null);
  const refresh = async (id?: string) => {
    if (!id) return null;
    currentRun.value = await mocks.getRun(id);
    return currentRun.value;
  };
  return {
    useFineJobWorkflowRunStore: () => ({
      get currentRun() {
        return currentRun.value;
      },
      loading: false,
      advancing: false,
      refresh,
      advance: mocks.advance,
      create: vi.fn(),
      restoreLatest: async () => {
        const response = await mocks.getLatestRun();
        currentRun.value = response.workflow_run;
        return currentRun.value;
      },
      pause: vi.fn(),
      resume: vi.fn(),
      cancel: vi.fn()
    })
  };
});

vi.mock("vue-router", () => ({
  useRouter: () => ({ push: mocks.push }),
  useRoute: () => ({ query: {} })
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
const TableColumnStub = defineComponent({
  template: "<div><slot :row=\"{ job: { title: '', company: '', salary: '', city: '', discovery_keyword: '', discovery_depth: 0, filter_result: '', filter_reasons: [], jd_status: '' }, status: '', analysis_result: {} }\" /></div>"
});

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
  progress: {
    current_keyword: "AI Agent",
    current_city: "广州",
    search_depth: 5,
    search_batch_count: 1,
    jobs_seen: 10,
    fresh_jobs: 3,
    duplicate_jobs: 7,
    candidates: 3,
    current_batch_new_jobs: 3,
    current_batch_duplicates: 7,
    jd_total: 0,
    jd_completed: 0,
    recommend_count: 0,
    review_count: 0,
    reject_count: 0
  },
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
      ElDialog: GenericStub,
      ElEmpty: GenericStub,
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
    mocks.getLatestRun.mockReset().mockResolvedValue({ workflow_run: null });
    mocks.advance.mockReset().mockResolvedValue(run("waiting_codex"));
    mocks.listStrategies.mockReset().mockResolvedValue({ strategies: [] });
    mocks.listRecommendations.mockReset().mockResolvedValue({ strategies: [] });
    mocks.getConfig.mockReset().mockResolvedValue({ codex_model: "gpt-5.6-luna", codex_reasoning_effort: "medium", codex_cli_path: "codex" });
    mocks.listModels.mockReset().mockResolvedValue({ models: [] });
    mocks.listItems.mockReset().mockResolvedValue({ items: [] });
    mocks.getItemContext.mockReset().mockResolvedValue({});
  });

  it("仅 waiting_codex 显示并交接到现有 Codex 工作台", async () => {
    const wrapper = mountCockpit();
    await flushPromises();
    await wrapper.find('[placeholder="输入 Workflow Run ID 查看本轮上下文"]').setValue("workflow-run-1");
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
    await wrapper.find('[placeholder="输入 Workflow Run ID 查看本轮上下文"]').setValue("workflow-run-1");
    await wrapper.findAll("button").find((item) => item.text() === "查看本轮上下文")!.trigger("click");
    await flushPromises();

    expect(wrapper.findAll("button").some((item) => item.text() === "交给 Codex 分析")).toBe(false);
  });
});
