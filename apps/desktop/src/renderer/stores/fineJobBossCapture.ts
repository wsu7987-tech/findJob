import { defineStore } from "pinia";
import { ref } from "vue";

import { ApiError, NetworkError, api } from "@/services/api";
import type {
  FineJobBossBrowserStatus,
  FineJobBossCaptureRequest,
  FineJobBossCaptureTask,
  FineJobBossCity,
  FineJobBossSearchPageRequest
} from "@/types";

export const useFineJobBossCaptureStore = defineStore("fineJobBossCapture", () => {
  const status = ref<FineJobBossBrowserStatus | null>(null);
  const cities = ref<FineJobBossCity[]>([]);
  const task = ref<FineJobBossCaptureTask | null>(null);
  const loadingStatus = ref(false);
  const loadingCities = ref(false);
  const starting = ref(false);
  const stopping = ref(false);
  const locating = ref(false);
  const capturing = ref(false);
  const stoppingCapture = ref(false);
  const suggesting = ref(false);
  const error = ref<string | null>(null);
  const syncError = ref<string | null>(null);
  const syncState = ref<"idle" | "syncing" | "retrying">("idle");
  let pollTimer: ReturnType<typeof setTimeout> | null = null;
  let pollGeneration = 0;
  let refreshGeneration = 0;
  let pollFailures = 0;
  let taskGeneration = 0;
  const actionGenerations: Record<string, number> = {};

  const loadStatus = async () => {
    const requestId = (actionGenerations.loadingStatus ?? 0) + 1;
    actionGenerations.loadingStatus = requestId;
    const ownsRequest = () => actionGenerations.loadingStatus === requestId;
    loadingStatus.value = true;
    error.value = null;
    try {
      const response = await api.getFineJobBossBrowserStatus();
      if (!ownsRequest()) return null;
      status.value = response;
      return status.value;
    } catch (errorValue) {
      if (ownsRequest()) error.value = mapError(errorValue);
      return null;
    } finally {
      if (ownsRequest()) loadingStatus.value = false;
    }
  };

  const loadCities = async () => {
    const requestId = (actionGenerations.loadingCities ?? 0) + 1;
    actionGenerations.loadingCities = requestId;
    const ownsRequest = () => actionGenerations.loadingCities === requestId;
    loadingCities.value = true;
    error.value = null;
    try {
      const response = await api.listFineJobBossCities();
      if (!ownsRequest()) return [];
      cities.value = response.cities;
      return response.cities;
    } catch (errorValue) {
      if (ownsRequest()) error.value = mapError(errorValue);
      return [];
    } finally {
      if (ownsRequest()) loadingCities.value = false;
    }
  };

  const startBrowser = async () => {
    const requestId = (actionGenerations.starting ?? 0) + 1;
    actionGenerations.starting = requestId;
    const ownsRequest = () => actionGenerations.starting === requestId;
    starting.value = true;
    error.value = null;
    try {
      const response = await api.startFineJobBossBrowser();
      if (!ownsRequest()) return null;
      status.value = response;
      return status.value;
    } catch (errorValue) {
      if (ownsRequest()) error.value = mapError(errorValue);
      throw errorValue;
    } finally {
      if (ownsRequest()) starting.value = false;
    }
  };

  const stopBrowser = async () => {
    const requestId = (actionGenerations.stopping ?? 0) + 1;
    actionGenerations.stopping = requestId;
    const ownsRequest = () => actionGenerations.stopping === requestId;
    stopping.value = true;
    error.value = null;
    try {
      const response = await api.stopFineJobBossBrowser();
      if (!ownsRequest()) return null;
      status.value = response;
      return status.value;
    } catch (errorValue) {
      if (ownsRequest()) error.value = mapError(errorValue);
      throw errorValue;
    } finally {
      if (ownsRequest()) stopping.value = false;
    }
  };

  const locate = async (payload: FineJobBossSearchPageRequest) => {
    const requestId = (actionGenerations.locating ?? 0) + 1;
    actionGenerations.locating = requestId;
    const ownsRequest = () => actionGenerations.locating === requestId;
    locating.value = true;
    error.value = null;
    try {
      const response = await api.locateFineJobBossSearchPage(payload);
      if (!ownsRequest()) return null;
      status.value = response.status;
      return response;
    } catch (errorValue) {
      if (ownsRequest()) error.value = mapError(errorValue);
      throw errorValue;
    } finally {
      if (ownsRequest()) locating.value = false;
    }
  };

  const capture = async (payload: FineJobBossCaptureRequest) => {
    const requestId = (actionGenerations.capturing ?? 0) + 1;
    actionGenerations.capturing = requestId;
    taskGeneration += 1;
    stopPolling();
    const ownerGeneration = taskGeneration;
    const ownerId = task.value?.id;
    const ownsRequest = () => actionGenerations.capturing === requestId && ownerGeneration === taskGeneration;
    capturing.value = true;
    error.value = null;
    try {
      const response = await api.captureFineJobBossJobs(payload);
      if (!ownsRequest()) return null;
      task.value = response;
      startPolling(task.value.id);
      return task.value;
    } catch (errorValue) {
      if (ownsRequest()) error.value = mapError(errorValue);
      throw errorValue;
    } finally {
      if (ownsRequest()) capturing.value = false;
    }
  };

  const continueCapture = async (pages: number) => {
    if (!task.value) return null;
    const requestId = (actionGenerations.capturing ?? 0) + 1;
    actionGenerations.capturing = requestId;
    const ownerGeneration = taskGeneration;
    const ownerId = task.value?.id;
    const ownsRequest = () => actionGenerations.capturing === requestId && ownerGeneration === taskGeneration && (task.value?.id === ownerId);
    capturing.value = true;
    error.value = null;
    try {
      const response = await api.continueFineJobBossCapture(task.value.id, pages);
      if (!ownsRequest()) return null;
      task.value = response;
      startPolling(task.value.id);
      return task.value;
    } catch (errorValue) {
      if (ownsRequest()) error.value = mapError(errorValue);
      throw errorValue;
    } finally {
      if (ownsRequest()) capturing.value = false;
    }
  };

  const stopCaptureTask = async () => {
    if (!task.value) return null;
    const requestId = (actionGenerations.stoppingCapture ?? 0) + 1;
    actionGenerations.stoppingCapture = requestId;
    const ownerGeneration = taskGeneration;
    const ownerId = task.value?.id;
    const ownsRequest = () => actionGenerations.stoppingCapture === requestId && ownerGeneration === taskGeneration && (task.value?.id === ownerId);
    stoppingCapture.value = true;
    error.value = null;
    try {
      const response = await api.stopFineJobBossCaptureTask(task.value.id);
      if (!ownsRequest()) return null;
      task.value = response;
      startPolling(task.value.id);
      return task.value;
    } catch (errorValue) {
      if (ownsRequest()) error.value = mapError(errorValue);
      if (ownsRequest()) await refreshTask(ownerId);
      throw errorValue;
    } finally {
      if (ownsRequest()) stoppingCapture.value = false;
    }
  };

  const captureDetails = async (jobIds: string[], force = false) => {
    if (!task.value) return null;
    const requestId = (actionGenerations.capturing ?? 0) + 1;
    actionGenerations.capturing = requestId;
    const ownerGeneration = taskGeneration;
    const ownerId = task.value?.id;
    const ownsRequest = () => actionGenerations.capturing === requestId && ownerGeneration === taskGeneration && (task.value?.id === ownerId);
    capturing.value = true;
    error.value = null;
    try {
      const response = await api.captureSelectedFineJobBossDetails(task.value.id, jobIds, force);
      if (!ownsRequest()) return null;
      task.value = response;
      startPolling(task.value.id);
      return task.value;
    } catch (errorValue) {
      if (ownsRequest()) error.value = mapError(errorValue);
      throw errorValue;
    } finally {
      if (ownsRequest()) capturing.value = false;
    }
  };

  const suggest = async (
    mode: "strategy" | "ai",
    command = "",
    options: {
      filterStrategyId?: string | null;
      recommendationStrategyId?: string | null;
      contextStaleAction?: "regenerate" | "use_current" | "cancel";
    } = {}
  ) => {
    if (!task.value) return [];
    const requestId = (actionGenerations.suggesting ?? 0) + 1;
    actionGenerations.suggesting = requestId;
    const ownerGeneration = taskGeneration;
    const ownerId = task.value?.id;
    const ownsRequest = () => actionGenerations.suggesting === requestId && ownerGeneration === taskGeneration && (task.value?.id === ownerId);
    suggesting.value = true;
    error.value = null;
    try {
      const response = await api.suggestFineJobBossDetails(task.value.id, {
        mode,
        command,
        filter_strategy_id: options.filterStrategyId,
        recommendation_strategy_id: options.recommendationStrategyId,
        extra_requirement: command,
        context_stale_action: options.contextStaleAction
      });
      if (!ownsRequest()) return [];
      task.value = response.task;
      return response.selected_job_ids;
    } catch (errorValue) {
      if (ownsRequest()) error.value = mapError(errorValue);
      throw errorValue;
    } finally {
      if (ownsRequest()) suggesting.value = false;
    }
  };

  const applyFilter = async (strategyId: string) => {
    if (!task.value) return [];
    const requestId = (actionGenerations.suggesting ?? 0) + 1;
    actionGenerations.suggesting = requestId;
    const ownerGeneration = taskGeneration;
    const ownerId = task.value?.id;
    const ownsRequest = () => actionGenerations.suggesting === requestId && ownerGeneration === taskGeneration && (task.value?.id === ownerId);
    suggesting.value = true;
    error.value = null;
    try {
      const response = await api.applyFineJobBossFilter(task.value.id, strategyId);
      if (!ownsRequest()) return [];
      task.value = response.task;
      return response.selected_job_ids;
    } catch (errorValue) {
      if (ownsRequest()) error.value = mapError(errorValue);
      throw errorValue;
    } finally {
      if (ownsRequest()) suggesting.value = false;
    }
  };

  const evaluateDeliveries = async (
    recommendationStrategyId: string,
    filterStrategyId?: string | null,
    extraRequirement = "",
    jobIds?: string[],
    contextStaleAction?: "regenerate" | "use_current" | "cancel"
  ) => {
    if (!task.value) return [];
    const requestId = (actionGenerations.suggesting ?? 0) + 1;
    actionGenerations.suggesting = requestId;
    const ownerGeneration = taskGeneration;
    const ownerId = task.value?.id;
    const ownsRequest = () => actionGenerations.suggesting === requestId && ownerGeneration === taskGeneration && (task.value?.id === ownerId);
    suggesting.value = true;
    error.value = null;
    try {
      const response = await api.evaluateFineJobBossDeliveries(task.value.id, {
        recommendation_strategy_id: recommendationStrategyId,
        filter_strategy_id: filterStrategyId,
        extra_requirement: extraRequirement,
        job_ids: jobIds,
        manual_override: true,
        context_stale_action: contextStaleAction
      });
      if (!ownsRequest()) return [];
      task.value = response.task;
      return response.evaluations;
    } catch (errorValue) {
      if (ownsRequest()) error.value = mapError(errorValue);
      throw errorValue;
    } finally {
      if (ownsRequest()) suggesting.value = false;
    }
  };

  const refreshTask = async (taskId = task.value?.id) => {
    if (!taskId) return null;
    const generation = pollGeneration;
    const requestId = ++refreshGeneration;
    const ownsRequest = () => generation === pollGeneration && requestId === refreshGeneration && (!task.value || task.value.id === taskId);
    syncState.value = "syncing";
    try {
      const next = await api.getFineJobBossCaptureTask(taskId);
      if (!ownsRequest()) return null;
      task.value = next;
      syncError.value = null;
      pollFailures = 0;
      syncState.value = "idle";
      return next;
    } catch (errorValue) {
      if (!ownsRequest()) return null;
      syncError.value = mapError(errorValue);
      const retryable = errorValue instanceof NetworkError || (errorValue instanceof ApiError && [500, 502, 503, 504].includes(errorValue.statusCode));
      if (retryable) {
        pollFailures += 1;
        syncState.value = "retrying";
      } else stopPolling();
      return null;
    }
  };

  const clearTask = () => {
    taskGeneration += 1;
    capturing.value = false; suggesting.value = false; stoppingCapture.value = false;
    stopPolling();
    task.value = null;
  };

  const setTask = (nextTask: FineJobBossCaptureTask | null) => {
    taskGeneration += 1;
    capturing.value = false; suggesting.value = false; stoppingCapture.value = false;
    stopPolling();
    task.value = nextTask;
    if (nextTask && (nextTask.status === "queued" || nextTask.status === "running")) {
      startPolling(nextTask.id);
    }
  };

  const startPolling = (taskId: string) => {
    stopPolling();
    const generation = pollGeneration;
    pollFailures = 0;
    const poll = async () => {
      const current = await refreshTask(taskId);
      // 清空、替换和卸载后，旧请求无权写回或安排下一轮。
      if (generation !== pollGeneration || task.value?.id !== taskId) return;
      if (current && current.status !== "queued" && current.status !== "running") {
        stopPolling();
        return;
      }
      const delay = pollFailures ? [1000, 2000, 5000, 10000, 15000][Math.min(pollFailures - 1, 4)] : 1000;
      pollTimer = globalThis.setTimeout(poll, delay);
    };
    pollTimer = globalThis.setTimeout(poll, 500);
  };

  const resumePolling = () => {
    if (task.value && (task.value.status === "queued" || task.value.status === "running")) {
      startPolling(task.value.id);
    }
  };

  const stopPolling = () => {
    pollGeneration += 1;
    refreshGeneration += 1;
    syncState.value = "idle";
    if (pollTimer != null) {
      globalThis.clearTimeout(pollTimer);
      pollTimer = null;
    }
  };

  return {
    status,
    cities,
    task,
    loadingStatus,
    loadingCities,
    starting,
    stopping,
    locating,
    capturing,
    stoppingCapture,
    suggesting,
    error,
    syncError,
    syncState,
    loadStatus,
    loadCities,
    startBrowser,
    stopBrowser,
    locate,
    capture,
    continueCapture,
    stopCaptureTask,
    captureDetails,
    suggest,
    applyFilter,
    evaluateDeliveries,
    refreshTask,
    clearTask,
    setTask,
    resumePolling,
    stopPolling
  };
});

const mapError = (errorValue: unknown) => {
  if (errorValue instanceof ApiError || errorValue instanceof NetworkError) {
    return errorValue.message;
  }
  return (errorValue as Error).message || "BOSS 岗位采集操作失败。";
};
