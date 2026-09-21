<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { useRouter } from "vue-router";

import { ApiError, api } from "@/services/api";
import { useFineJobWorkflowRunStore } from "@/stores/fineJobWorkflowRun";
import SmartCaptureConfigForm from "@/components/fine-job/SmartCaptureConfigForm.vue";
import {
  createDefaultSmartCaptureExecutionConfig,
  toSmartCaptureRequest,
  validateSmartCaptureExecutionConfig
} from "@/services/smartCaptureExecutionConfig";
import type {
  FineJobFilterStrategy,
  FineJobRecommendationStrategy
} from "@/types";

const strategies = ref<FineJobFilterStrategy[]>([]);
const recommendationStrategies = ref<FineJobRecommendationStrategy[]>([]);
const codexModels = ref<Array<{ id: string; label?: string | null; reasoning_efforts?: string[] }>>([]);
const codexModelLoadError = ref("");
const smartConfig = reactive(createDefaultSmartCaptureExecutionConfig("task_cockpit"));
const smartConfigModel = computed({
  get: () => smartConfig,
  set: (value) => Object.assign(smartConfig, value)
});
const workflowStore = useFineJobWorkflowRunStore();
const router = useRouter();
const workflowRun = computed(() => workflowStore.currentRun);
const childDecisionWaiting = computed(() => {
  const run = workflowRun.value;
  return Boolean(
    run
    && ["child_cancelled_waiting_decision", "child_failed_waiting_decision"].includes(run.control_state)
    && run.children?.some((item) => item.control_state === run.control_state)
  );
});

const selectedStrategy = computed(
  () => strategies.value.find((item) => item.id === smartConfig.filter_strategy_id) ?? null
);
const selectedRecommendationStrategy = computed(() =>
  recommendationStrategies.value.find((item) => item.id === smartConfig.recommendation_strategy_id) ?? null
);
const compatibleRecommendationStrategies = computed(() => recommendationStrategies.value.filter(
  (item) => item.filter_strategy_id === smartConfig.filter_strategy_id
));

const syncStrategyScope = () => {
  smartConfig.allowed_search_keywords = [...(selectedStrategy.value?.search_keywords ?? [])];
  smartConfig.allowed_cities = [...(selectedStrategy.value?.cities ?? [])];
  // 切换筛选策略后只保留与其关联的建议投递策略。
  const compatible = compatibleRecommendationStrategies.value;
  if (!compatible.some((item) => item.id === smartConfig.recommendation_strategy_id)) {
    smartConfig.recommendation_strategy_id = compatible[0]?.id ?? "";
  }
};

const ensureCollectionStartAvailable = async () => {
  const { active_task: activeTask } = await api.getFineJobActiveCollectionTask();
  if (!activeTask) return true;
  const activeLabel = activeTask.kind === "smart" ? "智能采集" : "自定义采集";
  await ElMessageBox.alert(
    `当前${activeLabel}尚未结束，请先停止${activeLabel}后再开始智能采集。`,
    "无法开始智能采集",
    { type: "warning", confirmButtonText: "知道了" }
  );
  return false;
};

const createRun = async () => {
  const strategy = selectedStrategy.value;
  const validation = validateSmartCaptureExecutionConfig(smartConfig);
  if (!validation.isValid) {
    ElMessage.warning(Object.values(validation.errors).join("；"));
    return;
  }
  if (!strategy?.id || (smartConfig.delivery_target_enabled && !selectedRecommendationStrategy.value?.id)) {
    ElMessage.warning(
      smartConfig.delivery_target_enabled
        ? "请完成岗位筛选策略和建议投递策略。"
        : "请完成岗位筛选策略。"
    );
    return;
  }
  if (
    smartConfig.delivery_target_enabled
    && selectedRecommendationStrategy.value?.filter_strategy_id !== strategy.id
  ) {
    ElMessage.warning("建议投递策略必须与当前岗位筛选策略匹配。");
    return;
  }
  try {
    if (!await ensureCollectionStartAvailable()) return;
    const run = await workflowStore.create(toSmartCaptureRequest(smartConfig));
    if (run) {
      await router.push({ name: "fine-job-capture" });
    }
  } catch (value) {
    if (value instanceof ApiError && value.errorCategory === "COLLECTION_TASK_ACTIVE") {
      await ElMessageBox.alert(value.message, "无法开始智能采集", {
        type: "warning",
        confirmButtonText: "知道了"
      });
      return;
    }
    ElMessage.error(value instanceof Error ? value.message : String(value));
  }
};

const pauseRun = async () => {
  if (!workflowRun.value) return;
  try {
    await workflowStore.pause();
  } catch (value) {
    ElMessage.error(value instanceof Error ? value.message : String(value));
  }
};

