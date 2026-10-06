import type { FineJobFilterStrategy, FineJobRecommendationStrategy } from "@/types";

export type SmartCaptureSource = "boss_capture" | "task_cockpit";
export type SmartCaptureReasoningEffort = "minimal" | "low" | "medium" | "high" | "xhigh";

export interface SmartCaptureExecutionConfig {
  filter_strategy_id: string;
  allowed_search_keywords: string[];
  allowed_cities: string[];
  filters: Record<string, string>;
  candidate_target_count: number;
  prefer_current_page: boolean;
  delivery_target_enabled: boolean;
  recommendation_strategy_id: string;
  recommend_target: number;
  review_target_enabled: boolean;
  review_target: number;
  target_mode: "any" | "all";
  analyze_all_candidates: boolean;
  stop_after_current_batch: boolean;
  analysis_batch_size: number;
  execution_policy_after_analysis_batch: "auto_continue" | "wait_for_user";
  execution_policy_codex_handoff: "auto" | "manual";
  analysis_guidance: string;
  codex_model: string;
  codex_reasoning_effort: SmartCaptureReasoningEffort;
  context_soft_budget_characters: number;
  min_depth: number;
  scroll_batch_size: number;
  max_depth: number;
  low_yield_streak_limit: number;
}

export interface SmartCaptureConfigValidation {
  isValid: boolean;
  errors: Record<string, string>;
  missingFields: string[];
}

export interface SmartCaptureConfigOptions {
  filterStrategies: FineJobFilterStrategy[];
  recommendationStrategies: FineJobRecommendationStrategy[];
}

export const createDefaultSmartCaptureExecutionConfig = (
  source: SmartCaptureSource
): SmartCaptureExecutionConfig => ({
  filter_strategy_id: "",
  allowed_search_keywords: [],
  allowed_cities: [],
  filters: {},
  candidate_target_count: 15,
  prefer_current_page: true,
  // 岗位采集页原来默认关闭投递目标，驾驶舱原来默认开启投递目标。
  delivery_target_enabled: source === "task_cockpit",
  recommendation_strategy_id: "",
  recommend_target: 5,
  review_target_enabled: false,
  review_target: 1,
  target_mode: "all",
  analyze_all_candidates: false,
  stop_after_current_batch: false,
  analysis_batch_size: 5,
  execution_policy_after_analysis_batch: "auto_continue",
  execution_policy_codex_handoff: "auto",
  analysis_guidance: "",
  codex_model: "",
  codex_reasoning_effort: "medium",
  context_soft_budget_characters: 12000,
  min_depth: source === "task_cockpit" ? 5 : 1,
  scroll_batch_size: 3,
  max_depth: 20,
  low_yield_streak_limit: 3
});

export const validateSmartCaptureExecutionConfig = (
  config: SmartCaptureExecutionConfig
): SmartCaptureConfigValidation => {
  const errors: Record<string, string> = {};
  const missingFields: string[] = [];
  const required = (field: string, value: string | string[], message: string) => {
    const empty = Array.isArray(value) ? value.length === 0 : !value.trim();
    if (empty) {
      errors[field] = message;
      missingFields.push(field);
    }
  };

  required("filter_strategy_id", config.filter_strategy_id, "请选择岗位筛选策略。");
  required("allowed_search_keywords", config.allowed_search_keywords, "至少选择一个搜索词。");
  required("allowed_cities", config.allowed_cities, "至少选择一个城市。");
  if (config.candidate_target_count < 1) errors.candidate_target_count = "候选目标必须至少为 1。";
  if (config.max_depth < config.min_depth) errors.max_depth = "最大搜索深度不能小于最低探索深度。";
  if (config.context_soft_budget_characters < 1000) {
    errors.context_soft_budget_characters = "Context 软预算不能小于 1000。";
  }
  if (config.analysis_batch_size < 1 || config.analysis_batch_size > 20) {
    errors.analysis_batch_size = "Analysis Batch 必须在 1 到 20 之间。";
  }

  if (config.delivery_target_enabled) {
    required("recommendation_strategy_id", config.recommendation_strategy_id, "请选择建议投递策略。");
    required("codex_model", config.codex_model, "请选择或输入 Codex 模型。");
    if (!config.codex_reasoning_effort) {
      errors.codex_reasoning_effort = "请选择 Codex 推理强度。";
      missingFields.push("codex_reasoning_effort");
    }
    if (config.recommend_target < 1) {
      errors.recommend_target = "Recommend 目标必须至少为 1。";
      missingFields.push("recommend_target");
    }
    if (config.candidate_target_count < config.recommend_target) {
      errors.candidate_target_count = "候选池目标不能小于 Recommend 目标。";
    }
    if (config.review_target_enabled && config.review_target < 1) {
      errors.review_target = "Review 目标必须至少为 1。";
      missingFields.push("review_target");
    }
  }
  return { isValid: Object.keys(errors).length === 0, errors, missingFields: [...new Set(missingFields)] };
};

