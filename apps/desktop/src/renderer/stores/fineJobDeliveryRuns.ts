import { defineStore } from "pinia";
import { computed, ref } from "vue";

import { ApiError, NetworkError, api } from "@/services/api";
import type { FineJobActionLog, FineJobDeliveryCandidate, FineJobDeliveryRun } from "@/types";

export const useFineJobDeliveryRunsStore = defineStore("fineJobDeliveryRuns", () => {
  const runs = ref<FineJobDeliveryRun[]>([]);
  const selectedRun = ref<FineJobDeliveryRun | null>(null);
  const candidates = ref<FineJobDeliveryCandidate[]>([]);
  const logs = ref<FineJobActionLog[]>([]);
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

  const loadRecentLogs = async () => {
    loading.value = true;
    error.value = null;
    try {
      const response = await api.listFineJobRecentActionLogs();
      logs.value = response.logs;
      return response.logs;
    } catch (errorValue) {
      error.value = mapError(errorValue);
      return [];
    } finally {
      loading.value = false;
    }
  };

  return {
    runs,
    selectedRun,
    candidates,
    logs,
    loading,
    creating,
    error,
    latestRun,
    load,
    createDryRun,
    loadRunDetail,
    loadRecentLogs
  };
});

const mapError = (errorValue: unknown) => {
  if (errorValue instanceof ApiError || errorValue instanceof NetworkError) {
    return errorValue.message;
  }
  return (errorValue as Error).message || "投递任务操作失败。";
};
