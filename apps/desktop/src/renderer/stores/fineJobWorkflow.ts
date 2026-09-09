import { defineStore } from "pinia";
import { ref } from "vue";

import { ApiError, NetworkError, api } from "@/services/api";
import type {
  FineJobAutomationAction,
  FineJobBossExecutionState,
  FineJobChatReviewTask,
  FineJobReviewItem,
  FineJobReviewStatus,
  FineJobReviewTab
} from "@/types";

export const useFineJobWorkflowStore = defineStore("fineJobWorkflow", () => {
  const items = ref<FineJobReviewItem[]>([]);
  const queuedActions = ref<FineJobAutomationAction[]>([]);
  const chatReviewTasks = ref<FineJobChatReviewTask[]>([]);
  const chatExecutedTasks = ref<FineJobChatReviewTask[]>([]);
  const selectedStatus = ref<FineJobReviewTab>("pending");
  const loading = ref(false);
  const processingId = ref<string | null>(null);
  const error = ref<string | null>(null);
  const total = ref(0);
  const page = ref(1);
  const pageSize = ref(20);
  const query = ref("");
  const decision = ref<FineJobReviewItem["ai_decision"] | "">("");
  const executionState = ref<FineJobBossExecutionState | "">("");
  const createdRange = ref<[string, string] | null>(null);

  const load = async (status: FineJobReviewTab = selectedStatus.value) => {
    loading.value = true;
    error.value = null;
    selectedStatus.value = status;
    try {
      const executionView = status === "executed" ? "executed" : "";
      const reviewStatus = executionView ? "approved" : status as FineJobReviewStatus;
      const chatTaskRequest = status === "executed"
        ? api.listFineJobChatExecutedTasks()
        : api.listFineJobChatReviewTasks();
      const [reviewResponse, actionResponse, chatTaskResponse] = await Promise.all([
        api.listFineJobReviewItems({
          status: reviewStatus,
          execution_view: executionView,
          decision: decision.value,
          query: query.value,
          execution_state: executionState.value,
          created_from: createdRange.value?.[0],
          created_to: createdRange.value?.[1],
          page: page.value,
          page_size: pageSize.value
        }),
        api.listFineJobAutomationActions("queued"),
        chatTaskRequest
      ]);
      items.value = reviewResponse.items;
      total.value = reviewResponse.total;
      queuedActions.value = actionResponse.actions;
      chatReviewTasks.value = status === "pending" ? chatTaskResponse.items : [];
      chatExecutedTasks.value = status === "executed" ? chatTaskResponse.items : [];
      return reviewResponse;
    } catch (errorValue) {
      error.value = mapError(errorValue);
      throw errorValue;
    } finally {
      loading.value = false;
    }
  };

  const archive = async (item: FineJobReviewItem, note = "") => {
    processingId.value = item.id;
    error.value = null;
    try {
      await api.archiveFineJobReviewItem(item.id, note);
      await load(selectedStatus.value);
    } catch (errorValue) {
      error.value = mapError(errorValue);
      throw errorValue;
    } finally {
      processingId.value = null;
    }
  };

  const restore = async (item: FineJobReviewItem) => {
    processingId.value = item.id;
    error.value = null;
    try {
      await api.restoreFineJobReviewItem(item.id);
      await load(selectedStatus.value);
    } catch (errorValue) {
      error.value = mapError(errorValue);
      throw errorValue;
    } finally {
      processingId.value = null;
    }
  };

  const deleteItem = async (item: FineJobReviewItem) => {
    processingId.value = item.id;
    error.value = null;
    try {
      await api.deleteFineJobReviewItem(item.id);
      await load(selectedStatus.value);
    } catch (errorValue) {
      error.value = mapError(errorValue);
      throw errorValue;
    } finally {
      processingId.value = null;
    }
  };

  const linkChatBatch = async () => {
    const status = selectedStatus.value;
    if (status !== "pending" && status !== "rejected") {
      throw new Error("仅待确认和已拒绝列表支持关联聊天信息。");
    }
    const reviewStatus = status;
    loading.value = true;
    error.value = null;
    try {
      const result = await api.linkFineJobReviewItemsChat({
        status: reviewStatus,
        execution_view: undefined,
        decision: decision.value || undefined,
        query: query.value || undefined,
        execution_state: executionState.value || undefined,
        created_from: createdRange.value?.[0],
        created_to: createdRange.value?.[1]
      });
      await load(status);
      return result;
    } catch (errorValue) {
      error.value = mapError(errorValue);
      throw errorValue;
    } finally {
      loading.value = false;
    }
  };

  const batch = async (
    reviewItemIds: string[],
    operation: "approve" | "reject" | "archive",
    allowOverride = false
  ) => {
    loading.value = true;
    error.value = null;
    try {
      const result = await api.batchFineJobReviewItems({
        review_item_ids: reviewItemIds,
        operation,
        allow_override: allowOverride
      });
      await load(selectedStatus.value);
      return result;
    } catch (errorValue) {
      error.value = mapError(errorValue);
      throw errorValue;
    } finally {
      loading.value = false;
    }
  };

  const approve = async (item: FineJobReviewItem, message: string, allowOverride = false) => {
    processingId.value = item.id;
    error.value = null;
    try {
      const response = await api.approveFineJobReviewItem(item.id, {
        message,
        allow_override: allowOverride
      });
      await load(selectedStatus.value);
      return response.action;
    } catch (errorValue) {
      error.value = mapError(errorValue);
      throw errorValue;
    } finally {
      processingId.value = null;
    }
  };

  const reject = async (item: FineJobReviewItem, note = "") => {
    processingId.value = item.id;
    error.value = null;
    try {
      await api.rejectFineJobReviewItem(item.id, note);
      await load(selectedStatus.value);
    } catch (errorValue) {
      error.value = mapError(errorValue);
      throw errorValue;
    } finally {
      processingId.value = null;
    }
  };

  return {
    items,
    queuedActions,
    chatReviewTasks,
    chatExecutedTasks,
    selectedStatus,
    loading,
    processingId,
    error,
    total,
    page,
    pageSize,
    query,
    decision,
    executionState,
    createdRange,
    load,
    approve,
    reject,
    archive,
    restore,
    deleteItem,
    linkChatBatch,
    batch
  };
});

const mapError = (errorValue: unknown) => {
  if (errorValue instanceof ApiError || errorValue instanceof NetworkError) {
    return errorValue.message;
  }
  return (errorValue as Error).message || "待确认事项加载失败。";
};
