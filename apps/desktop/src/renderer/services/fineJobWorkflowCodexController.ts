import type { FineJobSmartCaptureAnalysisSnapshot, FineJobWorkflowRun } from "@/types";

import { api } from "./api";
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

const needsPrefetchProgress = (run: FineJobWorkflowRun) => {
  const prefetch = run.telemetry?.prefetch;
  return Boolean(
    run.analysis_handoff?.attempt_status === "started"
      || (prefetch && ["preparing", "ready"].includes(String(prefetch.status)))
  );
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
      let refreshed = run;
      // Codex ACK started 后由同一 App-level 轮询驱动旁路 JD，后端仍保持当前正式 Run 状态。
      if (needsPrefetchProgress(run)) {
        refreshed = await api.advanceFineJobWorkflowRun(run.workflow_run_id);
      }
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
  intervalMs?: number;
  getCurrentSmartCapture?: () => Promise<{ smart_capture: Awaited<ReturnType<typeof api.getFineJobSmartCapture>> | null }>;
  getAnalysisSnapshot?: (smartCaptureId: string) => Promise<FineJobSmartCaptureAnalysisSnapshot>;
  handoffDependencies?: Parameters<typeof triggerSmartCaptureCodexHandoff>[3];
};

export const createFineJobSmartCaptureCodexController = (
  dependencies: SmartCaptureControllerDependencies
) => {
  let timer: ReturnType<typeof setInterval> | null = null;
  let polling = false;

  const tick = async () => {
    if (polling || (dependencies.isActive && !dependencies.isActive())) return;
    polling = true;
    try {
      const current = await (dependencies.getCurrentSmartCapture
        ? dependencies.getCurrentSmartCapture()
        : api.getCurrentFineJobSmartCapture());
      const capture = current.smart_capture;
      if (!capture || ["completed", "stopped", "failed"].includes(capture.status)) return;
      const snapshot = await (dependencies.getAnalysisSnapshot
        ? dependencies.getAnalysisSnapshot(capture.smart_capture_id)
        : api.getFineJobSmartCaptureAnalysisSnapshot(capture.smart_capture_id));
      if (!isAutoSmartCaptureCodexHandoffReady(snapshot)) return;
      await triggerSmartCaptureCodexHandoff(snapshot, dependencies.codexStore, "auto", dependencies.handoffDependencies);
    } finally {
      polling = false;
    }
  };

  const start = () => {
    if (timer) return;
    void tick().catch(() => undefined);
    timer = setInterval(() => { void tick().catch(() => undefined); }, dependencies.intervalMs ?? 1_200);
  };

  const stop = () => {
    if (!timer) return;
    clearInterval(timer);
    timer = null;
  };

  return { start, stop, tick };
};

export const startFineJobSmartCaptureCodexController = (
  dependencies: Omit<SmartCaptureControllerDependencies, "getCurrentSmartCapture" | "getAnalysisSnapshot">
) => createFineJobSmartCaptureCodexController({
  ...dependencies,
  getCurrentSmartCapture: () => api.getCurrentFineJobSmartCapture(),
  getAnalysisSnapshot: (smartCaptureId) => api.getFineJobSmartCaptureAnalysisSnapshot(smartCaptureId)
});
