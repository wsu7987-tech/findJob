// @vitest-environment jsdom

import { beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { defineComponent } from "vue";


const mocks = vi.hoisted(() => ({
  createRun: vi.fn(),
  attach: vi.fn(),
  submitPrompt: vi.fn(),
  startReading: vi.fn(),
  stopReading: vi.fn(),
  load: vi.fn(),
  codexLoad: vi.fn(),
  messageSuccess: vi.fn(),
  messageError: vi.fn(),
  messageWarning: vi.fn()
}));

const { createdRun } = vi.hoisted(() => ({ createdRun: {
  id: "refresh-run-1",
  scope_id: "refresh-scope-1",
  scope_generated_at: "2026-09-05T00:00:00Z",
  status: "pending",
  selected_since_time: "2026-09-04T00:00:00Z",
  workflow_options: {
    refresh_chat_list: true,
    refresh_chat_messages: true,
    refresh_related_jobs: true,
    analyze_conversations: false,
    generate_missing_suggestions: false
  },
  progress: {
    chat_list: { status: "succeeded" },
    chat_messages: { total: 0, completed: 0, succeeded: 0, failed: 0 },
    related_jobs: { total: 0, completed: 0, succeeded: 0, failed: 0 }
  },
  summary: {},
  resume_available: true,
  created_at: "2026-09-05T00:00:00Z",
  updated_at: "2026-09-05T00:00:00Z"
} }));

vi.mock("@/stores/fineJobJobHuntRefresh", () => ({
  useFineJobJobHuntRefreshStore: () => ({
    context: {
      timezone: "Asia/Shanghai",
      latest_local_message_at: null,
      last_successful_completed_at: null,
      default_since_time: "2026-09-04T00:00:00Z"
    },
    selectedSinceTime: "2026-09-04T00:00:00Z",
    workflowOptions: createdRun.workflow_options,
    scope: {
      id: "refresh-scope-1",
      selected_since_time: "2026-09-04T00:00:00Z",
      scope_generated_at: "2026-09-05T00:00:00Z",
      counts: {
        refreshed_sessions: 3,
        sessions_to_sync: 2,
        new_sessions_to_sync: 1,
        related_jobs: 2,
        jobs_to_collect: 1,
        jobs_missing_jd: 1,
        jobs_missing_evaluation: 2,
        unresolved_relations: 0
      }
    },
    currentRun: null,
    recentRuns: [],
    discovering: false,
    starting: false,
    error: null,
    hasExecutableWorkflow: true,
    load: mocks.load,
    discoverScope: vi.fn(),
    createRun: mocks.createRun,
    attachCodexSession: mocks.attach,
    selectRun: vi.fn(),
    startProgressReading: mocks.startReading,
    stopProgressReading: mocks.stopReading,
    invalidateScope: vi.fn()
  })
}));

vi.mock("@/stores/fineJobCodex", () => ({
  useFineJobCodexStore: () => ({
    status: "running",
    runId: "codex-session-1",
    load: mocks.codexLoad,
    start: vi.fn()
  })
}));

vi.mock("@/services/desktop-bridge", () => ({
  getCodexBridge: () => ({ submitCodexPrompt: mocks.submitPrompt })
}));

vi.mock("vue-router", () => ({
  useRouter: () => ({ push: vi.fn() })
}));

vi.mock("element-plus", () => ({
  ElMessage: {
    success: mocks.messageSuccess,
    error: mocks.messageError,
    warning: mocks.messageWarning
  }
}));

import JobHuntRefresh from "./JobHuntRefresh.vue";

const ElementStub = defineComponent({
  inheritAttrs: false,
  template: "<div v-bind=\"$attrs\"><slot /></div>"
});

const ButtonStub = defineComponent({
  inheritAttrs: false,
  emits: ["click"],
  template: "<button v-bind=\"$attrs\" @click=\"$emit('click')\"><slot /></button>"
});

describe("JobHuntRefresh", () => {
  beforeEach(() => {
    Object.values(mocks).forEach((mock) => mock.mockReset());
    mocks.load.mockResolvedValue(undefined);
    mocks.codexLoad.mockResolvedValue(undefined);
    mocks.createRun.mockResolvedValue(createdRun);
    mocks.attach.mockResolvedValue(createdRun);
    mocks.submitPrompt.mockResolvedValue(true);
  });

  it("创建持久化 Run 后向 Codex 提交只含 run_id 的结构化任务", async () => {
    const wrapper = mount(JobHuntRefresh, {
      global: {
        stubs: {
          ElAlert: ElementStub,
          ElButton: ButtonStub,
          ElCheckbox: ElementStub,
          ElDatePicker: ElementStub,
          ElTable: ElementStub,
          ElTableColumn: ElementStub,
          ElTag: ElementStub
        }
      }
    });
    await flushPromises();

    await wrapper.get("[data-testid='start-refresh-button']").trigger("click");
    await flushPromises();

    expect(mocks.createRun).toHaveBeenCalledTimes(1);
    expect(mocks.attach).toHaveBeenCalledWith("refresh-run-1", "codex-session-1");
    expect(mocks.submitPrompt).toHaveBeenCalledTimes(1);
    const prompt = String(mocks.submitPrompt.mock.calls[0][0]);
    expect(prompt).toContain('"workflow":"job_hunt_refresh_v1"');
    expect(prompt).toContain('"run_id":"refresh-run-1"');
    expect(prompt).toContain("不得重跑 succeeded 项");

    wrapper.unmount();
    expect(mocks.stopReading).toHaveBeenCalledTimes(1);
  });
});
