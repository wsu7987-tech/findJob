<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { useRouter } from "vue-router";

import { ApiError, api } from "@/services/api";
import { useFineJobWorkflowRunStore } from "@/stores/fineJobWorkflowRun";
import type {
  FineJobFilterStrategy,
  FineJobRecommendationStrategy
} from "@/types";

const strategies = ref<FineJobFilterStrategy[]>([]);
const recommendationStrategies = ref<FineJobRecommendationStrategy[]>([]);
const selectedStrategyId = ref("");
const selectedRecommendationStrategyId = ref("");
const codexModel = ref("");
const codexReasoningEffort = ref<"minimal" | "low" | "medium" | "high" | "xhigh">("medium");
const codexModels = ref<Array<{ id: string; label?: string | null; reasoning_efforts?: string[] }>>([]);
const analysisGuidance = ref("");
const selectedKeywords = ref<string[]>([]);
const selectedCities = ref<string[]>([]);
const recommendTarget = ref(5);
const enableReviewTarget = ref(false);
const reviewTarget = ref(1);
const targetMode = ref<"any" | "all">("all");
const analyzeAllCandidates = ref(false);
const stopAfterCurrentBatch = ref(false);
const analysisBatchSize = ref(5);
const afterAnalysisBatch = ref<"auto_continue" | "wait_for_user">("auto_continue");
const codexHandoff = ref<"auto" | "manual">("auto");
const candidateTargetCount = ref(15);
const contextSoftBudgetCharacters = ref(12000);
const workflowStore = useFineJobWorkflowRunStore();
const router = useRouter();
const workflowRun = computed(() => workflowStore.currentRun);

const selectedStrategy = computed(
  () => strategies.value.find((item) => item.id === selectedStrategyId.value) ?? null
);
const selectedRecommendationStrategy = computed(() =>
  recommendationStrategies.value.find((item) => item.id === selectedRecommendationStrategyId.value) ?? null
);
const compatibleRecommendationStrategies = computed(() => recommendationStrategies.value.filter(
  (item) => item.filter_strategy_id === selectedStrategyId.value
));

