<script setup lang="ts">
import { computed, watch } from "vue";
import { InfoFilled } from "@element-plus/icons-vue";

import type { FineJobFilterStrategy, FineJobRecommendationStrategy } from "@/types";
import {
  compatibleRecommendationStrategies,
  type SmartCaptureExecutionConfig,
  type SmartCaptureConfigValidation,
  validateSmartCaptureExecutionConfig
} from "@/services/smartCaptureExecutionConfig";

const props = withDefaults(defineProps<{
  modelValue: SmartCaptureExecutionConfig;
  filterStrategies: FineJobFilterStrategy[];
  recommendationStrategies: FineJobRecommendationStrategy[];
  codexModels?: Array<{ id: string; label?: string | null }>;
  codexModelLoadError?: string;
  showAnalysisGuidance?: boolean;
  showContextBudget?: boolean;
}>(), {
  codexModels: () => [],
  codexModelLoadError: "",
  showAnalysisGuidance: true,
  showContextBudget: true
});

const emit = defineEmits<{
  "update:modelValue": [value: SmartCaptureExecutionConfig];
  validation: [value: SmartCaptureConfigValidation];
}>();

const selectedStrategy = computed(() => props.filterStrategies.find(
  (item) => item.id === props.modelValue.filter_strategy_id
) ?? null);
const recommendationOptions = computed(() => compatibleRecommendationStrategies(
  props.recommendationStrategies,
  props.modelValue.filter_strategy_id
));
const validation = computed(() => validateSmartCaptureExecutionConfig(props.modelValue));

const updateField = <K extends keyof SmartCaptureExecutionConfig>(
  field: K,
  value: SmartCaptureExecutionConfig[K]
) => {
  emit("update:modelValue", { ...props.modelValue, [field]: value });
};

const selectStrategy = (strategyId: string) => {
  const strategy = props.filterStrategies.find((item) => item.id === strategyId);
  const recommendations = compatibleRecommendationStrategies(props.recommendationStrategies, strategyId);
  const recommendationId = recommendations.some(
    (item) => item.id === props.modelValue.recommendation_strategy_id
  ) ? props.modelValue.recommendation_strategy_id : String(recommendations[0]?.id ?? "");
  emit("update:modelValue", {
    ...props.modelValue,
    filter_strategy_id: strategyId,
    allowed_search_keywords: [...(strategy?.search_keywords ?? [])],
    allowed_cities: [...(strategy?.cities ?? [])],
    recommendation_strategy_id: recommendationId
  });
};

const updateDeliveryTarget = (enabled: boolean) => {
  emit("update:modelValue", {
    ...props.modelValue,
    delivery_target_enabled: enabled,
    auto_jd_detail_collection_enabled: enabled
      ? true
      : props.modelValue.auto_jd_detail_collection_enabled
  });
};

watch(validation, (value) => emit("validation", value), { immediate: true });
defineExpose({ validation });
</script>

