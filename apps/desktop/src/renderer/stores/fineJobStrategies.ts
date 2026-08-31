import { defineStore } from "pinia";
import { ref } from "vue";

import { ApiError, NetworkError, api } from "@/services/api";
import type {
  FineJobFilterStrategy,
  FineJobRecommendationStrategy,
  FineJobStrategyChangeSet,
  FineJobStrategySearchKeyword
} from "@/types";

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
  cooldown_rules: {
    applied_company: { period: "permanent", exclude_outsourcing: true },
    detailed_and_evaluated_company: { period: "days_3", exclude_outsourcing: true },
    applied_job: { period: "permanent", exclude_outsourcing: false },
    detailed_and_evaluated_job: { period: "days_7", exclude_outsourcing: false }
  },
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
  const filterKeywords = ref<FineJobStrategySearchKeyword[]>([]);
  const filterChangeSets = ref<FineJobStrategyChangeSet[]>([]);
  const recommendationChangeSets = ref<FineJobStrategyChangeSet[]>([]);

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

  const loadFilterResources = async (strategyId?: string) => {
    if (!strategyId) {
      filterKeywords.value = [];
      filterChangeSets.value = [];
      return;
    }
    const [keywordResponse, changeResponse] = await Promise.all([
      api.listFineJobStrategySearchKeywords(strategyId),
      api.listFineJobFilterStrategyChangeSets(strategyId)
    ]);
    filterKeywords.value = keywordResponse.keywords;
    filterChangeSets.value = changeResponse.change_sets;
  };

  const loadRecommendationChangeSets = async (strategyId?: string) => {
    recommendationChangeSets.value = strategyId
      ? (await api.listFineJobRecommendationStrategyChangeSets(strategyId)).change_sets
      : [];
  };

  const createFilterKeyword = async (
    strategyId: string,
    payload: Pick<FineJobStrategySearchKeyword, "keyword" | "reason" | "enabled" | "sort_order">
  ) => {
    await api.createFineJobStrategySearchKeyword(strategyId, payload);
    await loadFilterResources(strategyId);
  };

  const updateFilterKeyword = async (strategyId: string, keyword: FineJobStrategySearchKeyword) => {
    await api.updateFineJobStrategySearchKeyword(strategyId, keyword.id, {
      keyword: keyword.keyword.trim(),
      reason: keyword.reason.trim(),
      enabled: keyword.enabled,
      sort_order: keyword.sort_order
    });
    await loadFilterResources(strategyId);
  };

  const removeFilterKeyword = async (strategyId: string, keywordId: string) => {
    await api.deleteFineJobStrategySearchKeyword(strategyId, keywordId);
    await loadFilterResources(strategyId);
  };

  const moveFilterKeyword = async (strategyId: string, keywordId: string, offset: -1 | 1) => {
    const ids = filterKeywords.value.map((item) => item.id);
    const index = ids.indexOf(keywordId);
    const target = index + offset;
    if (index < 0 || target < 0 || target >= ids.length) return;
    [ids[index], ids[target]] = [ids[target], ids[index]];
    const response = await api.reorderFineJobStrategySearchKeywords(strategyId, ids);
    filterKeywords.value = response.keywords;
  };

  const applyFilterChangeSet = async (
    strategyId: string,
    changeSetId: string,
    mode: "update_current" | "save_as_new"
  ) => {
    await api.applyFineJobFilterStrategyChangeSet(strategyId, changeSetId, { mode });
    await load();
    await loadFilterResources(strategyId);
  };

  const applyRecommendationChangeSet = async (
    strategyId: string,
    changeSetId: string,
    mode: "update_current" | "save_as_new"
  ) => {
    await api.applyFineJobRecommendationStrategyChangeSet(strategyId, changeSetId, { mode });
    await load();
    await loadRecommendationChangeSets(strategyId);
  };

  return {
    filters,
    recommendations,
    loading,
    saving,
    error,
    filterKeywords,
    filterChangeSets,
    recommendationChangeSets,
    load,
    saveFilter,
    removeFilter,
    saveRecommendation,
    removeRecommendation,
    loadFilterResources,
    loadRecommendationChangeSets,
    createFilterKeyword,
    updateFilterKeyword,
    removeFilterKeyword,
    moveFilterKeyword,
    applyFilterChangeSet,
    applyRecommendationChangeSet
  };
});

const mapError = (errorValue: unknown) => {
  if (errorValue instanceof ApiError || errorValue instanceof NetworkError) {
    return errorValue.message;
  }
  return (errorValue as Error).message || "策略管理操作失败。";
};
