import { defineStore } from "pinia";
import { computed, ref } from "vue";

import { ApiError, NetworkError, api } from "@/services/api";
import type { FineJobDeliveryStrategy } from "@/types";

export const emptyDeliveryStrategy = (): FineJobDeliveryStrategy => ({
  automation_level: "assist",
  auto_greeting_enabled: false,
  force_contact_verification_enabled: false,
  daily_greeting_limit: 20,
  hourly_greeting_limit: 5,
  min_match_score: 0.72,
  resume_submit_mode: "manual",
  contact_share_mode: "manual",
  interview_accept_mode: "manual",
  only_online_interview: false,
  pause_on_risk: true,
  notes: ""
});

export const useFineJobDeliveryStrategyStore = defineStore("fineJobDeliveryStrategy", () => {
  const strategy = ref<FineJobDeliveryStrategy | null>(null);
  const loading = ref(false);
  const saving = ref(false);
  const error = ref<string | null>(null);

  const ready = computed(() => Boolean(strategy.value?.ready));

  const load = async () => {
    loading.value = true;
    error.value = null;
    try {
      const response = await api.getFineJobDeliveryStrategy();
      strategy.value = response.strategy;
      return response.strategy;
    } catch (errorValue) {
      error.value = mapError(errorValue);
      return null;
    } finally {
      loading.value = false;
    }
  };

  const save = async (payload: FineJobDeliveryStrategy) => {
    saving.value = true;
    error.value = null;
    try {
      const response = await api.saveFineJobDeliveryStrategy(payload);
      strategy.value = response.strategy;
      return response.strategy;
    } catch (errorValue) {
      error.value = mapError(errorValue);
      throw errorValue;
    } finally {
      saving.value = false;
    }
  };

  return {
    strategy,
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
  return (errorValue as Error).message || "投递策略操作失败。";
};
