import { defineStore } from "pinia";
import { ref } from "vue";

import { ApiError, NetworkError, api } from "@/services/api";
import type {
  FineJobBossExecutorDashboard,
  FineJobBossNavigationTask
} from "@/types";

export const useFineJobBossExecutorStore = defineStore("fineJobBossExecutor", () => {
  const dashboard = ref<FineJobBossExecutorDashboard | null>(null);
  const pairingCode = ref<string | null>(null);
  const pairingExpiresAt = ref<string | null>(null);
  const openingJobId = ref<string | null>(null);
  const loading = ref(false);
  const heartbeatTesting = ref(false);
  const error = ref<string | null>(null);

  const load = async () => {
    loading.value = true;
    error.value = null;
    try {
      dashboard.value = await api.getFineJobBossExecutorStatus();
      return dashboard.value;
    } catch (value) {
      error.value = mapError(value);
      throw value;
    } finally {
      loading.value = false;
    }
  };

  const createPairingCode = async () => {
    error.value = null;
    try {
      const result = await api.createFineJobBossPairingCode();
      pairingCode.value = result.code;
      pairingExpiresAt.value = result.expires_at;
      return result;
    } catch (value) {
      error.value = mapError(value);
      throw value;
    }
  };

  const control = async (command: "allow" | "pause" | "resume" | "emergency_stop") => {
    error.value = null;
    try {
      dashboard.value = await api.controlFineJobBossExecutor(command);
      return dashboard.value;
    } catch (value) {
      error.value = mapError(value);
      throw value;
    }
  };

  const testHeartbeat = async () => {
    heartbeatTesting.value = true;
    error.value = null;
    try {
      dashboard.value = await api.testFineJobBossExecutorHeartbeat();
      return dashboard.value;
    } catch (value) {
      error.value = mapError(value);
      // 心跳失败时立即给桌面端提供新的配对入口。
      try {
        const result = await api.createFineJobBossPairingCode();
        pairingCode.value = result.code;
        pairingExpiresAt.value = result.expires_at;
      } catch {
        // 保留原心跳错误，避免配对码生成失败覆盖根因。
      }
      throw value;
    } finally {
      heartbeatTesting.value = false;
    }
  };

  const disconnect = async () => {
    error.value = null;
    try {
      dashboard.value = await api.disconnectFineJobBossExecutor();
      pairingCode.value = null;
      pairingExpiresAt.value = null;
      return dashboard.value;
    } catch (value) {
      error.value = mapError(value);
      throw value;
    }
  };

  const openJob = async (
    jobId: string,
    sourceContext: "capture" | "history" | "review"
  ): Promise<FineJobBossNavigationTask> => {
    openingJobId.value = jobId;
    error.value = null;
    try {
      return (await api.openFineJobBossJob(jobId, sourceContext)).navigation;
    } catch (value) {
      error.value = mapError(value);
      throw value;
    } finally {
      openingJobId.value = null;
    }
  };

  const returnToReview = async (actionId: string) => {
    error.value = null;
    try {
      const result = await api.returnFineJobBossActionToReview(actionId);
      await load();
      return result.action;
    } catch (value) {
      error.value = mapError(value);
      throw value;
    }
  };

  const manualVerifyUnknown = async (actionId: string, contacted: boolean) => {
    error.value = null;
    try {
      const result = await api.manualVerifyFineJobBossUnknownAction(actionId, contacted);
      await load();
      return result.action;
    } catch (value) {
      error.value = mapError(value);
      throw value;
    }
  };

  return {
    dashboard,
    pairingCode,
    pairingExpiresAt,
    openingJobId,
    loading,
    heartbeatTesting,
    error,
    load,
    createPairingCode,
    control,
    testHeartbeat,
    disconnect,
    openJob,
    returnToReview,
    manualVerifyUnknown
  };
});

const mapError = (value: unknown) => {
  if (value instanceof ApiError || value instanceof NetworkError) return value.message;
  return (value as Error).message || "BOSS执行器操作失败。";
};
