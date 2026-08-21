import { defineStore } from "pinia";
import { computed, ref } from "vue";

import { ApiError, NetworkError, api } from "@/services/api";
import type {
  FineJobAutomationAction,
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

  const total = computed(() => items.value.length);

  const load = async (status: FineJobReviewStatus = selectedStatus.value) => {
    loading.value = true;
    error.value = null;
    selectedStatus.value = status;
    try {
      const [reviewResponse, actionResponse] = await Promise.all([
        api.listFineJobReviewItems(status),
        api.listFineJobAutomationActions("queued")
      ]);
      items.value = reviewResponse.items;
      queuedActions.value = actionResponse.actions;
      return reviewResponse;
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
    load,
    approve,
    reject
  };
});

const mapError = (errorValue: unknown) => {
  if (errorValue instanceof ApiError || errorValue instanceof NetworkError) {
    return errorValue.message;
  }
  return (errorValue as Error).message || "待确认事项加载失败。";
};
