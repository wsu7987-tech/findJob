// @vitest-environment jsdom

import { beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { defineComponent } from "vue";

import AppShell from "./AppShell.vue";
import { useConfigStore } from "@/stores/config";
import type { AppConfigPayload } from "@/types";

const getMainWindowStateMock = vi.fn();
const setMainWindowAlwaysOnTopMock = vi.fn();

vi.mock("vue-router", () => ({
  RouterLink: defineComponent({
    props: {
      to: {
        type: [String, Object],
        required: true
      }
    },
    template: "<a href='#'><slot /></a>"
  }),
  useRoute: () => ({
    name: "fine-job-dashboard",
    meta: {
      title: "求职总览"
    }
  })
}));

vi.mock("@/services/desktop-bridge", () => ({
  getMainWindowState: (...args: unknown[]) => getMainWindowStateMock(...args),
  setMainWindowAlwaysOnTop: (...args: unknown[]) => setMainWindowAlwaysOnTopMock(...args)
}));

const GenericStub = defineComponent({
  template: "<div><slot /></div>"
});

const SystemStatusChipStub = defineComponent({
  props: {
    label: {
      type: String,
      required: true
    },
    state: {
      type: Object,
      required: true
    }
  },
  template: "<div data-testid='status-chip'>{{ label }}|{{ state.status }}|{{ state.detail }}</div>"
});

const ElButtonStub = defineComponent({
  inheritAttrs: false,
  emits: ["click"],
  template: "<button v-bind=\"$attrs\" type='button' @click=\"$emit('click', $event)\"><slot /></button>"
});

describe("AppShell", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    getMainWindowStateMock.mockReset();
    setMainWindowAlwaysOnTopMock.mockReset();
    getMainWindowStateMock.mockResolvedValue({
      alwaysOnTop: false,
      fullscreen: false
    });
    setMainWindowAlwaysOnTopMock.mockResolvedValue({
      alwaysOnTop: true
    });
  });

  it("renders one-click connectivity testing when generation is not ready", async () => {
    const configStore = useConfigStore();
    const probeSpy = vi
      .spyOn(configStore, "probeGenerationCapabilities")
      .mockResolvedValue(undefined);

    const wrapper = mount(AppShell, {
      slots: {
        default: "<div>content</div>"
      },
      global: {
        stubs: {
          SystemStatusChip: SystemStatusChipStub,
          ElTooltip: GenericStub,
          ElButton: ElButtonStub,
          ArrowRight: true,
          Calendar: true,
          ChatDotRound: true,
          Document: true,
          Files: true,
          Link: true,
          List: true,
          Search: true,
          Setting: true,
          Suitcase: true,
          Top: true,
          TrendCharts: true,
          User: true
        }
      }
    });

    await flushPromises();

    expect(wrapper.text()).toContain("一键联通测试");
    await wrapper.get('[data-testid="connectivity-check"]').trigger("click");

    expect(probeSpy).toHaveBeenCalledTimes(1);
  });

  it("shows the selected Codex executor status in the main system bar", async () => {
    const configStore = useConfigStore();
    configStore.data = {
      reasoning_executor: "codex-cli"
    } as AppConfigPayload;
    configStore.codexStatus = {
      status: "ready",
      detail: "Codex CLI is installed and authenticated.",
      checkedAt: "2026-08-16T04:00:00Z",
      provider: "codex-cli",
      model: null,
      baseUrl: "codex",
      errorCategory: null,
      cliVersion: "0.147.0",
      authenticated: true,
      reasoningEffort: null
    };

    const wrapper = mount(AppShell, {
      global: {
        stubs: {
          SystemStatusChip: SystemStatusChipStub,
          ElTooltip: GenericStub,
          ElButton: ElButtonStub,
          ArrowRight: true,
          Document: true,
          Link: true,
          List: true,
          Monitor: true,
          Setting: true,
          Suitcase: true,
          Top: true,
          TrendCharts: true,
          User: true,
          Wallet: true
        }
      }
    });

    await flushPromises();

    expect(wrapper.text()).toContain(
      "Codex CLI 必需|ready|Codex CLI is installed and authenticated."
    );
    expect(wrapper.text()).not.toContain("LLM 必需");
  });

  it("toggles main window always-on-top from the title bar action", async () => {
    const wrapper = mount(AppShell, {
      slots: {
        default: "<div>content</div>"
      },
      global: {
        stubs: {
          SystemStatusChip: SystemStatusChipStub,
          ElTooltip: GenericStub,
          ElButton: ElButtonStub,
          ArrowRight: true,
          Calendar: true,
          ChatDotRound: true,
          Document: true,
          Files: true,
          Link: true,
          List: true,
          Search: true,
          Setting: true,
          Suitcase: true,
          Top: true,
          TrendCharts: true,
          User: true
        }
      }
    });

    await flushPromises();
    await wrapper.get('[data-testid="main-window-pin"]').trigger("click");

    expect(setMainWindowAlwaysOnTopMock).toHaveBeenCalledWith(true);
  });
});