const resumeRun = async () => {
  if (!workflowRun.value) return;
  try {
    await workflowStore.resume();
  } catch (value) {
    ElMessage.error(value instanceof Error ? value.message : String(value));
  }
};

const cancelRun = async () => {
  if (!workflowRun.value) return;
  try {
    await workflowStore.cancel();
  } catch (value) {
    ElMessage.error(value instanceof Error ? value.message : String(value));
  }
};

const decideChild = async (decision: "skip" | "end") => {
  try {
    await workflowStore.decideChild(decision);
  } catch (value) {
    ElMessage.error(value instanceof Error ? value.message : String(value));
  }
};

onMounted(async () => {
  try {
    strategies.value = (await api.listFineJobFilterStrategies()).strategies.filter((item) => item.enabled);
    recommendationStrategies.value = (await api.listFineJobRecommendationStrategies()).strategies.filter((item) => item.enabled);
    const config = await api.getConfig();
    smartConfig.codex_model = config.codex_model || "";
    smartConfig.codex_reasoning_effort = (config.codex_reasoning_effort as typeof smartConfig.codex_reasoning_effort) || "medium";
    try {
      codexModels.value = (await api.listCodexModels(config.codex_cli_path || "codex")).models;
      if (!smartConfig.codex_model) smartConfig.codex_model = codexModels.value[0]?.id || "";
    } catch (value) {
      // 仍允许输入当前 Codex 配置兼容的模型 ID，并显示最终保存配置。
      codexModelLoadError.value = value instanceof Error ? value.message : "Codex 模型目录加载失败，可直接输入模型 ID。";
    }
    const initialStrategy = strategies.value.find((item) => item.enabled) ?? strategies.value[0];
    smartConfig.filter_strategy_id = initialStrategy?.id ?? "";
    syncStrategyScope();
    await workflowStore.restoreLatest(true, "task_cockpit");
  } catch (value) {
    ElMessage.error(value instanceof Error ? value.message : String(value));
  }
});
</script>

<template>
  <section class="task-cockpit page-panel">
    <div class="page-heading">
      <div>
        <p class="app-shell__eyebrow">Workflow Run</p>
        <h3>任务驾驶舱</h3>
        <p class="secondary-text">Phase 1 先展示后端真实的任务上下文快照；岗位搜索、聊天和资料分析将逐步接入。</p>
      </div>
    </div>
    <el-card shadow="never">
      <template #header>新岗位深挖 Run</template>
      <div class="run-form">
        <SmartCaptureConfigForm
          v-model="smartConfigModel"
          :filter-strategies="strategies"
          :recommendation-strategies="recommendationStrategies"
          :codex-models="codexModels"
          :codex-model-load-error="codexModelLoadError"
        />
        <div class="cockpit-actions">
          <el-button type="primary" :loading="workflowStore.loading" @click="createRun">建立并自动推进</el-button>
          <el-button
            v-if="workflowRun && workflowRun.status !== 'paused' && !['cancelled', 'completed', 'completed_with_errors', 'failed'].includes(workflowRun.status)"
            @click="pauseRun"
          >暂停</el-button>
          <el-button
            v-if="workflowRun?.status === 'paused' || (workflowRun?.status === 'waiting_for_user' && ['capture_interrupted', 'browser_not_running', 'collection_task_active'].includes(workflowRun.stop_reason))"
            :loading="workflowStore.advancing"
            @click="resumeRun"
          >继续</el-button>
          <el-button
            v-if="workflowRun && !['cancelled', 'completed', 'completed_with_errors', 'failed'].includes(workflowRun.status)"
            type="danger"
            plain
            @click="cancelRun"
          >停止任务</el-button>
          <el-button
            v-if="childDecisionWaiting"
            @click="decideChild('skip')"
          >跳过该子任务继续</el-button>
          <el-button
            v-if="childDecisionWaiting"
            type="danger"
            plain
            @click="decideChild('end')"
          >结束父任务</el-button>
        </div>
        <el-alert
          v-if="workflowRun"
          :title="`Run 状态：${workflowRun.status}；当前步骤：${workflowRun.current_step}`"
          :description="workflowRun.next_action_reason"
          :type="workflowRun.waiting_for_user ? 'warning' : 'info'"
          :closable="false"
          show-icon
        />
      </div>
    </el-card>
  </section>
</template>

<style scoped>
.task-cockpit { display: grid; gap: 16px; }
.run-form { max-width: 760px; }
.codex-config { display: grid; gap: 10px; }
.codex-config { grid-template-columns: minmax(220px, 1fr) 160px; }
</style>