export const toSmartCaptureRequest = (config: SmartCaptureExecutionConfig) => ({
  filter_strategy_id: config.filter_strategy_id,
  allowed_search_keywords: [...config.allowed_search_keywords],
  allowed_cities: [...config.allowed_cities],
  filters: { ...config.filters },
  candidate_target_count: config.candidate_target_count,
  prefer_current_page: config.prefer_current_page,
  delivery_target_enabled: config.delivery_target_enabled,
  recommendation_strategy_id: config.delivery_target_enabled ? config.recommendation_strategy_id || undefined : undefined,
  recommend_target: config.delivery_target_enabled ? config.recommend_target : undefined,
  review_target: config.delivery_target_enabled && config.review_target_enabled ? config.review_target : undefined,
  target_mode: config.target_mode,
  analyze_all_candidates: config.analyze_all_candidates,
  stop_after_current_batch: config.stop_after_current_batch,
  analysis_batch_size: config.analysis_batch_size,
  execution_policy_after_analysis_batch: config.execution_policy_after_analysis_batch,
  execution_policy_codex_handoff: config.execution_policy_codex_handoff,
  analysis_guidance: config.analysis_guidance,
  codex_model: config.delivery_target_enabled ? config.codex_model || undefined : undefined,
  codex_reasoning_effort: config.delivery_target_enabled ? config.codex_reasoning_effort : undefined,
  context_soft_budget_characters: config.context_soft_budget_characters,
  min_depth: config.min_depth,
  scroll_batch_size: config.scroll_batch_size,
  max_depth: config.max_depth,
  low_yield_streak_limit: config.low_yield_streak_limit
});

export interface SmartCaptureExecutionConfigRequest {
  filter_strategy_id: string;
  allowed_search_keywords: string[];
  allowed_cities: string[];
  filters?: Record<string, string>;
  candidate_target_count?: number;
  prefer_current_page?: boolean;
  delivery_target_enabled?: boolean;
  recommendation_strategy_id?: string;
  recommend_target?: number;
  review_target?: number;
  target_mode?: "any" | "all";
  analyze_all_candidates?: boolean;
  stop_after_current_batch?: boolean;
  analysis_batch_size?: number;
  execution_policy_after_analysis_batch?: "auto_continue" | "wait_for_user";
  execution_policy_codex_handoff?: "auto" | "manual";
  analysis_guidance?: string;
  codex_model?: string;
  codex_reasoning_effort?: SmartCaptureReasoningEffort;
  context_soft_budget_characters?: number;
  min_depth?: number;
  scroll_batch_size?: number;
  max_depth?: number;
  low_yield_streak_limit?: number;
}

export const compatibleRecommendationStrategies = (
  strategies: FineJobRecommendationStrategy[],
  filterStrategyId: string
) => strategies.filter((item) => item.filter_strategy_id === filterStrategyId);
