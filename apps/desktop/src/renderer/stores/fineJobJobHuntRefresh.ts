import { defineStore } from "pinia";
import { computed, ref } from "vue";

import { ApiError, NetworkError, api } from "@/services/api";
import type {
  FineJobJobHuntRefreshContext,
  FineJobJobHuntRefreshScope,
  FineJobJobHuntRefreshRun,
  FineJobJobHuntRefreshWorkflowOptions
} from "@/types";


const defaultWorkflows = (): FineJobJobHuntRefreshWorkflowOptions => ({
  refresh_chat_list: true,
  refresh_chat_messages: true,
  refresh_related_jobs: true,
  analyze_conversations: false,
  generate_missing_suggestions: false
});

const isActive = (run: FineJobJobHuntRefreshRun | null) =>
  run?.status === "pending" || run?.status === "running";

export const useFineJobJobHuntRefreshStore = defineStore("fine-job-job-hunt-refresh", () => {
  const context = ref<FineJobJobHuntRefreshContext | null>(null);
  const selectedSinceTime = ref("");
  const workflowOptions = ref(defaultWorkflows());
  const scope = ref<FineJobJobHuntRefreshScope | null>(null);
  const currentRun = ref<FineJobJobHuntRefreshRun | null>(null);
  const recentRuns = ref<FineJobJobHuntRefreshRun[]>([]);
  const loading = ref(false);
  const discovering = ref(false);
  const starting = ref(false);
  const error = ref<string | null>(null);
  let progressTimer: number | null = null;

  const hasExecutableWorkflow = computed(() =>
    workflowOptions.value.refresh_chat_messages
      || workflowOptions.value.refresh_related_jobs
  );

  const load = async () => {
    loading.value = true;
    error.value = null;
    try {
      const [contextResult, runsResult] = await Promise.all([
        api.getFineJobJobHuntRefreshContext(),
        api.listFineJobJobHuntRefreshRuns()
      ]);
      context.value = contextResult;
      recentRuns.value = runsResult.runs;
      if (contextResult.latest_unconsumed_scope_id) {
        scope.value = await api.getFineJobJobHuntRefreshScope(
          contextResult.latest_unconsumed_scope_id
        );
        selectedSinceTime.value = scope.value.selected_since_time;
      } else if (!selectedSinceTime.value) {
        selectedSinceTime.value = contextResult.default_since_time;
      }
      currentRun.value = runsResult.runs[0] ?? null;
      if (isActive(currentRun.value)) startProgressReading();
    } catch (value) {
      error.value = mapError(value);
      throw value;
    } finally {
      loading.value = false;
    }
  };

  const discoverScope = async () => {
    discovering.value = true;
    error.value = null;
    try {
      scope.value = await api.discoverFineJobJobHuntRefreshScope(selectedSinceTime.value);
      return scope.value;
    } catch (value) {
      error.value = mapError(value);
      throw value;
    } finally {
      discovering.value = false;
    }
  };

  const createRun = async () => {
    if (!scope.value || scope.value.selected_since_time !== selectedSinceTime.value) {
      throw new Error("请先按当前时间获取更新范围。");
    }
    if (!hasExecutableWorkflow.value) throw new Error("请至少选择一个可执行工作流。");
    starting.value = true;
    error.value = null;
    try {
      const created = await api.createFineJobJobHuntRefreshRun({
        scope_id: scope.value.id,
        workflow_options: { ...workflowOptions.value },
        trigger_source: "page"
      });
      currentRun.value = created;
      recentRuns.value = [
        created,
        ...recentRuns.value.filter((item) => item.id !== created.id)
      ].slice(0, 10);
      startProgressReading();
      return created;
    } catch (value) {
      error.value = mapError(value);
      throw value;
    } finally {
      starting.value = false;
    }
  };

  const selectRun = async (runId: string) => {
    currentRun.value = await api.getFineJobJobHuntRefreshRun(runId);
    if (isActive(currentRun.value)) startProgressReading();
    else stopProgressReading();
    return currentRun.value;
  };

  const attachCodexSession = async (runId: string, codexSessionRef: string) => {
    currentRun.value = await api.attachFineJobJobHuntRefreshCodexSession(
      runId,
      codexSessionRef
    );
    return currentRun.value;
  };

  const refreshCurrentRun = async () => {
    if (!currentRun.value) return null;
    const refreshed = await api.getFineJobJobHuntRefreshRun(currentRun.value.id);
    currentRun.value = refreshed;
    const index = recentRuns.value.findIndex((item) => item.id === refreshed.id);
    if (index >= 0) recentRuns.value[index] = refreshed;
    if (!isActive(refreshed)) stopProgressReading();
    return refreshed;
  };

  const startProgressReading = () => {
    stopProgressReading();
    if (!isActive(currentRun.value)) return;
    progressTimer = window.setInterval(() => {
      void refreshCurrentRun().catch((value) => {
        error.value = mapError(value);
        stopProgressReading();
      });
    }, 2_000);
  };

  const stopProgressReading = () => {
    if (progressTimer !== null) window.clearInterval(progressTimer);
    progressTimer = null;
  };

  const invalidateScope = () => {
    scope.value = null;
  };

  return {
    context,
    selectedSinceTime,
    workflowOptions,
    scope,
    currentRun,
    recentRuns,
    loading,
    discovering,
    starting,
    error,
    hasExecutableWorkflow,
    load,
    discoverScope,
    createRun,
    selectRun,
    attachCodexSession,
    refreshCurrentRun,
    startProgressReading,
    stopProgressReading,
    invalidateScope
  };
});

const mapError = (value: unknown) => {
  if (value instanceof ApiError || value instanceof NetworkError) return value.message;
  return (value as Error).message || "求职数据更新操作失败。";
};
