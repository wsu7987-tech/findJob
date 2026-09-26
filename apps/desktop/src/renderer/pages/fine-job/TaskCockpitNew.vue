<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from "vue";
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
  FineJobRecommendationStrategy,
  FineJobWorkflowChild
} from "@/types";
import type { SmartCaptureExecutionConfig, SmartCaptureConfigValidation } from "@/services/smartCaptureExecutionConfig";

const strategies = ref<FineJobFilterStrategy[]>([]);
const recommendationStrategies = ref<FineJobRecommendationStrategy[]>([]);
const codexModels = ref<Array<{ id: string; label?: string | null; reasoning_efforts?: string[] }>>([]);
const codexModelLoadError = ref("");
const smartConfig = reactive(createDefaultSmartCaptureExecutionConfig("task_cockpit"));
const selectedChildren = ref<string[]>(["smart_capture"]);
const childConfigs = reactive<Record<string, SmartCaptureExecutionConfig>>({
  smart_capture: smartConfig
});
const childValidation = computed<Record<string, SmartCaptureConfigValidation>>(() => ({
  smart_capture: validateSmartCaptureExecutionConfig(childConfigs.smart_capture)
}));
const childTypes = [{ type: "smart_capture", label: "岗位采集", description: "搜索、候选、JD 与分析由岗位采集域执行。" }];
const configDrawerVisible = ref(false);
const editingChildType = ref("smart_capture");
const smartConfigModel = computed({
  get: () => childConfigs.smart_capture,
  set: (value) => Object.assign(childConfigs.smart_capture, value)
});
const workflowStore = useFineJobWorkflowRunStore();
const router = useRouter();
let cockpitPageActive = false;
const workflowRun = computed(() => workflowStore.currentRun);
const terminalStatuses = new Set(["cancelled", "completed", "completed_with_errors", "failed"]);
const workflowRunTerminal = computed(() => terminalStatuses.has(workflowRun.value?.status ?? ""));
const canEditOrchestration = computed(() => !workflowRun.value || terminalStatuses.has(workflowRun.value.status));
const canPauseRun = computed(() => Boolean(
  workflowRun.value
  && !workflowRunTerminal.value
  && workflowRun.value.control_state === "active"
));
const canResumeRun = computed(() => Boolean(
  workflowRun.value
  && !workflowRunTerminal.value
  && workflowRun.value.control_state === "paused"
));
const selectedChildValidation = computed(() => selectedChildren.value.map((type) => childValidation.value[type]));
const orchestrationComplete = computed(() =>
  selectedChildren.value.length > 0 && selectedChildValidation.value.every((validation) => validation?.isValid)
);
const decisionChild = computed(() => {
  const run = workflowRun.value;
  if (!run || !["child_cancelled_waiting_decision", "child_failed_waiting_decision"].includes(run.control_state)) {
    return null;
  }
  return run.children?.find((child) => child.control_state === run.control_state) ?? null;
});

