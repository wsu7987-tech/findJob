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

export const createFineJobWorkflowCodexController = (dependencies: ControllerDependencies) => {
  let timer: ReturnType<typeof setInterval> | null = null;
  let polling = false;

  const tick = async () => {
    if (polling) return;
    polling = true;
    try {
      const run = await dependencies.getLatestRun();
      if (!run) return;
      dependencies.workflowStore.setRun(run);
      // 只检查当前已 ready 的 Analysis Batch，不推进 JD 或其他 Workflow Step。
      const result = await triggerWorkflowCodexHandoff(
        run, dependencies.codexStore, "auto", dependencies.handoffDependencies
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
