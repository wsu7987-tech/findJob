import { defineStore } from "pinia";
import { ref } from "vue";

import { ApiError, NetworkError, api } from "@/services/api";
import type {
  FineJobAutomationAction,
  FineJobBossExecutionState,
  FineJobReviewItem,
  FineJobReviewStatus
} from "@/types";

export const useFineJobWorkflowStore = defineStore("fineJobWorkflow", () => {
  const items = ref<FineJobReviewItem[]>([]);
  const queuedActions = ref<FineJobAutomationAction[]>([]);
  const selectedStatus = ref<FineJobReviewStatus>("pending");
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

  const load = async (status: FineJobReviewStatus = selectedStatus.value) => {
    loading.value = true;
    error.value = null;
    selectedStatus.value = status;
    try {
      const [reviewResponse, actionResponse] = await Promise.all([
        api.listFineJobReviewItems({
          status,
          decision: decision.value,
          query: query.value,
          execution_state: executionState.value,
          created_from: createdRange.value?.[0],
          created_to: createdRange.value?.[1],
          page: page.value,
          page_size: pageSize.value
        }),
        api.listFineJobAutomationActions("queued")
      ]);
      items.value = reviewResponse.items;
      total.value = reviewResponse.total;
      queuedActions.value = actionResponse.actions;
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
    batch
  };
});

const mapError = (errorValue: unknown) => {
  if (errorValue instanceof ApiError || errorValue instanceof NetworkError) {
    return errorValue.message;
  }
  return (errorValue as Error).message || "待确认事项加载失败。";
};
