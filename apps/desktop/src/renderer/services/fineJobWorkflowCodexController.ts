import type { FineJobSmartCapture, FineJobSmartCaptureAnalysisSnapshot, FineJobWorkflowRun } from "@/types";

import { api, getBackendOrigin } from "./api";
import {
  isAutoSmartCaptureCodexHandoffReady,
  triggerSmartCaptureCodexHandoff,
  triggerWorkflowCodexHandoff
} from "./workflowCodexHandoff";

type WorkflowRunStore = {
  setRun: (run: FineJobWorkflowRun | null) => FineJobWorkflowRun | null;
};

type CodexStore = {
  load: () => Promise<void>;
  status: string;
  runtimeId: string | null;
  sessionRef: string | null;
  startWorkflow: Parameters<typeof triggerWorkflowCodexHandoff>[1]["startWorkflow"];
};

type ControllerDependencies = {
  getLatestRun: () => Promise<FineJobWorkflowRun | null>;
  workflowStore: WorkflowRunStore;
  codexStore: CodexStore;
  executionAuthority?: "workflow_legacy" | "smart_capture";
  isActive?: () => boolean;
  intervalMs?: number;
  handoffDependencies?: Parameters<typeof triggerWorkflowCodexHandoff>[3];
};

export class WorkflowControllerCutoverError extends Error {
  constructor() {
    super("Cutover 后 App-level Workflow controller 不能驱动 live Pipeline。");
    this.name = "WorkflowControllerCutoverError";
  }
}

export const assertWorkflowControllerAuthority = (
  executionAuthority: ControllerDependencies["executionAuthority"] = "workflow_legacy"
) => {
  if (executionAuthority === "smart_capture") {
    throw new WorkflowControllerCutoverError();
  }
};

export const createFineJobWorkflowCodexController = (dependencies: ControllerDependencies) => {
  let timer: ReturnType<typeof setInterval> | null = null;
  let polling = false;

  const tick = async () => {
    // Cutover 后旧 controller 只能停止，不能借 latest Workflow fallback 继续执行。
    assertWorkflowControllerAuthority(dependencies.executionAuthority);
    // 控制器只在当前 Workflow 已明确进入运行流程后访问后端。
    if (polling || (dependencies.isActive && !dependencies.isActive())) return;
    polling = true;
    try {
      const run = await dependencies.getLatestRun();
      if (!run || (dependencies.isActive && !dependencies.isActive())) return;
      const refreshed = run;
      if (dependencies.isActive && !dependencies.isActive()) return;
      dependencies.workflowStore.setRun(refreshed);
      // 只检查当前已 ready 的 Analysis Batch，不推进 JD 或其他 Workflow Step。
      const result = await triggerWorkflowCodexHandoff(
        refreshed, dependencies.codexStore, "auto", dependencies.handoffDependencies
      );
      if (dependencies.isActive && !dependencies.isActive()) return;
      dependencies.workflowStore.setRun(result.run);
    } finally {
      polling = false;
    }
  };

  const start = () => {
    if (timer) return;
    void dependencies.codexStore.load().catch(() => undefined).finally(tick);
    timer = setInterval(() => { void tick(); }, dependencies.intervalMs ?? 1_200);
  };

  const stop = () => {
    if (!timer) return;
    clearInterval(timer);
    timer = null;
  };

  return { start, stop, tick };
};

export const startFineJobWorkflowCodexController = (dependencies: Omit<ControllerDependencies, "getLatestRun">) =>
  createFineJobWorkflowCodexController({
    ...dependencies,
    getLatestRun: async () => (await api.getLatestFineJobWorkflowRun()).workflow_run
  });

type SmartCaptureControllerDependencies = {
  codexStore: Parameters<typeof triggerSmartCaptureCodexHandoff>[1];
  isActive?: () => boolean;
  getCurrentSmartCapture?: () => Promise<{ smart_capture: Awaited<ReturnType<typeof api.getFineJobSmartCapture>> | null }>;
  getAnalysisSnapshot?: (smartCaptureId: string) => Promise<FineJobSmartCaptureAnalysisSnapshot>;
  getBackendOrigin?: () => Promise<string>;
  createEventSource?: (url: string) => EventSource;
  handoffDependencies?: Parameters<typeof triggerSmartCaptureCodexHandoff>[3];
};

