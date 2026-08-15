import { defineStore } from "pinia";
import { computed, ref } from "vue";

import { ApiError, NetworkError, api } from "@/services/api";
import type { FineJobIntent } from "@/types";

export const emptyIntent = (): FineJobIntent => ({
  target_title: "",
  cities: [],
  keywords: [],
  expanded_keywords: [],
  excluded_keywords: [],
  salary_min: null,
  salary_max: null,
  work_mode: "any",
  notes: ""
});

export const useFineJobIntentStore = defineStore("fineJobIntent", () => {
  const intent = ref<FineJobIntent | null>(null);
  const loading = ref(false);
  const saving = ref(false);
  const error = ref<string | null>(null);

  const ready = computed(() => Boolean(intent.value?.ready));

  const load = async () => {
    loading.value = true;
    error.value = null;
    try {
      const response = await api.getFineJobIntent();
      intent.value = response.intent;
      return response.intent;
    } catch (errorValue) {
      error.value = mapError(errorValue);
      return null;
    } finally {
      loading.value = false;
    }
  };

  const save = async (payload: FineJobIntent) => {
    saving.value = true;
    error.value = null;
    try {
      const response = await api.saveFineJobIntent(payload);
      intent.value = response.intent;
      return response.intent;
    } catch (errorValue) {
      error.value = mapError(errorValue);
      throw errorValue;
    } finally {
      saving.value = false;
    }
  };

  return {
    intent,
    loading,
    saving,
    error,
    ready,
    load,
    save
  };
});

const mapError = (errorValue: unknown) => {
  if (errorValue instanceof ApiError || errorValue instanceof NetworkError) {
    return errorValue.message;
  }
  return (errorValue as Error).message || "期望岗位操作失败。";
};
