// @vitest-environment jsdom

import { beforeEach, describe, expect, it, vi } from "vitest";
import { mount } from "@vue/test-utils";

const chartMocks = vi.hoisted(() => ({
  init: vi.fn(),
  setOption: vi.fn(),
  resize: vi.fn(),
  dispose: vi.fn()
}));

vi.mock("echarts", () => ({
  init: chartMocks.init
}));

import ChartSurface from "./ChartSurface.vue";

describe("ChartSurface", () => {
  beforeEach(() => {
    Object.values(chartMocks).forEach((mock) => mock.mockReset());
    chartMocks.init.mockReturnValue({
      setOption: chartMocks.setOption,
      resize: chartMocks.resize,
      dispose: chartMocks.dispose
    });
  });

  it("初始化、更新并在卸载时释放图表实例", async () => {
    const wrapper = mount(ChartSurface, {
      props: { option: { series: [{ data: [1] }] } }
    });

    expect(chartMocks.init).toHaveBeenCalledTimes(1);
    expect(chartMocks.setOption).toHaveBeenCalledTimes(1);
    expect(chartMocks.resize).toHaveBeenCalled();

    await wrapper.setProps({ option: { series: [{ data: [1, 2] }] } });
    expect(chartMocks.setOption).toHaveBeenCalledTimes(2);

    wrapper.unmount();
    expect(chartMocks.dispose).toHaveBeenCalledTimes(1);
  });
});
