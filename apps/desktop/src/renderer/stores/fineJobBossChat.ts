import { defineStore } from "pinia";
import { computed, ref } from "vue";

import { ApiError, NetworkError, api } from "@/services/api";
import type {
  FineJobChatReplyTask,
  FineJobChatBatchSummary,
  FineJobChatBatchTask,
  FineJobChatRuntime,
  FineJobChatSession,
  FineJobChatSessionDetail
} from "@/types";


export const useFineJobBossChatStore = defineStore("fineJobBossChat", () => {
  const runtime = ref<FineJobChatRuntime | null>(null);
  const sessions = ref<FineJobChatSession[]>([]);
  const selectedSessionId = ref<string | null>(null);
  const searchQuery = ref("");
  const statusFilter = ref("");
  const accountFilter = ref("");
  const attentionFilter = ref("");
  const waitingOnFilter = ref<"candidate" | "recruiter" | "">("");
  const nextOffset = ref<number | null>(null);
  const detail = ref<FineJobChatSessionDetail | null>(null);
  const detailCache = ref<Record<string, FineJobChatSessionDetail>>({});
  const batchSummary = ref<FineJobChatBatchSummary | null>(null);
  const batchProgress = ref<FineJobChatBatchTask | null>(null);
  const batchSize = ref(20);
  const loading = ref(false);
  const mutating = ref(false);
  const error = ref<string | null>(null);
  let batchPollTimer: number | null = null;

  const currentTask = computed<FineJobChatReplyTask | null>(() =>
    detail.value?.reply_tasks.find((task) => [
      "pending_generation", "generating", "awaiting_review", "failed"
    ].includes(task.status)) ?? null
  );

  const load = async () => {
    loading.value = true;
    error.value = null;
    try {
      const [runtimeResult, sessionResult, summaryResult] = await Promise.all([
        api.getFineJobChatRuntime(),
        api.listFineJobChatSessions(listParams()),
        api.getFineJobChatBatchSummary()
      ]);
      runtime.value = runtimeResult.runtime;
      sessions.value = sessionResult.sessions;
      nextOffset.value = sessionResult.next_offset ?? null;
      batchSummary.value = summaryResult;
      const available = Math.min(summaryResult.pending_chat_count, summaryResult.batch_limit);
      batchSize.value = available ? Math.min(Math.max(batchSize.value, 1), available) : 0;
      // 页面打开时保持空白，只有用户点击左侧会话后才读取本地详情。
      selectedSessionId.value = null;
      detail.value = null;
    } catch (value) {
      error.value = mapError(value);
      throw value;
    } finally {
      loading.value = false;
    }
  };

  const listParams = (offset = 0) => ({
    q: searchQuery.value.trim() || undefined,
    status: statusFilter.value || undefined,
    account_uid: accountFilter.value.trim() || undefined,
    ...(attentionFilter.value ? { attention: attentionFilter.value } : {}),
    ...(waitingOnFilter.value ? { waiting_on: waitingOnFilter.value } : {}),
    limit: 50,
    offset
  });

  const loadList = async () => {
    const result = await api.listFineJobChatSessions(listParams());
    sessions.value = result.sessions;
    nextOffset.value = result.next_offset ?? null;
    if (selectedSessionId.value && !sessions.value.some((item) => item.id === selectedSessionId.value)) {
      // 筛选或重新读列表后，保留用户的主动选择状态，不自动切换到其他会话。
      selectedSessionId.value = null;
      detail.value = null;
    }
  };

  const loadMore = async () => {
    if (nextOffset.value === null) return;
    const result = await api.listFineJobChatSessions(listParams(nextOffset.value));
    const known = new Set(sessions.value.map((item) => item.id));
    sessions.value.push(...result.sessions.filter((item) => !known.has(item.id)));
    nextOffset.value = result.next_offset ?? null;
  };

  const loadDetail = async (sessionId: string) => {
    selectedSessionId.value = sessionId;
    const cached = detailCache.value[sessionId];
    if (cached) detail.value = cached;
    try {
      const loaded = await api.getFineJobChatSession(sessionId);
      detailCache.value[sessionId] = loaded;
      detail.value = loaded;
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

  const refreshFriendList = async () => mutate(async () => {
    const result = await api.refreshFineJobChatFriendList();
    await refreshSelected();
    await refreshBatchSummary();
    return result;
  });

  const refreshBatchSummary = async () => {
    batchSummary.value = await api.getFineJobChatBatchSummary();
    const available = Math.min(
      batchSummary.value.pending_chat_count,
      batchSummary.value.batch_limit
    );
    batchSize.value = available ? Math.min(Math.max(batchSize.value, 1), available) : 0;
    return batchSummary.value;
  };

  const stopBatchPolling = () => {
    if (batchPollTimer !== null) window.clearInterval(batchPollTimer);
    batchPollTimer = null;
  };

  const refreshBatchProgress = async (taskId: string) => {
    const progress = await api.getFineJobChatBatch(taskId);
    if (progress.status === "queued" || progress.status === "running") {
      batchProgress.value = progress;
      return progress;
    }
    stopBatchPolling();
    batchProgress.value = null;
    await refreshSelected();
    await refreshBatchSummary();
    return progress;
  };

  const startBatchPolling = (taskId: string) => {
    stopBatchPolling();
    batchPollTimer = window.setInterval(() => {
      void refreshBatchProgress(taskId).catch((value) => {
        error.value = mapError(value);
        stopBatchPolling();
      });
    }, 1000);
  };

  const startBatchUpdate = async () => mutate(async () => {
    const task = await api.startFineJobChatBatch(batchSize.value);
    batchProgress.value = task;
    startBatchPolling(task.id);
    return task;
  });

  const refreshHistory = async () => mutate(async () => {
    if (!selectedSessionId.value) throw new Error("请先选择聊天会话");
    // 固定当前详情引用，避免响应式状态变化后再次读取为空。
    const currentDetail = detail.value;
    // 本地已有消息且平台没有新消息时，直接复用本地记录。
    if ((currentDetail?.message_count ?? 0) > 0 && !currentDetail?.session.message_update_required) {
      return {
        session_id: selectedSessionId.value,
        fetched_count: 0,
        inserted_count: 0,
        message_update_required: false,
        has_more: Boolean(currentDetail?.session.history_has_more)
      };
    }
    const result = await api.refreshFineJobChatHistory(selectedSessionId.value);
    await refreshSelected();
    return result;
  });

  const loadMoreHistory = async () => mutate(async () => {
    if (!selectedSessionId.value) throw new Error("请先选择聊天会话");
    const result = await api.loadMoreFineJobChatHistory(selectedSessionId.value);
    await refreshSelected();
    return result;
  });

  const updateJob = async () => mutate(async () => {
    if (!selectedSessionId.value) throw new Error("请先选择聊天会话");
    const result = await api.updateFineJobChatJob(selectedSessionId.value);
    await refreshSelected();
    return result;
  });

  const rejectJob = async () => mutate(async () => {
    const jobId = detail.value?.session.job_id;
    if (!jobId) throw new Error("当前聊天尚未关联历史岗位");
    const result = await api.setFineJobJobApplicationStatus(
      jobId,
      "rejected",
      "自动代聊页面人工标记为已被拒绝"
    );
    await refreshSelected();
    return result;
  });

  const analyzeProgress = async () => mutate(async () => {
    if (!selectedSessionId.value) throw new Error("请先选择聊天会话");
    const result = await api.analyzeFineJobChatProgress(selectedSessionId.value);
    await refreshSelected();
    return result;
  });

  const generate = async (
    instruction: string,
    regenerate = false,
    actionKind: "reply" | "followup" | "ask_rejection_reason" = "reply",
    jobActionKey?: string
  ) => mutate(async () => {
    if (!selectedSessionId.value) throw new Error("请先选择聊天会话");
    const result = await api.generateFineJobChatReply(
      selectedSessionId.value,
      instruction,
      regenerate,
      actionKind,
      jobActionKey
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
    const selectedId = selectedSessionId.value;
    if (selectedId) await loadDetail(selectedId);
    const sessionResult = await api.listFineJobChatSessions(listParams());
    const refreshedSessions = [...sessionResult.sessions];
    // 筛选结果未包含当前会话时，仍保留用户正在查看的会话。
    if (selectedId && !refreshedSessions.some((item) => item.id === selectedId)) {
      const previous = sessions.value.find((item) => item.id === selectedId);
      const current = detail.value?.session;
      if (previous || current) {
        refreshedSessions.push({
          ...previous,
          ...current,
          id: selectedId
        } as FineJobChatSession);
      }
    }
    sessions.value = refreshedSessions;
    nextOffset.value = sessionResult.next_offset ?? null;
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
    searchQuery,
    statusFilter,
    accountFilter,
    attentionFilter,
    waitingOnFilter,
    nextOffset,
    detail,
    detailCache,
    batchSummary,
    batchProgress,
    batchSize,
    currentTask,
    loading,
    mutating,
    error,
    load,
    loadDetail,
    loadList,
    loadMore,
    updateRuntime,
    checkNow,
    refreshFriendList,
    refreshBatchSummary,
    startBatchUpdate,
    stopBatchPolling,
    refreshHistory,
    loadMoreHistory,
    updateJob,
    rejectJob,
    analyzeProgress,
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
