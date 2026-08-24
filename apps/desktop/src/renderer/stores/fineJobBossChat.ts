import { defineStore } from "pinia";
import { computed, ref } from "vue";

import { ApiError, NetworkError, api } from "@/services/api";
import type {
  FineJobChatReplyTask,
  FineJobChatRuntime,
  FineJobChatSession,
  FineJobChatSessionDetail
} from "@/types";


export const useFineJobBossChatStore = defineStore("fineJobBossChat", () => {
  const runtime = ref<FineJobChatRuntime | null>(null);
  const sessions = ref<FineJobChatSession[]>([]);
  const selectedSessionId = ref<string | null>(null);
  const detail = ref<FineJobChatSessionDetail | null>(null);
  const loading = ref(false);
  const mutating = ref(false);
  const error = ref<string | null>(null);

  const currentTask = computed<FineJobChatReplyTask | null>(() =>
    detail.value?.reply_tasks.find((task) => [
      "pending_generation", "generating", "awaiting_review", "failed"
    ].includes(task.status)) ?? null
  );

  const load = async () => {
    loading.value = true;
    error.value = null;
    try {
      const [runtimeResult, sessionResult] = await Promise.all([
        api.getFineJobChatRuntime(),
        api.listFineJobChatSessions()
      ]);
      runtime.value = runtimeResult.runtime;
      sessions.value = sessionResult.sessions;
      if (!selectedSessionId.value || !sessions.value.some((item) => item.id === selectedSessionId.value)) {
        selectedSessionId.value = sessions.value[0]?.id ?? null;
      }
      if (selectedSessionId.value) await loadDetail(selectedSessionId.value);
      else detail.value = null;
    } catch (value) {
      error.value = mapError(value);
      throw value;
    } finally {
      loading.value = false;
    }
  };

  const loadDetail = async (sessionId: string) => {
    selectedSessionId.value = sessionId;
    try {
      detail.value = await api.getFineJobChatSession(sessionId);
      return detail.value;
    } catch (value) {
      error.value = mapError(value);
      throw value;
    }
  };

  const updateRuntime = async (changes: Parameters<typeof api.updateFineJobChatRuntime>[0]) =>
    mutate(async () => {
      runtime.value = (await api.updateFineJobChatRuntime(changes)).runtime;
      return runtime.value;
    });

  const checkNow = async () => mutate(async () => {
    const result = await api.checkFineJobChatNow();
    await refreshSelected();
    return result.generated;
  });

  const generate = async (instruction: string, regenerate = false) => mutate(async () => {
    if (!selectedSessionId.value) throw new Error("请先选择聊天会话");
    const result = await api.generateFineJobChatReply(
      selectedSessionId.value,
      instruction,
      regenerate
    );
    await refreshSelected();
    return result.reply_task;
  });

  const confirm = async (finalText: string) => mutate(async () => {
    const task = currentTask.value;
    if (!task) throw new Error("没有可确认的回复草稿");
    await api.editFineJobChatReply(task.id, finalText);
    const result = await api.confirmFineJobChatReply(task.id, {
      final_text: finalText,
      based_on_message_id: task.based_on_message_id,
      based_on_session_version: task.based_on_session_version
    });
    await refreshSelected();
    return result.action;
  });

  const cancel = async () => mutate(async () => {
    if (!currentTask.value) return null;
    const result = await api.cancelFineJobChatReply(currentTask.value.id);
    await refreshSelected();
    return result.reply_task;
  });

  const setSessionStatus = async (
    operation: "take-over" | "resume" | "pause",
    reason: string
  ) => mutate(async () => {
    if (!selectedSessionId.value) throw new Error("请先选择聊天会话");
    const result = await api.setFineJobChatSessionStatus(selectedSessionId.value, operation, reason);
    await refreshSelected();
    return result.session;
  });

  const refreshSelected = async () => {
    if (selectedSessionId.value) await loadDetail(selectedSessionId.value);
    const sessionResult = await api.listFineJobChatSessions();
    sessions.value = sessionResult.sessions;
  };

  const mutate = async <T>(operation: () => Promise<T>): Promise<T> => {
    mutating.value = true;
    error.value = null;
    try {
      return await operation();
    } catch (value) {
      error.value = mapError(value);
      throw value;
    } finally {
      mutating.value = false;
    }
  };

  return {
    runtime,
    sessions,
    selectedSessionId,
    detail,
    currentTask,
    loading,
    mutating,
    error,
    load,
    loadDetail,
    updateRuntime,
    checkNow,
    generate,
    confirm,
    cancel,
    setSessionStatus
  };
});

const mapError = (value: unknown) => {
  if (value instanceof ApiError || value instanceof NetworkError) return value.message;
  return (value as Error).message || "自动代聊操作失败。";
};
