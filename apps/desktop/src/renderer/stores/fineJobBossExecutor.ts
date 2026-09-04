import { defineStore } from "pinia";
import { ref } from "vue";

import { ApiError, NetworkError, api } from "@/services/api";
import type {
  FineJobBossExecutorDashboard,
  FineJobBossNavigationTask,
  FineJobBossExecutorTestJob
} from "@/types";

export const useFineJobBossExecutorStore = defineStore("fineJobBossExecutor", () => {
  const dashboard = ref<FineJobBossExecutorDashboard | null>(null);
  const pairingCode = ref<string | null>(null);
  const pairingExpiresAt = ref<string | null>(null);
  const openingJobId = ref<string | null>(null);
  const loading = ref(false);
  const heartbeatTesting = ref(false);
  const error = ref<string | null>(null);
  const testJobs = ref<FineJobBossExecutorTestJob[]>([]);
  let statusSocket: WebSocket | null = null;
  let statusSocketReconnectTimer: number | null = null;
  let statusChannelUsers = 0;

  const setDashboard = (value: FineJobBossExecutorDashboard) => {
    dashboard.value = value;
    if (value.executor?.browser_connected) {
      pairingCode.value = null;
      pairingExpiresAt.value = null;
    }
    return value;
  };

  const load = async () => {
    loading.value = true;
    error.value = null;
    try {
      return setDashboard(await api.getFineJobBossExecutorStatus());
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

  const control = async (command: "start" | "pause") => {
    error.value = null;
    try {
      return setDashboard(await api.controlFineJobBossExecutor(command));
    } catch (value) {
      error.value = mapError(value);
      throw value;
    }
  };

  const startStatusSync = () => {
    statusChannelUsers += 1;
    connectStatusChannel();
  };

  const stopStatusSync = () => {
    statusChannelUsers = Math.max(0, statusChannelUsers - 1);
    if (statusChannelUsers > 0) return;
    if (statusSocketReconnectTimer !== null) {
      window.clearTimeout(statusSocketReconnectTimer);
      statusSocketReconnectTimer = null;
    }
    statusSocket?.close();
    statusSocket = null;
  };

  const connectStatusChannel = () => {
    if (statusChannelUsers === 0 || statusSocket) return;
    const socket = new WebSocket("ws://127.0.0.1:8000/api/fine-job/boss-executor/desktop-channel");
    statusSocket = socket;
    socket.addEventListener("message", (event) => {
      try {
        const message = JSON.parse(String(event.data)) as { type?: string; runtime?: FineJobBossExecutorDashboard };
        if (message.type === "executor_state" && message.runtime) setDashboard(message.runtime);
      } catch {
        // 控制通道消息无效时等待下一条状态同步消息。
      }
    });
    socket.addEventListener("close", () => {
      if (statusSocket !== socket) return;
      statusSocket = null;
      if (statusChannelUsers === 0 || statusSocketReconnectTimer !== null) return;
      // 桌面状态通道断开后自动重连，持续接收插件的状态变更。
      statusSocketReconnectTimer = window.setTimeout(() => {
        statusSocketReconnectTimer = null;
        connectStatusChannel();
      }, 1000);
    });
  };

  const testHeartbeat = async () => {
    heartbeatTesting.value = true;
    error.value = null;
    try {
      return setDashboard(await api.testFineJobBossExecutorHeartbeat());
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
      setDashboard(await api.disconnectFineJobBossExecutor());
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

  const loadTestJobs = async () => {
    error.value = null;
    try {
      const result = await api.listFineJobBossExecutorTestJobs();
      testJobs.value = result.jobs;
      return result.jobs;
    } catch (value) {
      error.value = mapError(value);
      throw value;
    }
  };

  const updateTestJob = async (jobId: string, payload: { encrypt_job_id: string; job_link: string }) => {
    error.value = null;
    try {
      const result = await api.updateFineJobBossExecutorTestJob(jobId, payload);
      testJobs.value = testJobs.value.map((job) => job.id === jobId ? result.job : job);
      return result.job;
    } catch (value) {
      error.value = mapError(value);
      throw value;
    }
  };

  const updateSettings = async (payload: {
    task_cooldown_max_seconds: number;
    page_load_wait_max_seconds: number;
  }) => {
    error.value = null;
    try {
      return setDashboard(await api.updateFineJobBossExecutorSettings(payload));
    } catch (value) {
      error.value = mapError(value);
      throw value;
    }
  };

  const createTestTask = async (payload: {
    job_id: string;
    close_page_after_completion: boolean;
    delay_seconds: number;
  }) => {
    error.value = null;
    try {
      const result = await api.createFineJobBossExecutorTestTask(payload);
      await load();
      return result.task;
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
    testJobs,
    load,
    createPairingCode,
    control,
    testHeartbeat,
    disconnect,
    openJob,
    returnToReview,
    loadTestJobs,
    updateTestJob,
    updateSettings,
    createTestTask,
    startStatusSync,
    stopStatusSync
  };
});

const mapError = (value: unknown) => {
  if (value instanceof ApiError || value instanceof NetworkError) return value.message;
  return (value as Error).message || "BOSS执行器操作失败。";
};