<template>
  <el-form label-position="top" class="smart-capture-config-form">
    <div class="form-grid">
      <el-form-item label="岗位筛选策略">
        <el-select
          :model-value="modelValue.filter_strategy_id"
          placeholder="选择岗位筛选策略"
          @update:model-value="selectStrategy"
        >
          <el-option
            v-for="item in filterStrategies"
            :key="item.id"
            :label="item.name"
            :value="item.id"
          />
        </el-select>
      </el-form-item>
      <el-form-item label="采集目标岗位数">
        <el-input-number
          :model-value="modelValue.candidate_target_count"
          :min="1"
          :max="500"
          @update:model-value="updateField('candidate_target_count', Number($event ?? 0))"
        />
      </el-form-item>
    </div>

    <el-form-item label="本轮搜索词（从策略中选择）">
      <el-checkbox-group
        :model-value="modelValue.allowed_search_keywords"
        @update:model-value="updateField('allowed_search_keywords', $event as string[])"
      >
        <el-checkbox
          v-for="keyword in selectedStrategy?.search_keywords ?? []"
          :key="keyword"
          :label="keyword"
        >{{ keyword }}</el-checkbox>
      </el-checkbox-group>
      <p v-if="!selectedStrategy?.search_keywords?.length" class="secondary-text">当前策略没有可用搜索词。</p>
    </el-form-item>

    <el-form-item label="本轮城市（从策略中选择）">
      <el-checkbox-group
        :model-value="modelValue.allowed_cities"
        @update:model-value="updateField('allowed_cities', $event as string[])"
      >
        <el-checkbox
          v-for="city in selectedStrategy?.cities ?? []"
          :key="city"
          :label="city"
        >{{ city }}</el-checkbox>
      </el-checkbox-group>
      <p v-if="!selectedStrategy?.cities?.length" class="secondary-text">当前策略没有可用城市。</p>
    </el-form-item>

    <section class="search-policy-section">
      <div class="search-policy-section__header">
        <strong>搜索策略</strong>
        <span class="secondary-text">控制当前条件的探索和继续采集方式</span>
      </div>
      <el-form-item class="search-policy-item">
        <template #label>
          <span class="search-policy-label">
            <span>搜索深度范围</span>
            <el-tooltip content="当前搜索条件的探索范围。首次至少探索最小深度，达到最大深度后不再继续当前条件。" placement="top">
              <el-icon class="search-policy-info"><InfoFilled /></el-icon>
            </el-tooltip>
          </span>
        </template>
        <div class="inline-form-control">
          <el-input-number
            :model-value="modelValue.min_depth"
            :min="1"
            :max="100"
            @update:model-value="updateField('min_depth', Number($event ?? 0))"
          />
          <span>至</span>
          <el-input-number
            :model-value="modelValue.max_depth"
            :min="1"
            :max="200"
            @update:model-value="updateField('max_depth', Number($event ?? 0))"
          />
        </div>
      </el-form-item>
      <el-form-item class="search-policy-item">
        <template #label>
          <span class="search-policy-label">
            <span>搜索滚动批次</span>
            <el-tooltip content="每次继续当前搜索条件时新增采集的页数，只影响单批大小，不是整个任务的总页数。" placement="top">
              <el-icon class="search-policy-info"><InfoFilled /></el-icon>
            </el-tooltip>
          </span>
        </template>
        <el-input-number
          :model-value="modelValue.scroll_batch_size"
          :min="1"
          :max="10"
          @update:model-value="updateField('scroll_batch_size', Number($event ?? 0))"
        />
      </el-form-item>
      <el-form-item class="search-policy-item">
        <template #label>
          <span class="search-policy-label">
            <span>低产出连续批次上限</span>
            <el-tooltip content="连续出现无新岗位或新鲜度低于约 25% 的批次数。达到上限后停止继续当前搜索条件。" placement="top">
              <el-icon class="search-policy-info"><InfoFilled /></el-icon>
            </el-tooltip>
          </span>
        </template>
        <el-input-number
          :model-value="modelValue.low_yield_streak_limit"
          :min="1"
          :max="20"
          @update:model-value="updateField('low_yield_streak_limit', Number($event ?? 0))"
        />
      </el-form-item>
    </section>
    <div class="capture-options">
      <el-switch
        :model-value="modelValue.prefer_current_page"
        @update:model-value="updateField('prefer_current_page', Boolean($event))"
      />
      <span>优先采集当前 BOSS 搜索页；当前页无效时自动定位</span>
    </div>
    <el-form-item v-if="showAnalysisGuidance" label="本 Run 临时分析指导（可选）">
      <el-input
        :model-value="modelValue.analysis_guidance"
        type="textarea"
        :rows="3"
        placeholder="只影响本 Run 的后续分析，不修改长期正式策略"
        @update:model-value="updateField('analysis_guidance', $event)"
      />
    </el-form-item>
    <el-form-item v-if="showContextBudget" label="本轮 Context 软预算（字符）">
      <el-input-number
        :model-value="modelValue.context_soft_budget_characters"
        :min="1000"
        :max="200000"
        :step="1000"
        @update:model-value="updateField('context_soft_budget_characters', Number($event ?? 0))"
      />
    </el-form-item>

    <el-form-item v-if="!modelValue.delivery_target_enabled" label="进入自动 JD 详情采集">
      <el-switch
        :model-value="modelValue.auto_jd_detail_collection_enabled"
        active-text="自动采集"
        inactive-text="手动采集"
        @update:model-value="updateField('auto_jd_detail_collection_enabled', Boolean($event))"
      />
    </el-form-item>
    <el-form-item label="投递目标">
      <el-switch
        :model-value="modelValue.delivery_target_enabled"
        active-text="采集后自动交给 Codex 分析"
        inactive-text="采集完成后等待手动选择"
        @update:model-value="updateDeliveryTarget(Boolean($event))"
      />
    </el-form-item>
    <template v-if="modelValue.delivery_target_enabled">
      <div class="form-grid">
        <el-form-item label="建议投递策略">
          <el-select
            :model-value="modelValue.recommendation_strategy_id"
            placeholder="选择建议投递策略"
            @update:model-value="updateField('recommendation_strategy_id', $event)"
          >
            <el-option
              v-for="item in recommendationOptions"
              :key="item.id"
              :label="item.name"
              :value="item.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="Recommend 目标">
          <el-input-number
            :model-value="modelValue.recommend_target"
            :min="1"
            :max="100"
            @update:model-value="updateField('recommend_target', Number($event ?? 0))"
          />
        </el-form-item>
        <el-form-item label="Review 目标（可选）">
          <div class="inline-form-control">
            <el-switch
              :model-value="modelValue.review_target_enabled"
              active-text="计入目标"
              inactive-text="不计入目标"
              @update:model-value="updateField('review_target_enabled', Boolean($event))"
            />
            <el-input-number
              v-if="modelValue.review_target_enabled"
              :model-value="modelValue.review_target"
              :min="1"
              :max="100"
              @update:model-value="updateField('review_target', Number($event ?? 0))"
            />
          </div>
        </el-form-item>
        <el-form-item label="目标达成模式">
          <el-select
            :model-value="modelValue.target_mode"
            @update:model-value="updateField('target_mode', $event)"
          >
            <el-option label="全部目标达到" value="all" />
            <el-option label="任一目标达到" value="any" />
          </el-select>
        </el-form-item>
        <el-form-item label="Codex 模型">
          <el-select
            :model-value="modelValue.codex_model"
            filterable
            allow-create
            placeholder="选择或输入 Codex 模型"
            @update:model-value="updateField('codex_model', $event)"
          >
            <el-option v-for="item in codexModels" :key="item.id" :label="item.label || item.id" :value="item.id" />
          </el-select>
          <div v-if="codexModelLoadError" class="secondary-text">{{ codexModelLoadError }}</div>
        </el-form-item>
        <el-form-item label="推理强度">
          <el-select
            :model-value="modelValue.codex_reasoning_effort"
            @update:model-value="updateField('codex_reasoning_effort', $event)"
          >
            <el-option label="minimal" value="minimal" />
            <el-option label="low" value="low" />
            <el-option label="medium" value="medium" />
            <el-option label="high" value="high" />
            <el-option label="xhigh" value="xhigh" />
          </el-select>
        </el-form-item>
        <el-form-item label="Analysis Batch">
          <el-input-number
            :model-value="modelValue.analysis_batch_size"
            :min="1"
            :max="20"
            @update:model-value="updateField('analysis_batch_size', Number($event ?? 0))"
          />
        </el-form-item>
        <el-form-item label="批次完成后">
          <el-select
            :model-value="modelValue.execution_policy_after_analysis_batch"
            @update:model-value="updateField('execution_policy_after_analysis_batch', $event)"
          >
            <el-option label="自动继续下一批" value="auto_continue" />
            <el-option label="等待我继续" value="wait_for_user" />
          </el-select>
        </el-form-item>
        <el-form-item label="Codex 交接">
          <el-select
            :model-value="modelValue.execution_policy_codex_handoff"
            @update:model-value="updateField('execution_policy_codex_handoff', $event)"
          >
            <el-option label="自动交接" value="auto" />
            <el-option label="等待手动交接" value="manual" />
          </el-select>
        </el-form-item>
      </div>
      <div class="capture-options">
        <el-switch
          :model-value="modelValue.analyze_all_candidates"
          @update:model-value="updateField('analyze_all_candidates', Boolean($event))"
        />
        <span>达到投递目标后继续分析已形成的候选岗位</span>
      </div>
      <div class="capture-options">
        <el-switch
          :model-value="modelValue.stop_after_current_batch"
          @update:model-value="updateField('stop_after_current_batch', Boolean($event))"
        />
        <span>当前 Codex 批次完成后等待我继续</span>
      </div>
    </template>

    <el-alert
      v-if="!validation.isValid"
      type="warning"
      title="配置尚未完整"
      :description="Object.values(validation.errors).join('；')"
      :closable="false"
      show-icon
    />
  </el-form>
</template>

<style scoped>
.smart-capture-config-form { display: grid; gap: 12px; }
.search-policy-section { padding: 0; }
.search-policy-section__header { display: flex; align-items: baseline; gap: 10px; padding: 0 0 4px; }
.search-policy-item { display: grid; grid-template-columns: minmax(190px, 0.35fr) minmax(0, 1fr); align-items: center; min-width: 0; margin: 0; padding: 12px 0; border-bottom: 1px solid var(--el-border-color-lighter); }
.search-policy-item:last-child { border-bottom: 0; }
.search-policy-item :deep(.el-form-item__label) { margin: 0; padding: 0 16px 0 0; }
.search-policy-item :deep(.el-form-item__content) { min-width: 0; }
.search-policy-label { display: inline-flex; align-items: center; gap: 4px; }
.search-policy-info { color: var(--el-text-color-secondary); cursor: help; }
.inline-form-control { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.capture-options { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }

@media (max-width: 900px) {
  .search-policy-item { grid-template-columns: 1fr; gap: 6px; }
  .search-policy-item :deep(.el-form-item__label) { padding-right: 0; }
}
</style>
