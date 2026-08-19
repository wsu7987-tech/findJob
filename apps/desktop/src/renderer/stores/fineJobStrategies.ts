import { defineStore } from "pinia";
import { ref } from "vue";

import { ApiError, NetworkError, api } from "@/services/api";
import type { FineJobFilterStrategy, FineJobRecommendationStrategy } from "@/types";

export const emptyFilterStrategy = (): FineJobFilterStrategy => ({
  name: "",
  enabled: true,
  search_keywords: [],
  cities: [],
  title_include_any: [],
  title_include_all: [],
  title_exclude: [],
  company_include: [],
  company_exclude: [],
  company_scales: [],
  company_industries: [],
  company_stages: [],
  degrees: [],
  experiences: [],
  job_types: [],
  monthly_salary_min: null,
  monthly_salary_max_at_least: null,
  daily_salary_min: null,
  skill_include_any: [],
  skill_include_all: [],
  skill_exclude: [],
  boss_active_statuses: [],
  unknown_value_policy: "review",
  notes: ""
});

export const emptyRecommendationStrategy = (): FineJobRecommendationStrategy => ({
  name: "",
  enabled: true,
  filter_strategy_id: null,
  resume_id: null,
  evaluation_method: "hybrid",
  desired_responsibilities: [],
  required_skills: [],
  preferred_skills: [],
  excluded_terms: [],
  preferred_industries: [],
  work_preferences: "",
  risk_notes: "",
  minimum_confidence: 0.7,
  insufficient_info_action: "review",
  notes: ""
});

export const useFineJobStrategiesStore = defineStore("fineJobStrategies", () => {
  const filters = ref<FineJobFilterStrategy[]>([]);
  const recommendations = ref<FineJobRecommendationStrategy[]>([]);
  const loading = ref(false);
  const saving = ref(false);
  const error = ref<string | null>(null);

  const load = async () => {
    loading.value = true;
    error.value = null;
    try {
      const [filterResponse, recommendationResponse] = await Promise.all([
        api.listFineJobFilterStrategies(),
        api.listFineJobRecommendationStrategies()
      ]);
      filters.value = filterResponse.strategies;
      recommendations.value = recommendationResponse.strategies;
    } catch (errorValue) {
      error.value = mapError(errorValue);
    } finally {
      loading.value = false;
    }
  };

  const saveFilter = async (payload: FineJobFilterStrategy) => {
    saving.value = true;
    error.value = null;
    try {
      const response = payload.id
        ? await api.updateFineJobFilterStrategy(payload.id, payload)
        : await api.createFineJobFilterStrategy(payload);
      await load();
      return response.strategy;
    } catch (errorValue) {
      error.value = mapError(errorValue);
      throw errorValue;
    } finally {
      saving.value = false;
    }
  };

  const removeFilter = async (strategyId: string) => {
    await api.deleteFineJobFilterStrategy(strategyId);
    await load();
  };

  const saveRecommendation = async (payload: FineJobRecommendationStrategy) => {
    saving.value = true;
    error.value = null;
    try {
      const response = payload.id
        ? await api.updateFineJobRecommendationStrategy(payload.id, payload)
        : await api.createFineJobRecommendationStrategy(payload);
      await load();
      return response.strategy;
    } catch (errorValue) {
      error.value = mapError(errorValue);
      throw errorValue;
    } finally {
      saving.value = false;
    }
  };

  const removeRecommendation = async (strategyId: string) => {
    await api.deleteFineJobRecommendationStrategy(strategyId);
    await load();
  };

  return {
    filters,
    recommendations,
    loading,
    saving,
    error,
    load,
    saveFilter,
    removeFilter,
    saveRecommendation,
    removeRecommendation
  };
});

const mapError = (errorValue: unknown) => {
  if (errorValue instanceof ApiError || errorValue instanceof NetworkError) {
    return errorValue.message;
  }
  return (errorValue as Error).message || "策略管理操作失败。";
};
