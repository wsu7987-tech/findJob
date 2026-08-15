import { computed, reactive, ref } from "vue";
import { defineStore } from "pinia";

import { NetworkError, api } from "../services/api";
import { deriveSourceLabel } from "../services/contract";
import type { ApiPoolItem, PoolCreateRequest } from "../types";

export const usePoolStore = defineStore("pool", () => {
  const items = ref<ApiPoolItem[]>([]);
  const total = ref(0);
  const loading = ref(false);
  const submitting = ref(false);
  const hasLoaded = ref(false);
  const connectionUnavailable = ref(false);
  const error = ref<string | null>(null);
  const filters = reactive({
    status: "pending",
    query: ""
  });

  const filteredItems = computed(() => {
    return items.value.filter((item) => {
      const matchesStatus =
        filters.status === "all" ? true : item.current_status === filters.status;
      const haystack = `${deriveSourceLabel(item)} ${item.source_value}`.toLowerCase();
      const matchesQuery =
        filters.query.trim().length === 0
          ? true
          : haystack.includes(filters.query.trim().toLowerCase());

      return matchesStatus && matchesQuery;
    });
  });

  const pendingItems = computed(() =>
    items.value.filter((item) => item.current_status === "pending")
  );

  const fetchItems = async () => {
    loading.value = true;
    error.value = null;

    try {
      const response = await api.getPoolItems();
      items.value = response.items;
      total.value = response.total;
      connectionUnavailable.value = false;
    } catch (errorValue) {
      connectionUnavailable.value = errorValue instanceof NetworkError;
      error.value = (errorValue as Error).message;
      items.value = [];
      total.value = 0;
    } finally {
      hasLoaded.value = true;
      loading.value = false;
    }
  };

  const addItem = async (payload: PoolCreateRequest) => {
    submitting.value = true;
    error.value = null;

    try {
      const response = await api.createPoolItem(payload);
      items.value = [response.item, ...items.value];
      total.value += 1;
      connectionUnavailable.value = false;
      return response.item;
    } catch (errorValue) {
      connectionUnavailable.value = errorValue instanceof NetworkError;
      error.value = (errorValue as Error).message;
      throw errorValue;
    } finally {
      submitting.value = false;
    }
  };

  const removeItem = async (itemId: string) => {
    error.value = null;
    try {
      await api.deletePoolItem(itemId);
      items.value = items.value.filter((item) => item.id !== itemId);
      total.value = Math.max(0, total.value - 1);
      connectionUnavailable.value = false;
    } catch (errorValue) {
      connectionUnavailable.value = errorValue instanceof NetworkError;
      error.value = (errorValue as Error).message;
      throw errorValue;
    }
  };

  const resummarizeItem = async (itemId: string) => {
    error.value = null;
    try {
      await api.resummarizePoolItem(itemId);
      items.value = items.value.map((item) =>
        item.id === itemId
          ? {
              ...item,
              current_status: "running"
            }
          : item
      );
      connectionUnavailable.value = false;
    } catch (errorValue) {
      connectionUnavailable.value = errorValue instanceof NetworkError;
      error.value = (errorValue as Error).message;
      throw errorValue;
    }
  };

  const reingestItem = async (itemId: string) => {
    error.value = null;
    try {
      await api.reingestPoolItem(itemId);
      items.value = items.value.map((item) =>
        item.id === itemId
          ? {
              ...item,
              current_status: "pending",
              was_resummarized: true
            }
          : item
      );
      connectionUnavailable.value = false;
    } catch (errorValue) {
      connectionUnavailable.value = errorValue instanceof NetworkError;
      error.value = (errorValue as Error).message;
      throw errorValue;
    }
  };

  return {
    items,
    total,
    loading,
    submitting,
    hasLoaded,
    connectionUnavailable,
    error,
    filters,
    filteredItems,
    pendingItems,
    fetchItems,
    addItem,
    removeItem,
    reingestItem,
    resummarizeItem
  };
});
