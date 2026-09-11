// @vitest-environment jsdom

import { beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { defineComponent, h } from "vue";

const mocks = vi.hoisted(() => ({
  clear: vi.fn(),
  focus: vi.fn(),
  load: vi.fn(),
  start: vi.fn(),
  startWorkflow: vi.fn(),
  strategiesLoad: vi.fn(),
  submitPrompt: vi.fn(),
  attachWorkflowSession: vi.fn(),
  refreshWorkflow: vi.fn(),
  push: vi.fn(),
  replace: vi.fn(),
  routeQuery: {} as Record<string, string>,
  codexState: {
    status: "idle",
    runtimeId: "runtime-1" as string | null,
    sessionRef: "runtime:runtime-1" as string | null
  }
}));

vi.mock("@/stores/fineJobCodex", () => ({
  useFineJobCodexStore: () => ({
    get status() { return mocks.codexState.status; },
    get runtimeId() { return mocks.codexState.runtimeId; },
    get sessionRef() { return mocks.codexState.sessionRef; },
    statusMessage: "",
    permissions: null,
    pending: { greetings: [], chat_replies: [] },
    pendingCount: 0,
    loading: false,
    error: null,
    load: mocks.load,
    start: mocks.start,
    startWorkflow: mocks.startWorkflow,
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
  useRouter: () => ({ push: mocks.push, replace: mocks.replace })
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
    mocks.startWorkflow.mockReset().mockResolvedValue({
      status: "running",
      runtimeId: "runtime-2",
      sessionRef: "runtime:runtime-2",
      workflowSessionMode: "new_from_workflow_state"
    });
    mocks.strategiesLoad.mockReset().mockResolvedValue(undefined);
    mocks.submitPrompt.mockReset().mockResolvedValue(true);
    mocks.attachWorkflowSession.mockReset().mockResolvedValue(undefined);
    mocks.refreshWorkflow.mockReset().mockResolvedValue(undefined);
    mocks.push.mockReset().mockResolvedValue(undefined);
    mocks.replace.mockReset().mockImplementation(async (target: { query: Record<string, string> }) => {
      mocks.routeQuery = target.query;
    });
    mocks.routeQuery = {};
    mocks.codexState.status = "idle";
    mocks.codexState.runtimeId = "runtime-1";
    mocks.codexState.sessionRef = "runtime:runtime-1";
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

  it("首次 handoff 只提交一次 Workflow Prompt，并在不可恢复时提示从 Workflow 状态新建", async () => {
    mocks.routeQuery = {
      task: "deep-job-search",
      workflow_run_id: "workflow-run-1",
      workflow_action: "submit"
    };
    mocks.refreshWorkflow.mockResolvedValue({
      codex_session_ref: "runtime:closed-runtime-1",
      completion_contract: {
        codex_execution_config: { model: "gpt-5.6-luna", reasoning_effort: "high" }
      }
    });

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

    expect(mocks.startWorkflow).toHaveBeenCalledWith({
      cols: 120,
      rows: 36,
      model: "gpt-5.6-luna",
      reasoningEffort: "high",
      sessionRef: "runtime:closed-runtime-1"
    });
    expect(mocks.submitPrompt).toHaveBeenCalledWith(expect.stringContaining(
      "后续批次读取同一 Run 的 Shared Base、analysis_guidance 和已保存 Item 结果"
    ));
    expect(mocks.attachWorkflowSession).toHaveBeenCalledWith("workflow-run-1", {
      codex_session_ref: "runtime:runtime-2",
      codex_runtime_id: "runtime-2"
    });
    expect(mocks.replace).toHaveBeenCalledWith({
      name: "fine-job-codex",
      query: {
        task: "deep-job-search",
        workflow_run_id: "workflow-run-1",
        workflow_action: "view"
      }
    });
    expect(wrapper.html()).toContain("原 Codex 会话不可恢复，已基于 Workflow 状态建立新分析会话");

    wrapper.unmount();
    mount(CodexWorkspace, { global: { stubs: {
      CodexTerminal: CodexTerminalStub, ElAlert: GenericStub, ElButton: ElButtonStub,
      ElEmpty: GenericStub, ElInputNumber: GenericStub, ElOption: GenericStub,
      ElSelect: GenericStub, ElTag: GenericStub, ElSwitch: GenericStub
    } } });
    await flushPromises();
    expect(mocks.submitPrompt).toHaveBeenCalledTimes(1);
  });

  it("live_reused + view 只进入现有 Workflow 会话，不重复提交 Prompt", async () => {
    mocks.routeQuery = {
      task: "deep-job-search",
      workflow_run_id: "workflow-run-1",
      workflow_action: "view"
    };
    mocks.codexState.status = "running";
    mocks.codexState.sessionRef = "runtime:workflow-runtime-1";
    mocks.refreshWorkflow.mockResolvedValue({
      codex_session_ref: "runtime:workflow-runtime-1",
      completion_contract: {
        codex_execution_config: { model: "gpt-5.6-luna", reasoning_effort: "high" }
      }
    });
    mocks.startWorkflow.mockResolvedValue({
      status: "running",
      runtimeId: "runtime-1",
      sessionRef: "runtime:workflow-runtime-1",
      workflowSessionMode: "live_reused"
    });

    const wrapper = mount(CodexWorkspace, { global: { stubs: {
      CodexTerminal: CodexTerminalStub, ElAlert: GenericStub, ElButton: ElButtonStub,
      ElEmpty: GenericStub, ElInputNumber: GenericStub, ElOption: GenericStub,
      ElSelect: GenericStub, ElTag: GenericStub, ElSwitch: GenericStub
    } } });
    await flushPromises();

    expect(mocks.startWorkflow).toHaveBeenCalledTimes(1);
    expect(mocks.submitPrompt).not.toHaveBeenCalled();
    expect(mocks.attachWorkflowSession).not.toHaveBeenCalled();
    expect(wrapper.html()).toContain("已进入当前 Workflow 的 Codex 分析会话");
  });

  it("live_reused + submit 明确继续待分析批次时提交一次 Prompt", async () => {
    mocks.routeQuery = {
      task: "deep-job-search",
      workflow_run_id: "workflow-run-1",
      workflow_action: "submit"
    };
    mocks.codexState.status = "running";
    mocks.codexState.sessionRef = "runtime:workflow-runtime-1";
    mocks.refreshWorkflow.mockResolvedValue({
      codex_session_ref: "runtime:workflow-runtime-1",
      completion_contract: {
        codex_execution_config: { model: "gpt-5.6-luna", reasoning_effort: "high" }
      }
    });
    mocks.startWorkflow.mockResolvedValue({
      status: "running",
      runtimeId: "runtime-1",
      sessionRef: "runtime:workflow-runtime-1",
      workflowSessionMode: "live_reused"
    });

    mount(CodexWorkspace, { global: { stubs: {
      CodexTerminal: CodexTerminalStub, ElAlert: GenericStub, ElButton: ElButtonStub,
      ElEmpty: GenericStub, ElInputNumber: GenericStub, ElOption: GenericStub,
      ElSelect: GenericStub, ElTag: GenericStub, ElSwitch: GenericStub
    } } });
    await flushPromises();

    expect(mocks.submitPrompt).toHaveBeenCalledTimes(1);
  });

  it("submit 失败时保留 submit action，不 replace 为 view", async () => {
    mocks.routeQuery = {
      task: "deep-job-search",
      workflow_run_id: "workflow-run-1",
      workflow_action: "submit"
    };
    mocks.refreshWorkflow.mockResolvedValue({
      codex_session_ref: null,
      completion_contract: {
        codex_execution_config: { model: "gpt-5.6-luna", reasoning_effort: "high" }
      }
    });
    mocks.submitPrompt.mockResolvedValue(false);

    mount(CodexWorkspace, { global: { stubs: {
      CodexTerminal: CodexTerminalStub, ElAlert: GenericStub, ElButton: ElButtonStub,
      ElEmpty: GenericStub, ElInputNumber: GenericStub, ElOption: GenericStub,
      ElSelect: GenericStub, ElTag: GenericStub, ElSwitch: GenericStub
    } } });
    await flushPromises();

    expect(mocks.submitPrompt).toHaveBeenCalledTimes(1);
    expect(mocks.replace).not.toHaveBeenCalled();
    expect(mocks.routeQuery.workflow_action).toBe("submit");
  });
});
