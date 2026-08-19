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
  const suggesting = ref(false);
  const error = ref<string | null>(null);
  let pollTimer: ReturnType<typeof setTimeout> | null = null;

  const loadStatus = async () => {
    loadingStatus.value = true;
    error.value = null;
    try {
      status.value = await api.getFineJobBossBrowserStatus();
      return status.value;
    } catch (errorValue) {
      error.value = mapError(errorValue);
      return null;
    } finally {
      loadingStatus.value = false;
    }
  };

  const loadCities = async () => {
    loadingCities.value = true;
    error.value = null;
    try {
      const response = await api.listFineJobBossCities();
      cities.value = response.cities;
      return response.cities;
    } catch (errorValue) {
      error.value = mapError(errorValue);
      return [];
    } finally {
      loadingCities.value = false;
    }
  };

  const startBrowser = async () => {
    starting.value = true;
    error.value = null;
    try {
      status.value = await api.startFineJobBossBrowser();
      return status.value;
    } catch (errorValue) {
      error.value = mapError(errorValue);
      throw errorValue;
    } finally {
      starting.value = false;
    }
  };

  const stopBrowser = async () => {
    stopping.value = true;
    error.value = null;
    try {
      status.value = await api.stopFineJobBossBrowser();
      return status.value;
    } catch (errorValue) {
      error.value = mapError(errorValue);
      throw errorValue;
    } finally {
      stopping.value = false;
    }
  };

  const locate = async (payload: FineJobBossSearchPageRequest) => {
    locating.value = true;
    error.value = null;
    try {
      const response = await api.locateFineJobBossSearchPage(payload);
      status.value = response.status;
      return response;
    } catch (errorValue) {
      error.value = mapError(errorValue);
      throw errorValue;
    } finally {
      locating.value = false;
    }
  };

  const capture = async (payload: FineJobBossCaptureRequest) => {
    capturing.value = true;
    error.value = null;
    try {
      task.value = await api.captureFineJobBossJobs(payload);
      startPolling(task.value.id);
      return task.value;
    } catch (errorValue) {
      error.value = mapError(errorValue);
      throw errorValue;
    } finally {
      capturing.value = false;
    }
  };

  const captureDetails = async (jobIds: string[], force = false) => {
    if (!task.value) return null;
    capturing.value = true;
    error.value = null;
    try {
      task.value = await api.captureSelectedFineJobBossDetails(task.value.id, jobIds, force);
      startPolling(task.value.id);
      return task.value;
    } catch (errorValue) {
      error.value = mapError(errorValue);
      throw errorValue;
    } finally {
      capturing.value = false;
    }
  };

  const suggest = async (
    mode: "strategy" | "ai",
    command = "",
    options: {
      filterStrategyId?: string | null;
      recommendationStrategyId?: string | null;
    } = {}
  ) => {
    if (!task.value) return [];
    suggesting.value = true;
    error.value = null;
    try {
      const response = await api.suggestFineJobBossDetails(task.value.id, {
        mode,
        command,
        filter_strategy_id: options.filterStrategyId,
        recommendation_strategy_id: options.recommendationStrategyId,
        extra_requirement: command
      });
      task.value = response.task;
      return response.selected_job_ids;
    } catch (errorValue) {
      error.value = mapError(errorValue);
      throw errorValue;
    } finally {
      suggesting.value = false;
    }
  };

  const applyFilter = async (strategyId: string) => {
    if (!task.value) return [];
    suggesting.value = true;
    error.value = null;
    try {
      const response = await api.applyFineJobBossFilter(task.value.id, strategyId);
      task.value = response.task;
      return response.selected_job_ids;
    } catch (errorValue) {
      error.value = mapError(errorValue);
      throw errorValue;
    } finally {
      suggesting.value = false;
    }
  };

  const evaluateDeliveries = async (
    recommendationStrategyId: string,
    filterStrategyId?: string | null,
    extraRequirement = "",
    jobIds?: string[]
  ) => {
    if (!task.value) return [];
    suggesting.value = true;
    error.value = null;
    try {
      const response = await api.evaluateFineJobBossDeliveries(task.value.id, {
        recommendation_strategy_id: recommendationStrategyId,
        filter_strategy_id: filterStrategyId,
        extra_requirement: extraRequirement,
        job_ids: jobIds
      });
      task.value = response.task;
      return response.evaluations;
    } catch (errorValue) {
      error.value = mapError(errorValue);
      throw errorValue;
    } finally {
      suggesting.value = false;
    }
  };

  const refreshTask = async (taskId = task.value?.id) => {
    if (!taskId) return null;
    try {
      task.value = await api.getFineJobBossCaptureTask(taskId);
      return task.value;
    } catch (errorValue) {
      error.value = mapError(errorValue);
      stopPolling();
      return null;
    }
  };

  const startPolling = (taskId: string) => {
    stopPolling();
    const poll = async () => {
      const current = await refreshTask(taskId);
      if (!current || current.status === "completed" || current.status === "failed") {
        capturing.value = false;
        stopPolling();
        return;
      }
      pollTimer = globalThis.setTimeout(poll, 1000);
    };
    pollTimer = globalThis.setTimeout(poll, 500);
  };

  const resumePolling = () => {
    if (task.value && (task.value.status === "queued" || task.value.status === "running")) {
      startPolling(task.value.id);
    }
  };

  const stopPolling = () => {
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
    suggesting,
    error,
    loadStatus,
    loadCities,
    startBrowser,
    stopBrowser,
    locate,
    capture,
    captureDetails,
    suggest,
    applyFilter,
    evaluateDeliveries,
    refreshTask,
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