const selectedStrategy = computed(
  () => strategies.value.find((item) => item.id === smartConfig.filter_strategy_id) ?? null
);
const selectedRecommendationStrategy = computed(() =>
  recommendationStrategies.value.find((item) => item.id === smartConfig.recommendation_strategy_id) ?? null
);
const compatibleRecommendationStrategies = computed(() => recommendationStrategies.value.filter(
  (item) => item.filter_strategy_id === childConfigs.smart_capture.filter_strategy_id
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
  const validation = childValidation.value.smart_capture;
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
    const run = await workflowStore.create(toSmartCaptureRequest(childConfigs.smart_capture));
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

const openChildConfig = (childType: string) => {
  editingChildType.value = childType;
  configDrawerVisible.value = true;
};

const handleChildSelectionChange = (selected: string[]) => {
  selectedChildren.value = selected;
  const latestSelectedChild = selected[selected.length - 1];
  if (latestSelectedChild) openChildConfig(latestSelectedChild);
};

const openCapture = async () => {
  await router.push({ name: "fine-job-capture" });
};

const resumeChild = async (child: FineJobWorkflowChild) => {
  try {
    await workflowStore.resumeChild(child);
  } catch (value) {
    ElMessage.error(value instanceof Error ? value.message : String(value));
  }
};

const retryChild = async (child: FineJobWorkflowChild) => {
  try {
    await workflowStore.retryChild(child);
  } catch (value) {
    ElMessage.error(value instanceof Error ? value.message : String(value));
  }
};

const childLabel = (child: FineJobWorkflowChild) =>
  childTypes.find((item) => item.type === child.child_type)?.label ?? child.child_type;

const resultSummary = (child: FineJobWorkflowChild) => {
  const summary = child.result_summary ?? {};
  const shortSummary = summary.short_summary ?? summary.message;
  return typeof shortSummary === "string" && shortSummary ? shortSummary : "暂无结果摘要";
};

const canResumeChild = (child: FineJobWorkflowChild) =>
  ["waiting_child_paused", "waiting_child_interrupted"].includes(child.control_state)
  && Boolean(child.capabilities.resume);

const canRetryChild = (child: FineJobWorkflowChild) =>
  child.control_state === "waiting_child_interrupted" && Boolean(child.capabilities.retry);

onMounted(async () => {
  // 进入驾驶舱时先释放其他页面遗留的订阅和快照，避免慢请求期间展示上一个任务。
  cockpitPageActive = true;
  workflowStore.stopPolling();
  workflowStore.setRun(null);
  try {
    const restorePromise = workflowStore.restoreLatest(false, "task_cockpit").then((run) => {
      if (cockpitPageActive) return run;
      workflowStore.stopPolling();
      workflowStore.setRun(null);
      return null;
    });
    const [filterResult, recommendationResult, config] = await Promise.all([
      api.listFineJobFilterStrategies(),
      api.listFineJobRecommendationStrategies(),
      api.getConfig()
    ]);
    strategies.value = filterResult.strategies.filter((item) => item.enabled);
    recommendationStrategies.value = recommendationResult.strategies.filter((item) => item.enabled);
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
    await restorePromise;
  } catch (value) {
    ElMessage.error(value instanceof Error ? value.message : String(value));
  }
});

onBeforeUnmount(() => {
  // 页面离开后停止父任务 SSE，防止后台继续改写共享 Store。
  cockpitPageActive = false;
  workflowStore.stopPolling();
  workflowStore.setRun(null);
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
    <el-card v-if="canEditOrchestration" shadow="never" data-testid="orchestration-panel">
      <template #header>新任务编排</template>
      <div class="orchestration-panel">
        <p class="secondary-text">选择本轮需要编排的子任务，完成每项配置后即可开始。</p>
        <el-checkbox-group v-model="selectedChildren" class="child-selection" @change="handleChildSelectionChange">
          <div v-for="child in childTypes" :key="child.type" class="child-config-row">
            <el-checkbox :label="child.type">{{ child.label }}</el-checkbox>
            <span class="secondary-text">{{ child.description }}</span>
            <el-tag :type="childValidation[child.type]?.isValid ? 'success' : 'warning'" size="small">
              {{ childValidation[child.type]?.isValid ? '配置完整' : '待补充配置' }}
            </el-tag>
            <el-button text type="primary" @click="openChildConfig(child.type)">配置</el-button>
          </div>
        </el-checkbox-group>
        <div class="cockpit-actions">
          <el-button type="primary" :disabled="!orchestrationComplete" :loading="workflowStore.loading" @click="createRun">
            建立并自动推进
          </el-button>
        </div>
      </div>
    </el-card>

    <el-card v-if="workflowRun" shadow="never" data-testid="workflow-status-panel">
      <template #header>父任务状态</template>
      <el-alert
        :title="`父任务状态：${workflowRun.status}`"
        :description="`控制状态：${workflowRunTerminal ? '已结束' : workflowRun.control_state}；${workflowRun.waiting_reason || workflowRun.next_action_reason || '任务正在等待状态更新。'}`"
        :type="workflowRun.control_state.includes('waiting') ? 'warning' : 'info'"
        :closable="false"
        show-icon
      />
      <div class="cockpit-actions">
        <el-button v-if="canPauseRun" @click="pauseRun">暂停</el-button>
        <el-button v-if="canResumeRun" :loading="workflowStore.advancing" @click="resumeRun">继续</el-button>
        <el-button v-if="!canEditOrchestration" type="danger" plain @click="cancelRun">停止任务</el-button>
        <el-button v-if="decisionChild" @click="decideChild('skip')">跳过该子任务继续</el-button>
        <el-button v-if="decisionChild" type="danger" plain @click="decideChild('end')">结束父任务</el-button>
      </div>
    </el-card>

    <el-card v-if="workflowRun" shadow="never" data-testid="child-timeline">
      <template #header>子任务时间线</template>
      <el-timeline>
        <el-timeline-item
          v-for="child in workflowRun.children ?? []"
          :key="child.child_relation_id"
          :timestamp="child.completed_at || child.started_at || child.updated_at"
          placement="top"
        >
          <div class="timeline-step">
            <div class="timeline-step__title">
              <strong>{{ childLabel(child) }}</strong>
              <el-tag size="small">{{ child.status }}</el-tag>
            </div>
            <p>控制状态：{{ child.control_state }}</p>
            <p v-if="child.waiting_reason">等待原因：{{ child.waiting_reason }}</p>
            <p>结果摘要：{{ resultSummary(child) }}</p>
            <div class="cockpit-actions">
              <el-button v-if="child.child_type === 'smart_capture'" text type="primary" @click="openCapture">
                查看岗位采集
              </el-button>
              <el-button v-if="canResumeChild(child)" @click="resumeChild(child)">恢复子任务</el-button>
              <el-button v-if="canRetryChild(child)" @click="retryChild(child)">重试子任务</el-button>
            </div>
          </div>
        </el-timeline-item>
      </el-timeline>
      <el-empty v-if="!(workflowRun.children?.length)" description="父任务尚未返回子任务摘要" />
    </el-card>

    <el-drawer v-model="configDrawerVisible" direction="rtl" size="min(760px, 92vw)" :title="`${childTypes.find((item) => item.type === editingChildType)?.label ?? '子任务'}配置`">
      <SmartCaptureConfigForm
        v-if="editingChildType === 'smart_capture'"
        v-model="smartConfigModel"
        :filter-strategies="strategies"
        :recommendation-strategies="recommendationStrategies"
        :codex-models="codexModels"
        :codex-model-load-error="codexModelLoadError"
      />
    </el-drawer>
  </section>
</template>

<style scoped>
.task-cockpit { display: grid; gap: 16px; }
.orchestration-panel, .child-selection, .timeline-step { display: grid; gap: 12px; }
.child-config-row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.timeline-step__title, .cockpit-actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.timeline-step p { margin: 0; color: var(--el-text-color-secondary); }
</style>
