import { ref } from "vue";
import { defineStore } from "pinia";

import { ApiError, NetworkError, api } from "../services/api";
import type {
  ReportPrecheckResponse,
  ReportVersionDetail,
  ReportVersionSummary
} from "../types";

export const useReportsStore = defineStore("reports", () => {
  const precheck = ref<ReportPrecheckResponse | null>(null);
  const versions = ref<ReportVersionSummary[]>([]);
  const activeReport = ref<ReportVersionDetail | null>(null);
  const loading = ref(false);
  const generating = ref(false);
  const hasLoadedPrecheck = ref(false);
  const error = ref<string | null>(null);
  const endpointUnavailable = ref(false);
  const connectionUnavailable = ref(false);

  const loadPrecheck = async () => {
    loading.value = true;
    error.value = null;

    try {
      precheck.value = await api.getReportPrecheck();
      endpointUnavailable.value = false;
      connectionUnavailable.value = false;
    } catch (errorValue) {
      endpointUnavailable.value = errorValue instanceof ApiError && errorValue.endpointUnavailable;
      connectionUnavailable.value = errorValue instanceof NetworkError;
      error.value = (errorValue as Error).message;
      precheck.value = null;
    } finally {
      hasLoadedPrecheck.value = true;
      loading.value = false;
    }
  };

  const loadVersions = async (weekKey: string) => {
    loading.value = true;
    error.value = null;

    try {
      const response = await api.getReportVersions(weekKey);
      versions.value = response.items;
      endpointUnavailable.value = false;
      connectionUnavailable.value = false;
    } catch (errorValue) {
      endpointUnavailable.value = errorValue instanceof ApiError && errorValue.endpointUnavailable;
      connectionUnavailable.value = errorValue instanceof NetworkError;
      error.value = (errorValue as Error).message;
      versions.value = [];
    } finally {
      loading.value = false;
    }
  };

  const loadReport = async (weekKey: string, version: number) => {
    loading.value = true;
    error.value = null;

    try {
      activeReport.value = await api.getReportVersion(weekKey, version);
      endpointUnavailable.value = false;
      connectionUnavailable.value = false;
    } catch (errorValue) {
      endpointUnavailable.value = errorValue instanceof ApiError && errorValue.endpointUnavailable;
      connectionUnavailable.value = errorValue instanceof NetworkError;
      error.value = (errorValue as Error).message;
      activeReport.value = null;
    } finally {
      loading.value = false;
    }
  };

  const createReport = async (weekKey?: string) => {
    generating.value = true;
    error.value = null;

    try {
      endpointUnavailable.value = false;
      connectionUnavailable.value = false;
      return await api.createReportRun(weekKey);
    } catch (errorValue) {
      endpointUnavailable.value = errorValue instanceof ApiError && errorValue.endpointUnavailable;
      connectionUnavailable.value = errorValue instanceof NetworkError;
      error.value = (errorValue as Error).message;
      throw errorValue;
    } finally {
      generating.value = false;
    }
  };

  return {
    precheck,
    versions,
    activeReport,
    loading,
    generating,
    hasLoadedPrecheck,
    error,
    endpointUnavailable,
    connectionUnavailable,
    loadPrecheck,
    loadVersions,
    loadReport,
    createReport
  };
});
