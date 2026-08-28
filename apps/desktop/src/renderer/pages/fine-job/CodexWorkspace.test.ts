// @vitest-environment jsdom

import { beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { defineComponent, h } from "vue";

const mocks = vi.hoisted(() => ({
  focus: vi.fn(),
  load: vi.fn(),
  start: vi.fn()
}));

vi.mock("@/stores/fineJobCodex", () => ({
  useFineJobCodexStore: () => ({
    status: "idle",
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

vi.mock("@/services/desktop-bridge", () => ({
  getCodexBridge: () => ({})
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
    expose({ focus: mocks.focus });
    return () => h("div", { "data-testid": "codex-terminal" });
  }
});

describe("CodexWorkspace", () => {
  beforeEach(() => {
    mocks.focus.mockReset();
    mocks.load.mockReset();
    mocks.start.mockReset().mockResolvedValue(undefined);
  });

  it("新建会话完成后将焦点交给 Codex 终端", async () => {
    const wrapper = mount(CodexWorkspace, {
      global: {
        stubs: {
          CodexTerminal: CodexTerminalStub,
          ElAlert: GenericStub,
          ElButton: ElButtonStub,
          ElEmpty: GenericStub,
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
});
