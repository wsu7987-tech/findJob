// @vitest-environment jsdom

import { beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { defineComponent } from "vue";

import AppShell from "./AppShell.vue";
import { useConfigStore } from "@/stores/config";

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
          SystemStatusChip: GenericStub,
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

  it("toggles main window always-on-top from the title bar action", async () => {
    const wrapper = mount(AppShell, {
      slots: {
        default: "<div>content</div>"
      },
      global: {
        stubs: {
          SystemStatusChip: GenericStub,
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
