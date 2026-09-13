import type { FineJobWorkflowRun } from "@/types";

import { api } from "./api";
import { triggerWorkflowCodexHandoff } from "./workflowCodexHandoff";

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
  intervalMs?: number;
  handoffDependencies?: Parameters<typeof triggerWorkflowCodexHandoff>[3];
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
    if (polling) return;
    polling = true;
    try {
      const run = await dependencies.getLatestRun();
      if (!run) return;
      let refreshed = run;
      // Codex ACK started 后由同一 App-level 轮询驱动旁路 JD，后端仍保持当前正式 Run 状态。
      if (needsPrefetchProgress(run)) {
        refreshed = await api.advanceFineJobWorkflowRun(run.workflow_run_id);
      }
      dependencies.workflowStore.setRun(refreshed);
      // 只检查当前已 ready 的 Analysis Batch，不推进 JD 或其他 Workflow Step。
      const result = await triggerWorkflowCodexHandoff(
        refreshed, dependencies.codexStore, "auto", dependencies.handoffDependencies
      );
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