const syncStrategyScope = () => {
  selectedKeywords.value = [...(selectedStrategy.value?.search_keywords ?? [])];
  selectedCities.value = [...(selectedStrategy.value?.cities ?? [])];
  // 切换筛选策略后只保留与其关联的建议投递策略。
  const compatible = compatibleRecommendationStrategies.value;
  if (!compatible.some((item) => item.id === selectedRecommendationStrategyId.value)) {
    selectedRecommendationStrategyId.value = compatible[0]?.id ?? "";
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
  if (!strategy?.id || !selectedRecommendationStrategy.value?.id || !codexModel.value || !selectedKeywords.value.length || !selectedCities.value.length) {
    ElMessage.warning("请完成筛选策略、建议投递策略、Codex 模型、搜索词和城市选择。");
    return;
  }
  if (selectedRecommendationStrategy.value.filter_strategy_id !== strategy.id) {
    ElMessage.warning("建议投递策略必须与当前岗位筛选策略匹配。");
    return;
  }
  if (candidateTargetCount.value < recommendTarget.value) {
    ElMessage.warning("候选池目标不能小于 Recommend 完成目标。");
    return;
  }
  try {
    if (!await ensureCollectionStartAvailable()) return;
    const run = await workflowStore.create({
      filter_strategy_id: strategy.id,
      recommendation_strategy_id: selectedRecommendationStrategy.value.id,
      codex_model: codexModel.value,
      codex_reasoning_effort: codexReasoningEffort.value,
      analysis_guidance: analysisGuidance.value,
      recommend_target: recommendTarget.value,
      review_target: enableReviewTarget.value ? reviewTarget.value : undefined,
      target_mode: targetMode.value,
      analyze_all_candidates: analyzeAllCandidates.value,
      stop_after_current_batch: stopAfterCurrentBatch.value,
      analysis_batch_size: analysisBatchSize.value,
      execution_policy_after_analysis_batch: afterAnalysisBatch.value,
      execution_policy_codex_handoff: codexHandoff.value,
      candidate_target_count: candidateTargetCount.value,
      allowed_search_keywords: selectedKeywords.value,
      allowed_cities: selectedCities.value,
      context_soft_budget_characters: contextSoftBudgetCharacters.value
    });
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

onMounted(async () => {
  try {
    strategies.value = (await api.listFineJobFilterStrategies()).strategies.filter((item) => item.enabled);
    recommendationStrategies.value = (await api.listFineJobRecommendationStrategies()).strategies.filter((item) => item.enabled);
    const config = await api.getConfig();
    codexModel.value = config.codex_model || "";
    codexReasoningEffort.value = (config.codex_reasoning_effort as typeof codexReasoningEffort.value) || "medium";
    try {
      codexModels.value = (await api.listCodexModels(config.codex_cli_path || "codex")).models;
      if (!codexModel.value) codexModel.value = codexModels.value[0]?.id || "";
    } catch {
      // 仍允许输入当前 Codex 配置兼容的模型 ID，并显示最终保存配置。
    }
    await workflowStore.restoreLatest(true);
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
      <el-form label-position="top" class="run-form">
        <el-form-item label="岗位筛选策略">
          <el-select v-model="selectedStrategyId" placeholder="选择已启用策略" @change="syncStrategyScope">
            <el-option v-for="item in strategies" :key="item.id" :label="item.name" :value="item.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="建议投递策略">
          <el-select v-model="selectedRecommendationStrategyId" placeholder="选择与筛选策略匹配的建议投递策略">
            <el-option v-for="item in compatibleRecommendationStrategies" :key="item.id" :label="item.name" :value="item.id" />
          </el-select>
          <p class="secondary-text">建议投递策略决定最终是否值得投递；系统会校验其筛选策略、候选人档案与具体简历关联。</p>
        </el-form-item>
        <el-form-item label="Codex 分析配置">
          <div class="codex-config">
            <el-select v-model="codexModel" filterable allow-create placeholder="选择或输入当前 Codex 可用模型">
              <el-option v-for="item in codexModels" :key="item.id" :label="item.label || item.id" :value="item.id" />
            </el-select>
            <el-select v-model="codexReasoningEffort">
              <el-option label="minimal" value="minimal" />
              <el-option label="low" value="low" />
              <el-option label="medium" value="medium" />
              <el-option label="high" value="high" />
              <el-option label="xhigh" value="xhigh" />
            </el-select>
          </div>
          <p class="secondary-text">模型目录来自当前 Codex CLI；保存的模型与推理强度会传入本 Run 的启动链路。</p>
        </el-form-item>
        <el-form-item label="本 Run 临时分析指导（可选）">
          <el-input v-model="analysisGuidance" type="textarea" :rows="3" placeholder="只影响本 Run 的后续分析，不修改长期正式策略" />
        </el-form-item>
        <el-form-item label="Recommend 完成目标">
          <el-input-number v-model="recommendTarget" :min="1" :max="100" />
        </el-form-item>
        <el-form-item label="Review 完成目标（可选）">
          <el-switch v-model="enableReviewTarget" active-text="计入目标" inactive-text="不计入目标" />
          <el-input-number v-if="enableReviewTarget" v-model="reviewTarget" :min="1" :max="100" />
        </el-form-item>
        <el-form-item label="目标达成模式">
          <el-select v-model="targetMode">
            <el-option label="全部已配置目标达到（all）" value="all" />
            <el-option label="任一已配置目标达到（any）" value="any" />
          </el-select>
        </el-form-item>
        <el-form-item label="Analysis Batch">
          <el-input-number v-model="analysisBatchSize" :min="1" :max="20" />
          <p class="secondary-text">每个 Analysis Batch 的岗位数，可在 1 到 20 之间选择。</p>
        </el-form-item>
        <el-form-item label="达标后的候选池处理">
          <el-switch v-model="analyzeAllCandidates" active-text="分析已形成候选池" inactive-text="达标即结束" />
          <p class="secondary-text">开启后会冻结达标时已形成的候选池，只继续分析其中岗位，不再采集新岗位。</p>
        </el-form-item>
        <el-form-item label="批次衔接">
          <el-switch v-model="stopAfterCurrentBatch" active-text="当前批后等待继续" inactive-text="不额外停止" />
          <el-select v-model="afterAnalysisBatch" :disabled="stopAfterCurrentBatch">
            <el-option label="自动继续下一批" value="auto_continue" />
            <el-option label="每批等待用户继续" value="wait_for_user" />
          </el-select>
        </el-form-item>
        <el-form-item label="Codex 交接">
          <el-select v-model="codexHandoff">
            <el-option label="自动交接（auto）" value="auto" />
            <el-option label="等待手动交接（manual）" value="manual" />
          </el-select>
          <p class="secondary-text">自动模式由桌面应用持续发现 ready Analysis Batch 并交接给 Codex；手动模式保留驾驶舱按钮。</p>
        </el-form-item>
        <el-form-item label="候选池目标">
          <el-input-number v-model="candidateTargetCount" :min="recommendTarget" :max="500" />
          <p class="secondary-text">候选池达到阶段目标后，系统按确定性发现顺序以小批次获取 JD；recommend 不足时继续补下一批。</p>
        </el-form-item>
        <el-form-item label="本轮 Context 软预算（字符）">
          <el-input-number v-model="contextSoftBudgetCharacters" :min="1000" :max="200000" :step="1000" />
          <p class="secondary-text">这是后端实际执行的软限制；超出时 Run 会暂停，等待你通过缩小搜索范围、候选池或资料范围重新建立 Run。</p>
        </el-form-item>
        <el-form-item label="本轮搜索词（从策略中选择）">
          <el-checkbox-group v-model="selectedKeywords">
            <el-checkbox v-for="keyword in selectedStrategy?.search_keywords ?? []" :key="keyword" :label="keyword">{{ keyword }}</el-checkbox>
          </el-checkbox-group>
        </el-form-item>
        <el-form-item label="本轮城市（从策略中选择）">
          <el-checkbox-group v-model="selectedCities">
            <el-checkbox v-for="city in selectedStrategy?.cities ?? []" :key="city" :label="city">{{ city }}</el-checkbox>
          </el-checkbox-group>
        </el-form-item>
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
        </div>
        <el-alert
          v-if="workflowRun"
          :title="`Run 状态：${workflowRun.status}；当前步骤：${workflowRun.current_step}`"
          :description="workflowRun.next_action_reason"
          :type="workflowRun.waiting_for_user ? 'warning' : 'info'"
          :closable="false"
          show-icon
        />
      </el-form>
    </el-card>
  </section>
</template>

<style scoped>
.task-cockpit { display: grid; gap: 16px; }
.run-form { max-width: 760px; }
.codex-config { display: grid; gap: 10px; }
.codex-config { grid-template-columns: minmax(220px, 1fr) 160px; }
</style>