export const createFineJobSmartCaptureCodexController = (
  dependencies: SmartCaptureControllerDependencies
) => {
  let started = false;
  let backendOrigin = "";
  let currentSource: EventSource | null = null;
  let detailSource: EventSource | null = null;
  let detailCaptureId = "";
  let inspecting = false;
  let pendingInspection: { capture: FineJobSmartCapture; force: boolean } | null = null;
  const inspectedVersions = new Map<string, number>();
  const attemptedAnalysisBatches = new Set<string>();

  const isActive = () => !dependencies.isActive || dependencies.isActive();
  const isTerminal = (capture: FineJobSmartCapture) =>
    ["completed", "stopped", "failed"].includes(capture.status);
  const needsCodexInspection = (capture: FineJobSmartCapture) =>
    !isTerminal(capture) && (capture.stage === "waiting_codex" || capture.waiting_reason === "codex");
  const parseCapture = (event: MessageEvent<string>) => {
    try {
      return JSON.parse(event.data) as FineJobSmartCapture;
    } catch {
      return null;
    }
  };

  const inspectCapture = async (capture: FineJobSmartCapture, force = false): Promise<void> => {
    if (!isActive() || !needsCodexInspection(capture)) return;
    if (inspecting) {
      // 合并并发事件，只保留最新版本，避免同一状态重复读取 Analysis Snapshot。
      if (!pendingInspection || capture.state_version >= pendingInspection.capture.state_version) {
        pendingInspection = { capture, force: force || pendingInspection?.force === true };
      }
      return;
    }
    const inspectedVersion = inspectedVersions.get(capture.smart_capture_id);
    if (!force && inspectedVersion !== undefined && inspectedVersion >= capture.state_version) return;

    inspecting = true;
    inspectedVersions.set(capture.smart_capture_id, capture.state_version);
    try {
      const snapshot = await (dependencies.getAnalysisSnapshot
        ? dependencies.getAnalysisSnapshot(capture.smart_capture_id)
        : api.getFineJobSmartCaptureAnalysisSnapshot(capture.smart_capture_id));
      if (!isAutoSmartCaptureCodexHandoffReady(snapshot)) return;
      const batchKey = `${snapshot.smart_capture_id}:${snapshot.analysis_batch_id}`;
      if (attemptedAnalysisBatches.has(batchKey)) return;
      const result = await triggerSmartCaptureCodexHandoff(
        snapshot, dependencies.codexStore, "auto", dependencies.handoffDependencies
      );
      // Codex 忙碌时允许在其恢复 idle 后重试；其余结果等待新的 Analysis Batch。
      if (result.status !== "busy") attemptedAnalysisBatches.add(batchKey);
    } catch (error) {
      if (inspectedVersions.get(capture.smart_capture_id) === capture.state_version) {
        inspectedVersions.delete(capture.smart_capture_id);
      }
      throw error;
    } finally {
      inspecting = false;
      const next = pendingInspection;
      pendingInspection = null;
      if (next) await inspectCapture(next.capture, next.force);
    }
  };

  const closeDetailSource = () => {
    detailSource?.close();
    detailSource = null;
    detailCaptureId = "";
  };

  const applyCapture = async (capture: FineJobSmartCapture, fromDetail = false) => {
    if (!isActive()) return;
    if (isTerminal(capture)) {
      if (capture.smart_capture_id === detailCaptureId) closeDetailSource();
      return;
    }
    if (started && backendOrigin && !fromDetail && detailCaptureId !== capture.smart_capture_id) {
      closeDetailSource();
      detailCaptureId = capture.smart_capture_id;
      const sourceFactory = dependencies.createEventSource ?? ((url: string) => new EventSource(url));
      detailSource = sourceFactory(
        `${backendOrigin}/api/fine-job/smart-captures/${encodeURIComponent(capture.smart_capture_id)}/events`
      );
      detailSource.onmessage = (event) => {
        const next = parseCapture(event);
        if (next) void applyCapture(next, true).catch(() => undefined);
      };
    }
    await inspectCapture(capture);
  };

  const tick = async () => {
    if (!isActive()) return;
    const current = await (dependencies.getCurrentSmartCapture
      ? dependencies.getCurrentSmartCapture()
      : api.getCurrentFineJobSmartCapture());
    if (current.smart_capture) await inspectCapture(current.smart_capture, true);
  };

  const start = async () => {
    if (started) return;
    started = true;
    backendOrigin = await (dependencies.getBackendOrigin ?? getBackendOrigin)();
    if (!started) return;
    const sourceFactory = dependencies.createEventSource
      ?? (typeof EventSource === "undefined" ? null : (url: string) => new EventSource(url));
    if (!sourceFactory) {
      await tick();
      return;
    }
    // current SSE 负责身份切换，detail SSE 负责该任务后续状态变化。
    currentSource = sourceFactory(`${backendOrigin}/api/fine-job/smart-captures/current/events`);
    currentSource.onmessage = (event) => {
      const capture = parseCapture(event);
      if (capture) void applyCapture(capture).catch(() => undefined);
    };
  };

  const stop = () => {
    started = false;
    currentSource?.close();
    currentSource = null;
    closeDetailSource();
  };

  return { start, stop, tick };
};

export const startFineJobSmartCaptureCodexController = (
  dependencies: Omit<SmartCaptureControllerDependencies, "getCurrentSmartCapture" | "getAnalysisSnapshot" | "getBackendOrigin">
) => createFineJobSmartCaptureCodexController({
  ...dependencies,
  getCurrentSmartCapture: () => api.getCurrentFineJobSmartCapture(),
  getAnalysisSnapshot: (smartCaptureId) => api.getFineJobSmartCaptureAnalysisSnapshot(smartCaptureId),
  getBackendOrigin
});
