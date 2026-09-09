import { defineStore } from "pinia";
import { ref } from "vue";

import { ApiError, NetworkError, api } from "@/services/api";
import type {
  FineJobActionLog,
  FineJobOperationsDashboard
} from "@/types";

export const useFineJobDeliveryRunsStore = defineStore("fineJobDeliveryRuns", () => {
  const logs = ref<FineJobActionLog[]>([]);
  const dashboard = ref<FineJobOperationsDashboard | null>(null);
  const logTotal = ref(0);
  const logPage = ref(1);
  const logPageSize = ref(25);
  const logActionTypes = ref<string[]>([]);
  const loading = ref(false);
  const error = ref<string | null>(null);

  const loadRecentLogs = async (query: {
    query?: string;
    level?: string;
    action_type?: string;
    category?: string;
    outcome?: string;
    created_from?: string;
    created_to?: string;
    page?: number;
    page_size?: number;
  } = {}) => {
    loading.value = true;
    error.value = null;
    try {
      const response = await api.listFineJobRecentActionLogs(query);
      logs.value = response.logs;
      logTotal.value = response.total ?? response.logs.length;
      logPage.value = response.page ?? query.page ?? 1;
      logPageSize.value = response.page_size ?? query.page_size ?? logPageSize.value;
      logActionTypes.value = response.action_types ?? [];
      return response.logs;
    } catch (errorValue) {
      error.value = mapError(errorValue);
      return [];
    } finally {
      loading.value = false;
    }
  };

  const loadDashboard = async () => {
    loading.value = true;
    error.value = null;
    try {
      dashboard.value = await api.getFineJobOperationsDashboard();
      return dashboard.value;
    } catch (errorValue) {
      error.value = mapError(errorValue);
      throw errorValue;
    } finally {
      loading.value = false;
    }
  };

  const cleanupLogs = async (before: string) => {
    error.value = null;
    try {
      return await api.cleanupFineJobActionLogs({ before });
    } catch (errorValue) {
      error.value = mapError(errorValue);
      throw errorValue;
    }
  };

  return {
    logs,
    dashboard,
    logTotal,
    logPage,
    logPageSize,
    logActionTypes,
    loading,
    error,
    loadRecentLogs,
    loadDashboard,
    cleanupLogs
  };
});

const mapError = (errorValue: unknown) => {
  if (errorValue instanceof ApiError || errorValue instanceof NetworkError) {
    return errorValue.message;
  }
  return (errorValue as Error).message || "投递任务操作失败。";
};
