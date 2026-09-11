import { defineStore } from "pinia";
import { ref } from "vue";

import { api } from "@/services/api";
import type { FineJobWorkflowRun } from "@/types";

const boundaryStatuses = new Set([
  "waiting_codex",
  "waiting_for_user",
  "paused",
  "cancelled",
  "completed",
  "completed_with_errors",
  "failed"
]);
const terminalStatuses = new Set(["paused", "cancelled", "completed", "completed_with_errors", "failed"]);

export const useFineJobWorkflowRunStore = defineStore("fine-job-workflow-run", () => {
  const currentRun = ref<FineJobWorkflowRun | null>(null);
  const loading = ref(false);
  const advancing = ref(false);
  const error = ref<string | null>(null);
  let pollingTimer: ReturnType<typeof setInterval> | null = null;

  const setRun = (run: FineJobWorkflowRun | null) => {
    currentRun.value = run;
    if (run && !terminalStatuses.has(run.status)) ensurePolling();
    if (!run || terminalStatuses.has(run.status)) stopPolling();
    return run;
  };

  const refresh = async (workflowRunId = currentRun.value?.workflow_run_id) => {
    if (!workflowRunId) return null;
    try {
      return setRun(await api.getFineJobWorkflowRun(workflowRunId));
    } catch (value) {
      error.value = value instanceof Error ? value.message : String(value);
      throw value;
    }
  };

  const advance = async () => {
    const run = currentRun.value;
    if (!run || boundaryStatuses.has(run.status) || advancing.value) return run;
    advancing.value = true;
    try {
      return setRun(await api.advanceFineJobWorkflowRun(run.workflow_run_id));
    } catch (value) {
      error.value = value instanceof Error ? value.message : String(value);
      throw value;
    } finally {
      advancing.value = false;
    }
  };

  const tick = async () => {
    const run = currentRun.value;
    if (!run || terminalStatuses.has(run.status) || advancing.value) return;
    const refreshed = await refresh(run.workflow_run_id);
    if (refreshed && !boundaryStatuses.has(refreshed.status)) await advance();
  };

  const ensurePolling = () => {
    if (pollingTimer) return;
    pollingTimer = setInterval(() => { void tick(); }, 1200);
  };

  const stopPolling = () => {
    if (!pollingTimer) return;
    clearInterval(pollingTimer);
    pollingTimer = null;
  };

  const create = async (payload: Parameters<typeof api.createFineJobDeepJobSearchRun>[0]) => {
    loading.value = true;
    error.value = null;
    try {
      setRun(await api.createFineJobDeepJobSearchRun(payload));
      await advance();
      return currentRun.value;
    } finally {
      loading.value = false;
    }
  };

  const restoreLatest = async () => {
    loading.value = true;
    error.value = null;
    try {
      const response = await api.getLatestFineJobWorkflowRun();
      return setRun(response.workflow_run);
    } finally {
      loading.value = false;
    }
  };

  const pause = async () => {
    if (!currentRun.value) return null;
    return setRun(await api.pauseFineJobWorkflowRun(currentRun.value.workflow_run_id));
  };

  const resume = async () => {
    if (!currentRun.value) return null;
    setRun(await api.resumeFineJobWorkflowRun(currentRun.value.workflow_run_id));
    await advance();
    return currentRun.value;
  };

  const cancel = async () => {
    if (!currentRun.value) return null;
    return setRun(await api.cancelFineJobWorkflowRun(currentRun.value.workflow_run_id));
  };

  return {
    currentRun,
    loading,
    advancing,
    error,
    setRun,
    refresh,
    advance,
    create,
    restoreLatest,
    pause,
    resume,
    cancel,
    ensurePolling,
    stopPolling
  };
});
