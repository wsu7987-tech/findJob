import { defineStore } from "pinia";
import { computed, ref } from "vue";

import { ApiError, NetworkError, api } from "@/services/api";
import type {
  FineJobActionLog,
  FineJobDeliveryCandidate,
  FineJobDeliveryRun,
  FineJobOperationsDashboard
} from "@/types";

export const useFineJobDeliveryRunsStore = defineStore("fineJobDeliveryRuns", () => {
  const runs = ref<FineJobDeliveryRun[]>([]);
  const selectedRun = ref<FineJobDeliveryRun | null>(null);
  const candidates = ref<FineJobDeliveryCandidate[]>([]);
  const logs = ref<FineJobActionLog[]>([]);
  const dashboard = ref<FineJobOperationsDashboard | null>(null);
  const logTotal = ref(0);
  const logPage = ref(1);
  const logPageSize = ref(25);
  const logActionTypes = ref<string[]>([]);
  const loading = ref(false);
  const creating = ref(false);
  const error = ref<string | null>(null);

  const latestRun = computed(() => runs.value[0] ?? null);

  const load = async () => {
    loading.value = true;
    error.value = null;
    try {
      const response = await api.listFineJobDeliveryRuns();
      runs.value = response.runs;
      selectedRun.value = selectedRun.value ?? response.runs[0] ?? null;
      return response.runs;
    } catch (errorValue) {
      error.value = mapError(errorValue);
      return [];
    } finally {
      loading.value = false;
    }
  };

  const createDryRun = async () => {
    creating.value = true;
    error.value = null;
    try {
      const response = await api.createFineJobDeliveryRun({ mode: "dry_run" });
      selectedRun.value = response.run;
      await load();
      await loadRunDetail(response.run.id);
      return response.run;
    } catch (errorValue) {
      error.value = mapError(errorValue);
      throw errorValue;
    } finally {
      creating.value = false;
    }
  };

  const loadRunDetail = async (runId: string) => {
    loading.value = true;
    error.value = null;
    try {
      const [runResponse, candidateResponse, logResponse] = await Promise.all([
        api.getFineJobDeliveryRun(runId),
        api.listFineJobDeliveryCandidates(runId),
        api.listFineJobDeliveryRunLogs(runId)
      ]);
      selectedRun.value = runResponse.run;
      candidates.value = candidateResponse.candidates;
      logs.value = logResponse.logs;
      return runResponse.run;
    } catch (errorValue) {
      error.value = mapError(errorValue);
      return null;
    } finally {
      loading.value = false;
    }
  };

  const loadRecentLogs = async (query: {
    query?: string;
    level?: string;
    action_type?: string;
    category?: string;
    outcome?: string;
    source?: string;
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
      runs.value = dashboard.value.legacy_runs;
      return dashboard.value;
    } catch (errorValue) {
      error.value = mapError(errorValue);
      throw errorValue;
    } finally {
      loading.value = false;
    }
  };

  const deleteLegacyRun = async (runId: string) => {
    error.value = null;
    try {
      const result = await api.deleteFineJobDeliveryRun(runId);
      await loadDashboard();
      return result;
    } catch (errorValue) {
      error.value = mapError(errorValue);
      throw errorValue;
    }
  };

  const cleanupLogs = async (
    before: string,
    source: "all" | "legacy_run" | "main_workflow"
  ) => {
    error.value = null;
    try {
      return await api.cleanupFineJobActionLogs({ before, source });
    } catch (errorValue) {
      error.value = mapError(errorValue);
      throw errorValue;
    }
  };

  return {
    runs,
    selectedRun,
    candidates,
    logs,
    dashboard,
    logTotal,
    logPage,
    logPageSize,
    logActionTypes,
    loading,
    creating,
    error,
    latestRun,
    load,
    createDryRun,
    loadRunDetail,
    loadRecentLogs,
    loadDashboard,
    deleteLegacyRun,
    cleanupLogs
  };
});

const mapError = (errorValue: unknown) => {
  if (errorValue instanceof ApiError || errorValue instanceof NetworkError) {
    return errorValue.message;
  }
  return (errorValue as Error).message || "投递任务操作失败。";
};
