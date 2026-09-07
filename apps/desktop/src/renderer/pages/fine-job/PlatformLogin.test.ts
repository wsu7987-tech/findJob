// @vitest-environment jsdom

import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import { flushPromises, shallowMount } from "@vue/test-utils";
import { defineComponent } from "vue";

const mocks = vi.hoisted(() => ({
  getStatus: vi.fn(),
  start: vi.fn(),
  stop: vi.fn(),
  mark: vi.fn(),
  openPath: vi.fn(),
  platformLoad: vi.fn(),
  captureLoadStatus: vi.fn(),
  openLoginWindow: vi.fn(),
  checkLoginStatus: vi.fn(),
  messageSuccess: vi.fn(),
  messageError: vi.fn(),
  messageWarning: vi.fn()
}));

vi.mock("@/services/api", () => ({
  api: {
    getFineJobBossNetworkDebugStatus: mocks.getStatus,
    startFineJobBossNetworkDebug: mocks.start,
    stopFineJobBossNetworkDebug: mocks.stop,
    markFineJobBossNetworkDebug: mocks.mark
  }
}));

vi.mock("@/stores/fineJobPlatformSessions", () => ({
  useFineJobPlatformSessionsStore: () => ({
    bossSession: null,
    loading: false,
    openingLogin: false,
    checking: false,
    error: null,
    load: mocks.platformLoad,
    openBossLoginWindow: mocks.openLoginWindow,
    checkBossLoginStatus: mocks.checkLoginStatus
  })
}));

vi.mock("@/stores/fineJobBossCapture", () => ({
  useFineJobBossCaptureStore: () => ({
    status: { running: true },
    loadStatus: mocks.captureLoadStatus
  })
}));

vi.mock("element-plus", () => ({
  ElMessage: {
    success: mocks.messageSuccess,
    error: mocks.messageError,
    warning: mocks.messageWarning
  }
}));

const ButtonStub = defineComponent({
  inheritAttrs: false,
  emits: ["click"],
  template: "<button v-bind=\"$attrs\" type=\"button\" @click=\"$emit('click', $event)\"><slot /></button>"
});

const InputStub = defineComponent({
  props: { modelValue: { type: String, default: "" } },
  emits: ["update:modelValue"],
  template: "<input :value=\"modelValue\" @input=\"$emit('update:modelValue', $event.target.value)\" />"
});

const TagStub = defineComponent({ template: "<span><slot /></span>" });

const AlertStub = defineComponent({
  props: { title: String, description: String },
  template: "<div><span>{{ title }}</span><span>{{ description }}</span><slot /></div>"
});

const buildStatus = (overrides: Record<string, unknown> = {}) => ({
  active: false,
  trace_id: null,
  event_count: 0,
  request_count: 0,
  frame_count: 0,
  marker_count: 0,
  dropped_event_count: 0,
  target_count: 0,
  targets: [],
  evidence_complete: true,
  gap_reasons: [],
  output_path: null,
  started_at: null,
  finished_at: null,
  error_message: null,
  marker: null,
  ...overrides
});

const mountPage = () => shallowMount(PlatformLogin, {
  global: {
    directives: { loading: () => undefined },
    stubs: {
      "el-button": ButtonStub,
      "el-input": InputStub,
      "el-tag": TagStub,
      "el-alert": AlertStub
    }
  }
});

import PlatformLogin from "./PlatformLogin.vue";

