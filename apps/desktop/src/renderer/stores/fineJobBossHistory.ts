import { defineStore } from "pinia";
import { ref } from "vue";

import { ApiError, NetworkError, api } from "@/services/api";
import type {
  FineJobBossCaptureTask,
  FineJobBossHistoryJob,
  FineJobBossHistoryQuery,
  FineJobBossHistoryResponse
} from "@/types";

export const useFineJobBossHistoryStore = defineStore("fineJobBossHistory", () => {
  const items = ref<FineJobBossHistoryJob[]>([]);
  const total = ref(0);
  const page = ref(1);
  const pageSize = ref(20);
  const loading = ref(false);
  const error = ref<string | null>(null);
  const detailTask = ref<FineJobBossCaptureTask | null>(null);
  const detailJobId = ref<string | null>(null);
  const deliveryJobId = ref<string | null>(null);
  let detailPollTimer: ReturnType<typeof setTimeout> | null = null;

  const load = async (query: FineJobBossHistoryQuery = {}) => {
    loading.value = true;
    error.value = null;
    try {
      const response: FineJobBossHistoryResponse = await api.listFineJobBossCaptureHistory({
        page: page.value,
        page_size: pageSize.value,
        ...query
      });
      items.value = response.items;
      total.value = response.total;
      page.value = response.page;
      pageSize.value = response.page_size;
      return response;
    } catch (errorValue) {
      error.value = mapError(errorValue);
      throw errorValue;
    } finally {
      loading.value = false;
    }
  };

  const captureDetails = async (historyJobId: string) => {
    error.value = null;
    detailJobId.value = historyJobId;
    try {
      detailTask.value = await api.captureFineJobBossHistoryDetails(historyJobId);
      startDetailPolling(detailTask.value.id);
      return detailTask.value;
    } catch (errorValue) {
      detailJobId.value = null;
      error.value = mapError(errorValue);
      throw errorValue;
    }
  };

  const evaluateDelivery = async (
    historyJobId: string,
    payload: {
      recommendation_strategy_id: string;
      filter_strategy_id?: string | null;
      extra_requirement?: string;
      context_stale_action?: "regenerate" | "use_current" | "cancel";
    }
  ) => {
    error.value = null;
    deliveryJobId.value = historyJobId;
    try {
      const response = await api.evaluateFineJobBossHistoryDelivery(historyJobId, {
        ...payload,
        manual_override: true
      });
      const index = items.value.findIndex((item) => item.id === historyJobId);
      if (index >= 0) items.value[index] = response.job;
      return response.job;
    } catch (errorValue) {
      error.value = mapError(errorValue);
      throw errorValue;
    } finally {
      deliveryJobId.value = null;
    }
  };

  const refreshDetailTask = async (taskId: string) => {
    try {
      detailTask.value = await api.getFineJobBossCaptureTask(taskId);
      return detailTask.value;
    } catch (errorValue) {
      error.value = mapError(errorValue);
      detailJobId.value = null;
      stopDetailPolling();
      return null;
    }
  };

  const startDetailPolling = (taskId: string) => {
    stopDetailPolling();
    const poll = async () => {
      const current = await refreshDetailTask(taskId);
      if (!current || current.status === "completed" || current.status === "failed") {
        detailJobId.value = null;
        stopDetailPolling();
        return;
      }
      detailPollTimer = globalThis.setTimeout(poll, 1000);
    };
    detailPollTimer = globalThis.setTimeout(poll, 500);
  };

  const stopDetailPolling = () => {
    if (detailPollTimer != null) {
      globalThis.clearTimeout(detailPollTimer);
      detailPollTimer = null;
    }
  };

  const clearDetailTask = () => {
    detailTask.value = null;
    detailJobId.value = null;
  };

  return {
    items,
    total,
    page,
    pageSize,
    loading,
    error,
    detailTask,
    detailJobId,
    deliveryJobId,
    load,
    captureDetails,
    evaluateDelivery,
    stopDetailPolling,
    clearDetailTask
  };
});

const mapError = (errorValue: unknown) => {
  if (errorValue instanceof ApiError || errorValue instanceof NetworkError) {
    return errorValue.message;
  }
  return (errorValue as Error).message || "历史采集记录加载失败。";
};
