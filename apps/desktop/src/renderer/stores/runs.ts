import { computed, ref } from "vue";
import { defineStore } from "pinia";

import { ApiError, NetworkError, api } from "../services/api";
import { normalizeRunSnapshot } from "../services/contract";
import { createRunSubscription } from "../services/run-stream";
import type { ApiRunSnapshot, UiRunSnapshot } from "../types";

export const useRunsStore = defineStore("runs", () => {
  const currentRun = ref<UiRunSnapshot | null>(null);
  const list = ref<ApiRunSnapshot[]>([]);
  const listTotal = ref(0);
  const loadingRun = ref(false);
  const loadingList = ref(false);
  const hasLoadedList = ref(false);
  const runError = ref<string | null>(null);
  const listError = ref<string | null>(null);
  const historyUnavailable = ref(false);
  const runEndpointUnavailable = ref(false);
  const listConnectionUnavailable = ref(false);
  const runConnectionUnavailable = ref(false);
  const streamMode = ref<"idle" | "sse" | "polling">("idle");
  const loading = computed(() => loadingRun.value || loadingList.value);
  const error = computed(() => runError.value ?? listError.value);
  let stopStream: (() => void) | null = null;

  const loadRun = async (runId: string) => {
    loadingRun.value = true;
    runError.value = null;

    try {
      currentRun.value = normalizeRunSnapshot(await api.getRun(runId));
      runEndpointUnavailable.value = false;
      runConnectionUnavailable.value = false;
    } catch (errorValue) {
      runEndpointUnavailable.value = errorValue instanceof ApiError && errorValue.endpointUnavailable;
      runConnectionUnavailable.value = errorValue instanceof NetworkError;
      runError.value = (errorValue as Error).message;
      currentRun.value = null;
      throw errorValue;
    } finally {
      loadingRun.value = false;
    }
  };

  const watchRun = async (runId: string) => {
    await loadRun(runId);
    stopWatching();
    stopStream = await createRunSubscription({
      runId,
      onUpdate: (snapshot) => {
        currentRun.value = normalizeRunSnapshot(snapshot);
        runError.value = null;
        runConnectionUnavailable.value = false;
      },
      onModeChange: (mode) => {
        streamMode.value = mode;
      },
      onError: (errorValue) => {
        runError.value = errorValue.message;
      }
    });
  };

  const stopWatching = () => {
    stopStream?.();
    stopStream = null;
    streamMode.value = "idle";
  };

  const cancelRun = async (runId: string) => {
    runError.value = null;
    try {
      currentRun.value = normalizeRunSnapshot(await api.cancelRun(runId));
      runEndpointUnavailable.value = false;
      runConnectionUnavailable.value = false;
    } catch (errorValue) {
      runEndpointUnavailable.value = errorValue instanceof ApiError && errorValue.endpointUnavailable;
      runConnectionUnavailable.value = errorValue instanceof NetworkError;
      runError.value = (errorValue as Error).message;
      throw errorValue;
    }
  };

  const loadRunsList = async (taskType?: string, status?: string) => {
    loadingList.value = true;
    listError.value = null;

    try {
      const response = await api.getRuns(taskType, status);
      list.value = response.items;
      listTotal.value = response.total;
      historyUnavailable.value = false;
      listConnectionUnavailable.value = false;
    } catch (errorValue) {
      historyUnavailable.value = errorValue instanceof ApiError && errorValue.endpointUnavailable;
      listConnectionUnavailable.value = errorValue instanceof NetworkError;
      list.value = [];
      listTotal.value = 0;
      listError.value = (errorValue as Error).message;
    } finally {
      hasLoadedList.value = true;
      loadingList.value = false;
    }
  };

  return {
    currentRun,
    list,
    listTotal,
    loadingRun,
    loadingList,
    hasLoadedList,
    runError,
    listError,
    historyUnavailable,
    runEndpointUnavailable,
    listConnectionUnavailable,
    runConnectionUnavailable,
    streamMode,
    loading,
    error,
    loadRun,
    watchRun,
    stopWatching,
    cancelRun,
    loadRunsList
  };
});
