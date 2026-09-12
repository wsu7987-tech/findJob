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
  getItemContext: vi.fn(),
  triggerWorkflowHandoff: vi.fn(),
  codexState: { status: "idle", sessionRef: null as string | null }
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
      setRun: (run: ReturnType<typeof run> | null) => {
        currentRun.value = run;
        return run;
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

vi.mock("@/stores/fineJobCodex", () => ({
  useFineJobCodexStore: () => ({
    get status() { return mocks.codexState.status; },
    get sessionRef() { return mocks.codexState.sessionRef; }
  })
}));

vi.mock("@/services/workflowCodexHandoff", () => ({
  triggerWorkflowCodexHandoff: mocks.triggerWorkflowHandoff
}));

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
const FormItemStub = defineComponent({
  props: { label: { type: String, default: "" } },
  template: "<div><span>{{ label }}</span><slot /></div>"
});
const DescriptionsItemStub = defineComponent({
  props: { label: { type: String, default: "" } },
  template: "<div><span>{{ label }}</span><slot /></div>"
});
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
  completion_contract: { target_count: 2, execution_policy: { codex_handoff: "manual" as const } },
  analysis_handoff: {
    analysis_batch_id: "analysis-batch-1",
    pending_item_count: 2,
    running_item_count: 0,
    succeeded_item_count: 0,
    handoff_status: "none",
    attempt_status: "none",
    needs_initial_codex_handoff: true,
    needs_next_batch_handoff: false,
    codex_processing: false,
    analysis_batch_complete: false,
    recovery_available: false,
    awaiting_start_ack: false,
    start_ack_timed_out: false,
    retry_available: false,
    start_ack_timeout_seconds: 45
  }
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
      ElDescriptionsItem: DescriptionsItemStub,
      ElDialog: GenericStub,
      ElEmpty: GenericStub,
      ElForm: GenericStub,
      ElFormItem: FormItemStub,
      ElInput: InputStub,
      ElInputNumber: GenericStub,
      ElOption: GenericStub,
      ElSelect: GenericStub,
      ElSwitch: GenericStub,
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
    mocks.triggerWorkflowHandoff.mockReset().mockImplementation(async (value) => ({
      status: "submitted", run: value, message: "已交接"
    }));
    mocks.codexState.status = "idle";
    mocks.codexState.sessionRef = null;
  });

  it("manual 模式显示交给 Codex 分析，并调用共享 orchestration", async () => {
    const wrapper = mountCockpit();
    await flushPromises();
    await wrapper.find('[placeholder="输入 Workflow Run ID 查看本轮上下文"]').setValue("workflow-run-1");
    await wrapper.findAll("button").find((item) => item.text() === "查看本轮上下文")!.trigger("click");
    await flushPromises();

    const handoff = wrapper.findAll("button").find((item) => item.text() === "交给 Codex 分析");
    expect(handoff).toBeDefined();
    expect(wrapper.findAll("button").some((item) => item.text() === "查看 Codex 分析")).toBe(false);
    await handoff!.trigger("click");
    expect(mocks.triggerWorkflowHandoff).toHaveBeenCalledWith(expect.objectContaining({ workflow_run_id: "workflow-run-1" }), expect.anything(), "manual");
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

  it("展示 Completion Contract 与批次衔接的最小配置", async () => {
    const wrapper = mountCockpit();
    await flushPromises();

    expect(wrapper.text()).toContain("Recommend 完成目标");
    expect(wrapper.text()).toContain("Review 完成目标（可选）");
    expect(wrapper.text()).toContain("目标达成模式");
    expect(wrapper.text()).toContain("Analysis Batch");
    expect(wrapper.text()).toContain("达标后的候选池处理");
    expect(wrapper.text()).toContain("批次衔接");
    expect(wrapper.text()).toContain("Codex 交接");
  });

  it("批次等待用户时显示继续入口", async () => {
    mocks.getRun.mockResolvedValue({
      ...run("waiting_for_user"),
      stop_reason: "analysis_batch_completed_waiting_user"
    });
    const wrapper = mountCockpit();
    await flushPromises();
    await wrapper.find('[placeholder="输入 Workflow Run ID 查看本轮上下文"]').setValue("workflow-run-1");
    await wrapper.findAll("button").find((item) => item.text() === "查看本轮上下文")!.trigger("click");
    await flushPromises();

    expect(wrapper.findAll("button").some((item) => item.text() === "继续")).toBe(true);
  });

  it("使用 completion_progress 与实际 pending review 展示完成结果", async () => {
    const completedRun = {
      ...run("completed"),
      completion_progress: {
        recommend: { current: 0, target: 2, remaining: 2, reached: false },
        review: { current: 1, target: 1, remaining: 0, reached: true },
        target_mode: "any",
        target_reached: true
      }
    };
    mocks.getRun.mockResolvedValue(completedRun);
    mocks.getLatestRun.mockResolvedValue({ workflow_run: completedRun });
    mocks.listItems.mockResolvedValue({
      items: [
        {
          workflow_task_id: "recommend-item", status: "succeeded",
          analysis_result: { decision: "recommend", review_status: "pending" },
          job: { title: "推荐岗位", company: "公司 A" }
        },
        {
          workflow_task_id: "review-item", status: "succeeded",
          analysis_result: { decision: "review", review_status: "pending" },
          job: { title: "复核岗位", company: "公司 B" }
        }
      ]
    });
    const wrapper = mountCockpit();
    await flushPromises();

    expect(wrapper.text()).toContain("业务完成状态");
    expect(wrapper.text()).toContain("已达到配置目标");
    expect(wrapper.text()).toContain("Recommend 完成");
    expect(wrapper.text()).toContain("Review 完成");
    expect(wrapper.text()).toContain("进入待确认 2");
  });

  it("waiting_codex 的存活 sessionRef 匹配时优先显示查看 Codex 分析", async () => {
    mocks.getRun.mockResolvedValue({
      ...run("waiting_codex"),
      codex_session_ref: "runtime:workflow-runtime-1"
    });
    mocks.codexState.status = "running";
    mocks.codexState.sessionRef = "runtime:workflow-runtime-1";
    const wrapper = mountCockpit();
    await flushPromises();
    await wrapper.find('[placeholder="输入 Workflow Run ID 查看本轮上下文"]').setValue("workflow-run-1");
    await wrapper.findAll("button").find((item) => item.text() === "查看本轮上下文")!.trigger("click");
    await flushPromises();

    const view = wrapper.findAll("button").find((item) => item.text() === "查看 Codex 分析");
    expect(view).toBeDefined();
    expect(wrapper.findAll("button").some((item) => item.text() === "交给 Codex 分析")).toBe(false);
    await view!.trigger("click");
    expect(mocks.push).toHaveBeenCalledWith({
      name: "fine-job-codex",
      query: {
        task: "deep-job-search",
        workflow_run_id: "workflow-run-1",
        workflow_action: "view"
      }
    });
  });

  it("已结束的 runtime session 不显示无效果的查看按钮", async () => {
    mocks.getRun.mockResolvedValue({
      ...run("running"),
      codex_session_ref: "runtime:ended-runtime-1"
    });
    const wrapper = mountCockpit();
    await flushPromises();
    await wrapper.find('[placeholder="输入 Workflow Run ID 查看本轮上下文"]').setValue("workflow-run-1");
    await wrapper.findAll("button").find((item) => item.text() === "查看本轮上下文")!.trigger("click");
    await flushPromises();

    expect(wrapper.findAll("button").some((item) => item.text() === "查看 Codex 分析")).toBe(false);
  });

  it("第一批完成后新 pending 批次在同一存活会话显示继续分析下一批", async () => {
    mocks.getRun.mockResolvedValue({
      ...run("waiting_codex"),
      codex_session_ref: "runtime:workflow-runtime-1",
      analysis_handoff: {
        ...run("waiting_codex").analysis_handoff,
        analysis_batch_id: "analysis-batch-2",
        pending_item_count: 1,
        succeeded_item_count: 0,
        needs_initial_codex_handoff: false,
        needs_next_batch_handoff: true
      }
    });
    mocks.codexState.status = "running";
    mocks.codexState.sessionRef = "runtime:workflow-runtime-1";
    const wrapper = mountCockpit();
    await flushPromises();
    await wrapper.find('[placeholder="输入 Workflow Run ID 查看本轮上下文"]').setValue("workflow-run-1");
    await wrapper.findAll("button").find((item) => item.text() === "查看本轮上下文")!.trigger("click");
    await flushPromises();

    const next = wrapper.findAll("button").find((item) => item.text() === "继续分析下一批");
    expect(next).toBeDefined();
    await next!.trigger("click");
    expect(mocks.triggerWorkflowHandoff).toHaveBeenCalledWith(expect.anything(), expect.anything(), "manual");
  });

  it("Codex 正在处理批次时只显示查看入口，不显示继续分析", async () => {
    mocks.getRun.mockResolvedValue({
      ...run("waiting_codex"),
      codex_session_ref: "runtime:workflow-runtime-1",
      analysis_handoff: {
        ...run("waiting_codex").analysis_handoff,
        handoff_status: "submitted",
        attempt_status: "started",
        codex_processing: true
      }
    });
    mocks.codexState.status = "running";
    mocks.codexState.sessionRef = "runtime:workflow-runtime-1";
    const wrapper = mountCockpit();
    await flushPromises();
    await wrapper.find('[placeholder="输入 Workflow Run ID 查看本轮上下文"]').setValue("workflow-run-1");
    await wrapper.findAll("button").find((item) => item.text() === "查看本轮上下文")!.trigger("click");
    await flushPromises();

    expect(wrapper.findAll("button").some((item) => item.text() === "Codex 分析中")).toBe(true);
    expect(wrapper.findAll("button").some((item) => item.text() === "继续分析下一批")).toBe(false);
  });

  it("auto 模式只展示准备状态，不显示人工交接按钮", async () => {
    mocks.getRun.mockResolvedValue({
      ...run("waiting_codex"),
      completion_contract: { target_count: 2, execution_policy: { codex_handoff: "auto" } }
    });
    const wrapper = mountCockpit();
    await flushPromises();
    await wrapper.find('[placeholder="输入 Workflow Run ID 查看本轮上下文"]').setValue("workflow-run-1");
    await wrapper.findAll("button").find((item) => item.text() === "查看本轮上下文")!.trigger("click");
    await flushPromises();

    expect(wrapper.text()).toContain("准备 Codex");
    expect(wrapper.findAll("button").some((item) => item.text() === "交给 Codex 分析")).toBe(false);
    expect(wrapper.findAll("button").some((item) => item.text() === "继续分析下一批")).toBe(false);
  });
});
