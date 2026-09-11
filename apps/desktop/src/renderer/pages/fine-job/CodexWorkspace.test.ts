// @vitest-environment jsdom

import { beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { defineComponent, h } from "vue";

const mocks = vi.hoisted(() => ({
  clear: vi.fn(),
  focus: vi.fn(),
  load: vi.fn(),
  start: vi.fn(),
  strategiesLoad: vi.fn(),
  submitPrompt: vi.fn(),
  attachWorkflowSession: vi.fn(),
  refreshWorkflow: vi.fn(),
  push: vi.fn(),
  routeQuery: {} as Record<string, string>
}));

vi.mock("@/stores/fineJobCodex", () => ({
  useFineJobCodexStore: () => ({
    status: "idle",
    runId: "codex-session-1",
    statusMessage: "",
    permissions: null,
    pending: { greetings: [], chat_replies: [] },
    pendingCount: 0,
    loading: false,
    error: null,
    load: mocks.load,
    start: mocks.start,
    savePermissions: vi.fn(),
    decide: vi.fn()
  })
}));

vi.mock("@/stores/fineJobWorkflowRun", () => ({
  useFineJobWorkflowRunStore: () => ({ refresh: mocks.refreshWorkflow })
}));

vi.mock("@/services/api", () => ({
  api: { attachFineJobWorkflowCodexSession: mocks.attachWorkflowSession }
}));

vi.mock("@/stores/fineJobStrategies", () => ({
  useFineJobStrategiesStore: () => ({
    filters: [{ id: "filter-1", name: "Agent 筛选", enabled: true }],
    recommendations: [{ id: "recommendation-1", name: "Agent 建议", enabled: true }],
    load: mocks.strategiesLoad
  })
}));

vi.mock("@/services/desktop-bridge", () => ({
  getCodexBridge: () => ({ submitCodexPrompt: mocks.submitPrompt })
}));

vi.mock("vue-router", () => ({
  useRoute: () => ({ query: mocks.routeQuery }),
  useRouter: () => ({ push: mocks.push })
}));

import CodexWorkspace from "./CodexWorkspace.vue";

const ElButtonStub = defineComponent({
  inheritAttrs: false,
  emits: ["click"],
  template: "<button v-bind=\"$attrs\" type=\"button\" @click=\"$emit('click', $event)\"><slot /></button>"
});

const GenericStub = defineComponent({
  template: "<div><slot /></div>"
});

const CodexTerminalStub = defineComponent({
  emits: ["ready"],
  setup(_, { expose }) {
    expose({ clear: mocks.clear, focus: mocks.focus });
    return () => h("div", { "data-testid": "codex-terminal" });
  }
});

describe("CodexWorkspace", () => {
  beforeEach(() => {
    mocks.clear.mockReset();
    mocks.focus.mockReset();
    mocks.load.mockReset();
    mocks.start.mockReset().mockResolvedValue(undefined);
    mocks.strategiesLoad.mockReset().mockResolvedValue(undefined);
    mocks.submitPrompt.mockReset().mockResolvedValue(true);
    mocks.attachWorkflowSession.mockReset().mockResolvedValue(undefined);
    mocks.refreshWorkflow.mockReset().mockResolvedValue(undefined);
    mocks.push.mockReset().mockResolvedValue(undefined);
    mocks.routeQuery = {};
  });

  it("新建会话完成后将焦点交给 Codex 终端", async () => {
    const wrapper = mount(CodexWorkspace, {
      global: {
        stubs: {
          CodexTerminal: CodexTerminalStub,
          ElAlert: GenericStub,
          ElButton: ElButtonStub,
          ElEmpty: GenericStub,
          ElInputNumber: GenericStub,
          ElOption: GenericStub,
          ElSelect: GenericStub,
          ElTag: GenericStub,
          ElSwitch: GenericStub
        }
      }
    });

    await wrapper.get("button").trigger("click");
    await flushPromises();

    expect(mocks.start).toHaveBeenCalledWith(120, 36, false);
    expect(mocks.focus).toHaveBeenCalledTimes(1);
  });

  it("Clear 只清空终端显示", async () => {
    const wrapper = mount(CodexWorkspace, {
      global: {
        stubs: {
          CodexTerminal: CodexTerminalStub,
          ElAlert: GenericStub,
          ElButton: ElButtonStub,
          ElEmpty: GenericStub,
          ElInputNumber: GenericStub,
          ElOption: GenericStub,
          ElSelect: GenericStub,
          ElTag: GenericStub,
          ElSwitch: GenericStub
        }
      }
    });

    await flushPromises();
    const clearButton = wrapper.findAll("button").find((button) => button.text() === "Clear");
    expect(clearButton).toBeDefined();
    await clearButton!.trigger("click");

    expect(mocks.clear).toHaveBeenCalledTimes(1);
  });

  it("两个快捷按钮分别提交短 Skill 任务", async () => {
    const wrapper = mount(CodexWorkspace, {
      global: {
        stubs: {
          CodexTerminal: CodexTerminalStub,
          ElAlert: GenericStub,
          ElButton: ElButtonStub,
          ElEmpty: GenericStub,
          ElInputNumber: GenericStub,
          ElOption: GenericStub,
          ElSelect: GenericStub,
          ElTag: GenericStub,
          ElSwitch: GenericStub
        }
      }
    });

    await flushPromises();
    await wrapper.get('[data-testid="submit-filter-task"]').trigger("click");
    await flushPromises();
    await wrapper.get('[data-testid="submit-recommendation-task"]').trigger("click");
    await flushPromises();

    expect(mocks.start).toHaveBeenCalledTimes(2);
    expect(mocks.submitPrompt).toHaveBeenNthCalledWith(
      1,
      "使用 $finejob，按岗位筛选策略“Agent 筛选”（filter_strategy_id=filter-1）从新采集开始，完成 20 条岗位筛选。"
    );
    expect(mocks.submitPrompt).toHaveBeenNthCalledWith(
      2,
      "使用 $finejob，按建议投递策略“Agent 建议”（recommendation_strategy_id=recommendation-1）从新采集开始获取 10 条推荐投递岗位。开始前提醒当前自动招呼状态；本任务只生成建议并放入待确认，不执行真实招呼。"
    );
  });

  it("deep_job_search 通过新会话提交最小 Workflow 身份提示词", async () => {
    mocks.routeQuery = { task: "deep-job-search", workflow_run_id: "workflow-run-1" };

    mount(CodexWorkspace, {
      global: {
        stubs: {
          CodexTerminal: CodexTerminalStub,
          ElAlert: GenericStub,
          ElButton: ElButtonStub,
          ElEmpty: GenericStub,
          ElInputNumber: GenericStub,
          ElOption: GenericStub,
          ElSelect: GenericStub,
          ElTag: GenericStub,
          ElSwitch: GenericStub
        }
      }
    });
    await flushPromises();

    expect(mocks.start).toHaveBeenCalledWith(120, 36, false);
    expect(mocks.submitPrompt).toHaveBeenCalledWith(
      "使用 $finejob 继续处理 deep_job_search Workflow，workflow_run_id=workflow-run-1。严格根据 Workflow Run 状态、Completion Contract 和 FineJob Skill 执行，直到 completed 或 waiting_for_user。"
    );
    expect(mocks.attachWorkflowSession).toHaveBeenCalledWith("workflow-run-1", {
      codex_session_ref: "codex-session-1"
    });
  });
});
