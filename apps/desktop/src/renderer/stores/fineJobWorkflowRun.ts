import { defineStore } from "pinia";
import { ref } from "vue";

import { api, getBackendOrigin } from "@/services/api";
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
const terminalStatuses = new Set(["cancelled", "completed", "completed_with_errors", "failed"]);

export const useFineJobWorkflowRunStore = defineStore("fine-job-workflow-run", () => {
  const currentRun = ref<FineJobWorkflowRun | null>(null);
  const loading = ref(false);
  const advancing = ref(false);
  const error = ref<string | null>(null);
  const pollingActive = ref(false);
  const streamActive = ref(false);
  let eventSource: EventSource | null = null;
  let subscribedRunId = "";

  const stopPolling = () => {
    pollingActive.value = false;
    streamActive.value = false;
    subscribedRunId = "";
    eventSource?.close();
    eventSource = null;
  };

  const ensurePolling = () => {
    const workflowRunId = currentRun.value?.workflow_run_id;
    if (!pollingActive.value || !workflowRunId || terminalStatuses.has(currentRun.value?.status || "")) return;
    if (eventSource && subscribedRunId === workflowRunId) return;
    eventSource?.close();
    eventSource = null;
    subscribedRunId = workflowRunId;
    void getBackendOrigin().then((origin) => {
      if (!pollingActive.value || subscribedRunId !== workflowRunId) return;
      const eventUrl = new URL(`/api/fine-job/workflow-runs/${workflowRunId}/events`, origin).toString();
      const source = new EventSource(eventUrl);
      eventSource = source;
      source.onopen = () => {
        streamActive.value = true;
        error.value = null;
      };
      source.onmessage = (event) => {
        try {
          setRun(JSON.parse(event.data) as FineJobWorkflowRun);
        } catch (value) {
          error.value = value instanceof Error ? value.message : String(value);
        }
      };
      source.onerror = () => {
        // EventSource 会自动重连；重连后服务端会先发送当前完整快照。
        streamActive.value = false;
      };
    }).catch((value) => {
      error.value = value instanceof Error ? value.message : String(value);
    });
  };

  const setRun = (run: FineJobWorkflowRun | null) => {
    currentRun.value = run;
    if (!run || terminalStatuses.has(run.status)) stopPolling();
    if (run && pollingActive.value && !terminalStatuses.has(run.status)) ensurePolling();
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

  const startPolling = () => {
    // 保留旧方法名，调用方无需改动；实际建立的是 SSE 状态订阅。
    pollingActive.value = true;
    if (currentRun.value && !terminalStatuses.has(currentRun.value.status)) ensurePolling();
  };

  const create = async (
    payload: Parameters<typeof api.createFineJobDeepJobSearchRun>[0],
    createdFrom: "task_cockpit" | "boss_capture" = "task_cockpit"
  ) => {
    loading.value = true;
    error.value = null;
    try {
      setRun(await api.createFineJobDeepJobSearchRun(payload, createdFrom));
      startPolling();
      if (currentRun.value?.status !== "pending") return currentRun.value;
      const child = currentRun.value?.children?.find((item) => item.status === "pending");
      if (!child || !currentRun.value) throw new Error("Workflow Run 缺少可启动的岗位采集子任务。");
      setRun(await api.startFineJobWorkflowChild(currentRun.value.workflow_run_id, child.child_relation_id));
      return currentRun.value;
    } finally {
      loading.value = false;
    }
  };

  const restoreLatest = async (
    includeCompleted = false,
    createdFrom?: "task_cockpit" | "boss_capture"
  ) => {
    loading.value = true;
    error.value = null;
    try {
      const response = await api.getLatestFineJobWorkflowRun(includeCompleted, createdFrom);
      const run = setRun(response.workflow_run);
      if (run && !terminalStatuses.has(run.status)) startPolling();
      return run;
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
    startPolling();
    return currentRun.value;
  };

  const cancel = async () => {
    if (!currentRun.value) return null;
    return setRun(await api.cancelFineJobWorkflowRun(currentRun.value.workflow_run_id));
  };

  const decideChild = async (decision: "skip" | "end") => {
    const run = currentRun.value;
    if (!run || !["child_cancelled_waiting_decision", "child_failed_waiting_decision"].includes(run.control_state)) {
      return null;
    }
    const child = run?.children?.find((item) =>
      item.control_state === run.control_state
    );
    if (!child) return null;
    return setRun(await api.decideFineJobWorkflowChild(run.workflow_run_id, child.child_relation_id, decision));
  };

  return {
    currentRun,
    pollingActive,
    streamActive,
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
    decideChild,
    startPolling,
    ensurePolling,
    stopPolling
  };
});