describe("PlatformLogin Protocol Trace", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
    mocks.platformLoad.mockResolvedValue(undefined);
    mocks.captureLoadStatus.mockResolvedValue(undefined);
    mocks.getStatus.mockResolvedValue(buildStatus());
    mocks.start.mockResolvedValue(buildStatus({ active: true, trace_id: "trace-1" }));
    mocks.stop.mockResolvedValue(buildStatus({ output_path: "D:/trace/trace.json", finished_at: "2026-09-07T00:00:00Z" }));
    mocks.mark.mockImplementation(async () => buildStatus({ active: true, trace_id: "trace-1", marker_count: 1 }));
    mocks.openPath.mockResolvedValue("");
    window.desktopBridge = { openPath: mocks.openPath } as unknown as typeof window.desktopBridge;
  });

  afterEach(() => {
    vi.useRealTimers();
    delete window.desktopBridge;
  });

  it("加载并展示后端 status 字段", async () => {
    mocks.getStatus.mockResolvedValue(buildStatus({
      active: true,
      trace_id: "trace-42",
      event_count: 12,
      request_count: 4,
      frame_count: 6,
      marker_count: 2,
      dropped_event_count: 3,
      target_count: 1,
      evidence_complete: false,
      gap_reasons: ["tail_drain_unconfirmed", "event_buffer_overflow"],
      error_message: "trace warning"
    }));

    const wrapper = mountPage();
    await flushPromises();

    expect(mocks.getStatus).toHaveBeenCalledTimes(1);
    expect(wrapper.text()).toContain("正在记录");
    expect(wrapper.text()).toContain("trace-42");
    expect(wrapper.text()).toContain("12");
    expect(wrapper.text()).toContain("证据不完整");
    expect(wrapper.text()).toContain("tail_drain_unconfirmed、event_buffer_overflow");
    expect(wrapper.text()).toContain("3");
    expect(wrapper.text()).toContain("trace warning");
  });

  it("Start 和 Stop 根据 active 状态切换并立即刷新", async () => {
    const wrapper = mountPage();
    await flushPromises();

    const startButton = wrapper.findAll("button").find((button) => button.text() === "开始 Trace");
    const stopButton = wrapper.findAll("button").find((button) => button.text() === "停止 Trace");
    expect(startButton?.attributes("disabled")).toBeUndefined();
    expect(stopButton?.attributes("disabled")).toBeDefined();
    expect(wrapper.findAll("button").filter((button) => [
      "发送消息前", "发送消息后", "发送简历前", "发送简历后", "投递前", "投递后"
    ].includes(button.text())).every((button) => button.attributes("disabled") !== undefined)).toBe(true);

    await startButton?.trigger("click");
    await flushPromises();
    expect(mocks.start).toHaveBeenCalledTimes(1);
    expect(wrapper.text()).toContain("正在记录");
    expect(startButton?.attributes("disabled")).toBeDefined();
    expect(stopButton?.attributes("disabled")).toBeUndefined();

    await stopButton?.trigger("click");
    await flushPromises();
    expect(mocks.stop).toHaveBeenCalledTimes(1);
    expect(wrapper.text()).toContain("未记录");
    expect(wrapper.text()).toContain("D:/trace/trace.json");
  });

  it("只通过 mark API 发送六个固定 marker", async () => {
    mocks.getStatus.mockResolvedValue(buildStatus({ active: true, trace_id: "trace-1" }));
    const wrapper = mountPage();
    await flushPromises();

    for (const label of ["发送消息前", "发送消息后", "发送简历前", "发送简历后", "投递前", "投递后"]) {
      const button = wrapper.findAll("button").find((candidate) => candidate.text() === label);
      await button?.trigger("click");
      await flushPromises();
    }

    expect(mocks.mark.mock.calls.map(([payload]) => payload.marker)).toEqual([
      "before_send",
      "after_send",
      "before_resume",
      "after_resume",
      "before_apply",
      "after_apply"
    ]);
  });

  it("添加自定义 marker 前只做空值检查并调用 mark API", async () => {
    mocks.getStatus.mockResolvedValue(buildStatus({ active: true }));
    const wrapper = mountPage();
    await flushPromises();

    await wrapper.find("input").setValue("  custom_marker  ");
    await wrapper.findAll("button").find((button) => button.text() === "添加标记")?.trigger("click");
    await flushPromises();

    expect(mocks.mark).toHaveBeenCalledWith({ marker: "custom_marker" });
  });

  it("打开 Trace 使用现有 desktopBridge.openPath", async () => {
    mocks.getStatus.mockResolvedValue(buildStatus({ output_path: "D:/trace/trace.json" }));
    const wrapper = mountPage();
    await flushPromises();

    await wrapper.findAll("button").find((button) => button.text() === "打开 Trace")?.trigger("click");
    expect(mocks.openPath).toHaveBeenCalledWith("D:/trace/trace.json");
  });

  it("active 时轮询，停止后和卸载时清理 timer", async () => {
    mocks.getStatus
      .mockResolvedValueOnce(buildStatus({ active: true }))
      .mockResolvedValue(buildStatus({ active: false }));
    const wrapper = mountPage();
    await flushPromises();

    await vi.advanceTimersByTimeAsync(2000);
    await flushPromises();
    const callsAfterInactive = mocks.getStatus.mock.calls.length;
    await vi.advanceTimersByTimeAsync(4000);
    expect(mocks.getStatus).toHaveBeenCalledTimes(callsAfterInactive);

    wrapper.unmount();
    await vi.advanceTimersByTimeAsync(4000);
    expect(mocks.getStatus).toHaveBeenCalledTimes(callsAfterInactive);
  });

  it("后端错误沿用页面错误提示", async () => {
    mocks.start.mockRejectedValue(new Error("backend unavailable"));
    const wrapper = mountPage();
    await flushPromises();
    await wrapper.findAll("button").find((button) => button.text() === "开始 Trace")?.trigger("click");
    await flushPromises();

    expect(wrapper.text()).toContain("backend unavailable");
    expect(mocks.messageError).toHaveBeenCalledWith("backend unavailable");
  });
});
