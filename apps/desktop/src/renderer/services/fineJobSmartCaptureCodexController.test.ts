import { describe, expect, it, vi } from "vitest";

import type { FineJobSmartCaptureAnalysisSnapshot } from "@/types";

import { createFineJobSmartCaptureCodexController } from "./fineJobWorkflowCodexController";

const snapshot = (): FineJobSmartCaptureAnalysisSnapshot => ({
  smart_capture_id: "smart-capture-controller-1",
  workflow_run_id: null,
  status: "waiting_for_user",
  analysis_batch_id: "analysis-controller-1",
  items: [{ status: "pending", payload: { codex_model: "gpt-5.6-luna", codex_reasoning_effort: "high" } } as never],
  handoff: {
    analysis_batch_id: "analysis-controller-1",
    pending_item_count: 1,
    running_item_count: 0,
    succeeded_item_count: 0,
    handoff_status: "none",
    attempt_status: "none",
    needs_initial_codex_handoff: true,
    needs_next_batch_handoff: false
  },
  smart_capture: {
    smart_capture_id: "smart-capture-controller-1",
    source: "boss_capture",
    workflow_run_id: null,
    status: "waiting_for_user",
    search_config: {},
    execution_config: { analysis: { codex_model: "gpt-5.6-luna", codex_reasoning_effort: "high", handoff: "auto" } },
    stage: "waiting_codex",
    waiting_reason: "codex",
    control_cause: "",
    state_version: 1,
    capabilities: { start: false, pause: false, resume: false, retry: false, stop: true },
    progress: {},
    result_summary: {},
    message: "",
    created_at: "",
    updated_at: "",
    batches: [],
    jobs: []
  }
});

describe("fineJobSmartCaptureCodexController", () => {
  it("从 current Smart Capture 发现 ready batch，且不调用 Workflow advance", async () => {
    const current = { smart_capture: snapshot().smart_capture };
    const getCurrentSmartCapture = vi.fn().mockResolvedValue(current);
    const getAnalysisSnapshot = vi.fn().mockResolvedValue(snapshot());
    const advance = vi.fn();
    const codexStore = {
      status: "idle",
      runtimeId: null,
      sessionRef: null,
      startSmartCapture: vi.fn().mockResolvedValue({ runtimeId: "runtime-controller-1", sessionRef: "runtime:controller-1" })
    };
    const client = {
      getFineJobSmartCaptureAnalysisSnapshot: getAnalysisSnapshot,
      attachFineJobSmartCaptureCodexSession: vi.fn().mockResolvedValue({}),
      claimFineJobSmartCaptureAnalysisHandoff: vi.fn().mockResolvedValue({
        ...snapshot(),
        handoff: { ...snapshot().handoff!, handoff_attempt_id: "attempt-controller-1" }
      }),
      markFineJobSmartCaptureAnalysisHandoffPromptWritten: vi.fn().mockResolvedValue(snapshot()),
      releaseFineJobSmartCaptureAnalysisHandoff: vi.fn().mockResolvedValue(snapshot())
    };
    const transport = { submitSmartCaptureCodexPrompt: vi.fn().mockResolvedValue(true) };
    const controller = createFineJobSmartCaptureCodexController({
      getCurrentSmartCapture,
      getAnalysisSnapshot,
      codexStore,
      handoffDependencies: { client, transport }
    });

    await controller.start();
    await controller.tick();

    expect(getCurrentSmartCapture).toHaveBeenCalledTimes(2);
    expect(advance).not.toHaveBeenCalled();
    expect(codexStore.startSmartCapture).toHaveBeenCalledTimes(1);
    expect(transport.submitSmartCaptureCodexPrompt).toHaveBeenCalledWith(expect.stringContaining("smart_capture_id="));
    controller.stop();
  });

  it("通过 SSE 触发并按 state_version 去重，不创建定时轮询", async () => {
    const sources = new Map<string, {
      onmessage: ((event: MessageEvent<string>) => void) | null;
      close: ReturnType<typeof vi.fn>;
    }>();
    const createEventSource = vi.fn((url: string) => {
      const source = { onmessage: null, close: vi.fn() };
      sources.set(url, source);
      return source as unknown as EventSource;
    });
    const getAnalysisSnapshot = vi.fn().mockResolvedValue(snapshot());
    const codexStore = {
      status: "idle",
      runtimeId: null,
      sessionRef: null,
      startSmartCapture: vi.fn().mockResolvedValue({ runtimeId: "runtime-controller-1", sessionRef: "runtime:controller-1" })
    };
    const client = {
      getFineJobSmartCaptureAnalysisSnapshot: getAnalysisSnapshot,
      attachFineJobSmartCaptureCodexSession: vi.fn().mockResolvedValue({}),
      claimFineJobSmartCaptureAnalysisHandoff: vi.fn().mockResolvedValue({
        ...snapshot(),
        handoff: { ...snapshot().handoff!, handoff_attempt_id: "attempt-controller-1" }
      }),
      markFineJobSmartCaptureAnalysisHandoffPromptWritten: vi.fn().mockResolvedValue(snapshot()),
      releaseFineJobSmartCaptureAnalysisHandoff: vi.fn().mockResolvedValue(snapshot())
    };
    const transport = { submitSmartCaptureCodexPrompt: vi.fn().mockResolvedValue(true) };
    const intervalSpy = vi.spyOn(globalThis, "setInterval");
    const controller = createFineJobSmartCaptureCodexController({
      codexStore,
      getBackendOrigin: vi.fn().mockResolvedValue("http://127.0.0.1:8000"),
      createEventSource,
      getAnalysisSnapshot,
      handoffDependencies: { client, transport }
    });

    await controller.start();
    const currentSource = sources.get("http://127.0.0.1:8000/api/fine-job/smart-captures/current/events");
    currentSource?.onmessage?.({ data: JSON.stringify(snapshot().smart_capture) } as MessageEvent<string>);
    await vi.waitFor(() => expect(codexStore.startSmartCapture).toHaveBeenCalledTimes(1));

    const detailSource = sources.get(
      "http://127.0.0.1:8000/api/fine-job/smart-captures/smart-capture-controller-1/events"
    );
    detailSource?.onmessage?.({ data: JSON.stringify(snapshot().smart_capture) } as MessageEvent<string>);
    await Promise.resolve();

    expect(getAnalysisSnapshot).toHaveBeenCalledTimes(1);
    expect(intervalSpy).not.toHaveBeenCalled();
    controller.stop();
    intervalSpy.mockRestore();
  });
});
