<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import { api } from "@/services/api";
import { useFineJobCodexStore } from "@/stores/fineJobCodex";
import { useFineJobWorkflowRunStore } from "@/stores/fineJobWorkflowRun";
import type {
  FineJobFilterStrategy,
  FineJobRecommendationStrategy,
  FineJobWorkflowAnalysisItem,
  FineJobWorkflowContextSnapshot
} from "@/types";

const workflowRunId = ref("");
const contextChannel = ref("deep_job_search");
const analysisTaskId = ref("");
const snapshot = ref<FineJobWorkflowContextSnapshot | null>(null);
const strategies = ref<FineJobFilterStrategy[]>([]);
const recommendationStrategies = ref<FineJobRecommendationStrategy[]>([]);
const analysisItems = ref<FineJobWorkflowAnalysisItem[]>([]);
const selectedStrategyId = ref("");
const selectedRecommendationStrategyId = ref("");
const codexModel = ref("");
const codexReasoningEffort = ref<"minimal" | "low" | "medium" | "high" | "xhigh">("medium");
const codexModels = ref<Array<{ id: string; label?: string | null; reasoning_efforts?: string[] }>>([]);
const analysisGuidance = ref("");
const selectedAnalysisItem = ref<FineJobWorkflowAnalysisItem | null>(null);
const selectedItemContext = ref<Record<string, unknown> | null>(null);
const showAnalysisDetail = ref(false);
const feedbackReason = ref("technical_direction");
const selectedKeywords = ref<string[]>([]);
const selectedCities = ref<string[]>([]);
const targetCount = ref(5);
const candidateTargetCount = ref(15);
const contextSoftBudgetCharacters = ref(12000);
const router = useRouter();
const route = useRoute();
const codexStore = useFineJobCodexStore();
const workflowStore = useFineJobWorkflowRunStore();
const error = ref("");
const workflowRun = computed(() => workflowStore.currentRun);
const hasCurrentWorkflowCodexSession = computed(() => Boolean(
  workflowRun.value?.codex_session_ref
    && codexStore.status === "running"
    && workflowRun.value.codex_session_ref === codexStore.sessionRef
));
const hasEndedRuntimeWorkflowSession = computed(() => Boolean(
  workflowRun.value?.codex_session_ref?.startsWith("runtime:")
    && !hasCurrentWorkflowCodexSession.value
));

const selectedStrategy = computed(
  () => strategies.value.find((item) => item.id === selectedStrategyId.value) ?? null
);
const selectedRecommendationStrategy = computed(() =>
  recommendationStrategies.value.find((item) => item.id === selectedRecommendationStrategyId.value) ?? null
);
const compatibleRecommendationStrategies = computed(() => recommendationStrategies.value.filter(
  (item) => item.filter_strategy_id === selectedStrategyId.value
));
const activeAnalysisItem = computed(() => analysisItems.value.find((item) => item.status === "running") ?? null);

const syncStrategyScope = () => {
  selectedKeywords.value = [...(selectedStrategy.value?.search_keywords ?? [])];
  selectedCities.value = [...(selectedStrategy.value?.cities ?? [])];
};

const loadSnapshot = async () => {
  const identifier = workflowRunId.value.trim();
  if (!identifier) return;
  error.value = "";
  try {
    const channel = contextChannel.value === "analysis_item"
      ? `analysis_item:${analysisTaskId.value.trim()}`
      : contextChannel.value;
    if (contextChannel.value === "analysis_item" && !analysisTaskId.value.trim()) {
      error.value = "查看单个分析 Item Context 前，请输入 Workflow Task ID。";
      return;
    }
    const [loadedSnapshot] = await Promise.all([
      api.getFineJobWorkflowContextSnapshot(identifier, channel),
      workflowStore.refresh(identifier)
    ]);
    snapshot.value = loadedSnapshot;
  } catch (value) {
    error.value = value instanceof Error ? value.message : String(value);
  }
};

const loadAnalysisItems = async () => {
  const identifier = workflowRunId.value.trim();
  if (!identifier) return;
  analysisItems.value = (await api.listFineJobWorkflowAnalysisItems(identifier)).items;
};

const viewAnalysisItem = async (item: FineJobWorkflowAnalysisItem) => {
  selectedAnalysisItem.value = item;
  selectedItemContext.value = await api.getFineJobWorkflowAnalysisItemContext(
    workflowRunId.value, item.workflow_task_id
  );
  showAnalysisDetail.value = true;
};

const listText = (value: unknown) => Array.isArray(value) ? value.map((item) => String(item)).join("；") : "";

const saveFeedback = async (item: FineJobWorkflowAnalysisItem, sentiment: "expected" | "unexpected") => {
  await api.saveFineJobWorkflowAnalysisFeedback(workflowRunId.value, item.workflow_task_id, {
    sentiment,
    reason: sentiment === "unexpected" ? feedbackReason.value : undefined
  });
  await loadAnalysisItems();
};

const saveAnalysisGuidance = async () => {
  if (!workflowRunId.value) return;
  const run = await api.updateFineJobWorkflowAnalysisGuidance(workflowRunId.value, analysisGuidance.value);
  workflowStore.setRun(run);
};

const createRun = async () => {
  const strategy = selectedStrategy.value;
  if (!strategy?.id || !selectedRecommendationStrategy.value?.id || !codexModel.value || !selectedKeywords.value.length || !selectedCities.value.length) {
    error.value = "请完成筛选策略、建议投递策略、Codex 模型、搜索词和城市选择。";
    return;
  }
  error.value = "";
  try {
    const run = await workflowStore.create({
      filter_strategy_id: strategy.id,
      recommendation_strategy_id: selectedRecommendationStrategy.value.id,
      codex_model: codexModel.value,
      codex_reasoning_effort: codexReasoningEffort.value,
      analysis_guidance: analysisGuidance.value,
      target_count: targetCount.value,
      candidate_target_count: candidateTargetCount.value,
      allowed_search_keywords: selectedKeywords.value,
      allowed_cities: selectedCities.value,
      context_soft_budget_characters: contextSoftBudgetCharacters.value
    });
    workflowRunId.value = run?.workflow_run_id ?? "";
    await loadSnapshot();
    await loadAnalysisItems();
  } catch (value) {
    error.value = value instanceof Error ? value.message : String(value);
  }
};

const advanceRun = async () => {
  const identifier = workflowRunId.value.trim();
  if (!identifier) return;
  error.value = "";
  try {
    await workflowStore.refresh(identifier);
    await workflowStore.advance();
    await loadSnapshot();
    await loadAnalysisItems();
  } catch (value) {
    error.value = value instanceof Error ? value.message : String(value);
  }
};

const resumeRun = async () => {
  const identifier = workflowRunId.value.trim();
  if (!identifier) return;
  error.value = "";
  try {
    await workflowStore.refresh(identifier);
    await workflowStore.resume();
    await loadSnapshot();
  } catch (value) {
    error.value = value instanceof Error ? value.message : String(value);
  }
};

const pauseRun = async () => {
  if (!workflowRun.value) return;
  error.value = "";
  try {
    await workflowStore.pause();
  } catch (value) {
    error.value = value instanceof Error ? value.message : String(value);
  }
};

const cancelRun = async () => {
  if (!workflowRun.value) return;
  error.value = "";
  try {
    await workflowStore.cancel();
  } catch (value) {
    error.value = value instanceof Error ? value.message : String(value);
  }
};

const openWorkflowCodex = async (action: "submit" | "view") => {
  const currentRun = workflowRun.value;
  if (!currentRun) return;
  if (action === "submit" && currentRun.status !== "waiting_codex") return;
  if (action === "view" && !hasCurrentWorkflowCodexSession.value) return;
  await router.push({
    name: "fine-job-codex",
    query: {
      task: "deep-job-search",
      workflow_run_id: currentRun.workflow_run_id,
      workflow_action: action
    }
  });
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
    const routeRunId = String(route.query.workflow_run_id || "").trim();
    if (routeRunId) {
      workflowRunId.value = routeRunId;
      await workflowStore.refresh(routeRunId);
    } else {
      const restored = await workflowStore.restoreLatest();
      workflowRunId.value = restored?.workflow_run_id ?? "";
    }
    if (workflowRunId.value) await loadSnapshot();
    if (workflowRunId.value) {
      analysisGuidance.value = workflowRun.value?.completion_contract?.analysis_guidance?.text || "";
      await loadAnalysisItems();
    }
  } catch (value) {
    error.value = value instanceof Error ? value.message : String(value);
  }
});

watch(workflowRun, (run) => {
  if (run) {
    workflowRunId.value = run.workflow_run_id;
    analysisGuidance.value = run.completion_contract?.analysis_guidance?.text || analysisGuidance.value;
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
        <el-form-item label="本轮要完成的推荐岗位数">
          <el-input-number v-model="targetCount" :min="1" :max="100" />
        </el-form-item>
        <el-form-item label="候选池目标">
          <el-input-number v-model="candidateTargetCount" :min="targetCount" :max="500" />
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
        <el-button type="primary" :loading="workflowStore.loading" @click="createRun">建立并自动推进</el-button>
      </el-form>
    </el-card>
    <div class="inspector-input">
      <el-input v-model="workflowRunId" placeholder="输入 Workflow Run ID 查看本轮上下文" clearable @keyup.enter="loadSnapshot" />
      <el-select v-model="contextChannel" class="channel-select">
        <el-option label="搜索 Context" value="deep_job_search" />
        <el-option label="分析 Shared Base" value="candidate_analysis" />
      </el-select>
      <el-button type="primary" :loading="workflowStore.loading" @click="loadSnapshot">查看本轮上下文</el-button>
      <el-button :loading="workflowStore.advancing" :disabled="!workflowRunId" @click="advanceRun">立即推进</el-button>
      <el-button v-if="workflowRun && workflowRun.status !== 'paused' && !['cancelled', 'completed', 'completed_with_errors', 'failed'].includes(workflowRun.status)" @click="pauseRun">暂停</el-button>
      <el-button v-if="workflowRun?.status === 'paused' || (workflowRun?.status === 'waiting_for_user' && ['capture_interrupted', 'browser_not_running'].includes(workflowRun.stop_reason))" :loading="workflowStore.advancing" @click="resumeRun">继续</el-button>
      <el-button v-if="workflowRun && !['cancelled', 'completed', 'completed_with_errors', 'failed'].includes(workflowRun.status)" type="danger" plain @click="cancelRun">停止任务</el-button>
      <el-button v-if="workflowRun?.status === 'waiting_codex'" type="primary" @click="openWorkflowCodex('submit')">交给 Codex 分析</el-button>
      <el-button v-else-if="hasCurrentWorkflowCodexSession" type="primary" @click="openWorkflowCodex('view')">查看 Codex 分析</el-button>
    </div>
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <el-alert
      v-if="hasEndedRuntimeWorkflowSession"
      title="原 Codex 会话不可恢复；下一批进入 waiting_codex 后可重新交给 Codex 分析。"
      type="info"
      :closable="false"
      show-icon
    />
    <el-alert
      v-if="workflowRun"
      :title="`Run 状态：${workflowRun.status}；当前步骤：${workflowRun.current_step}`"
      :description="workflowRun.next_action_reason"
      :type="workflowRun.waiting_for_user ? 'warning' : 'info'"
      :closable="false"
      show-icon
    />
    <el-descriptions v-if="workflowRun" :column="3" border>
      <el-descriptions-item label="Workflow Run ID"><code>{{ workflowRun.workflow_run_id }}</code></el-descriptions-item>
      <el-descriptions-item label="目标推荐数">{{ workflowRun.completion_contract?.target_count ?? targetCount }}</el-descriptions-item>
      <el-descriptions-item label="正式 recommend">{{ workflowRun.completed_count }}</el-descriptions-item>
      <el-descriptions-item label="剩余目标">{{ workflowRun.remaining_count }}</el-descriptions-item>
      <el-descriptions-item label="当前搜索">{{ workflowRun.progress.current_keyword || '等待开始' }} / {{ workflowRun.progress.current_city || '—' }}</el-descriptions-item>
      <el-descriptions-item label="搜索深度 / 批次">{{ workflowRun.progress.search_depth }} / {{ workflowRun.progress.search_batch_count }}</el-descriptions-item>
      <el-descriptions-item label="岗位：已见 / Fresh / 重复">{{ workflowRun.progress.jobs_seen }} / {{ workflowRun.progress.fresh_jobs }} / {{ workflowRun.progress.duplicate_jobs }}</el-descriptions-item>
      <el-descriptions-item label="初筛候选">{{ workflowRun.progress.candidates }}</el-descriptions-item>
      <el-descriptions-item label="JD：完成 / 已建">{{ workflowRun.progress.jd_completed }} / {{ workflowRun.progress.jd_total }}</el-descriptions-item>
      <el-descriptions-item label="分析：推荐 / 复核 / 拒绝">{{ workflowRun.progress.recommend_count }} / {{ workflowRun.progress.review_count }} / {{ workflowRun.progress.reject_count }}</el-descriptions-item>
      <el-descriptions-item label="当前状态">{{ workflowRun.status }} / {{ workflowRun.current_step }}</el-descriptions-item>
      <el-descriptions-item label="下一步">{{ workflowRun.next_action }}</el-descriptions-item>
      <el-descriptions-item label="下一步原因">{{ workflowRun.next_action_reason }}</el-descriptions-item>
      <el-descriptions-item label="建议投递策略">{{ workflowRun.completion_contract?.selected_strategy_ids?.recommendation_strategy_id || '—' }}</el-descriptions-item>
      <el-descriptions-item label="本 Run 模型">{{ workflowRun.completion_contract?.codex_execution_config?.model || '—' }}</el-descriptions-item>
      <el-descriptions-item label="推理强度">{{ workflowRun.completion_contract?.codex_execution_config?.reasoning_effort || '—' }}</el-descriptions-item>
    </el-descriptions>
    <section v-if="workflowRun" class="surface-card analysis-guidance-card">
      <div class="card-actions">
        <strong>本 Run 分析指导 v{{ workflowRun.completion_contract?.analysis_guidance?.version || 1 }}</strong>
        <el-button @click="saveAnalysisGuidance">保存指导</el-button>
      </div>
      <el-input v-model="analysisGuidance" type="textarea" :rows="2" placeholder="补充要求会保存并作用于后续批次" />
    </section>
    <section v-if="workflowRun" class="surface-card">
      <div class="page-heading">
        <div>
          <h3>待分析岗位队列</h3>
          <p class="secondary-text">本批进入 Codex 前的岗位与筛选依据均来自后端持久化记录。</p>
        </div>
        <el-button @click="loadAnalysisItems">刷新</el-button>
      </div>
      <el-table :data="analysisItems">
        <el-table-column label="岗位" min-width="180"><template #default="scope">{{ scope.row.job.title }}</template></el-table-column>
        <el-table-column label="公司" min-width="140"><template #default="scope">{{ scope.row.job.company }}</template></el-table-column>
        <el-table-column label="薪资 / 城市" min-width="140"><template #default="scope">{{ scope.row.job.salary }} / {{ scope.row.job.city }}</template></el-table-column>
        <el-table-column label="来源" min-width="140"><template #default="scope">{{ scope.row.job.discovery_keyword }} · 深度 {{ scope.row.job.discovery_depth }}</template></el-table-column>
        <el-table-column label="初筛" min-width="160"><template #default="scope">{{ scope.row.job.filter_result }}：{{ scope.row.job.filter_reasons.join('；') }}</template></el-table-column>
        <el-table-column label="JD / Item" min-width="130"><template #default="scope">{{ scope.row.job.jd_status }} / {{ scope.row.status }}</template></el-table-column>
        <el-table-column label="操作" width="100"><template #default="scope"><el-button link @click="viewAnalysisItem(scope.row)">查看详情</el-button></template></el-table-column>
      </el-table>
    </section>
    <section v-if="workflowRun" class="surface-card">
      <div class="page-heading"><div><h3>分析结果</h3><p class="secondary-text">展示可审计的结构化依据，不保存模型内部思维链。</p></div></div>
      <el-alert v-if="activeAnalysisItem" type="info" :closable="false" :title="`正在分析：${activeAnalysisItem.job.title} · ${activeAnalysisItem.job.company}`" />
      <el-empty v-if="!analysisItems.some((item) => item.status === 'succeeded')" description="当前尚无已保存分析结果；进行中会在 Codex 分析工作台显示正在处理的 Item。" />
      <div v-for="item in analysisItems.filter((entry) => entry.status === 'succeeded')" :key="item.workflow_task_id" class="analysis-result-card">
        <div class="card-actions"><strong>{{ item.job.title }} · {{ item.job.company }}</strong><el-tag>{{ item.analysis_result.decision }}</el-tag></div>
        <p>硬条件：{{ listText(item.analysis_result.hard_requirements) || '未提供' }}</p>
        <p>匹配点：{{ listText(item.analysis_result.strengths) || '未提供' }}</p>
        <p>差距：{{ listText(item.analysis_result.gaps) || '无' }}</p>
        <p>风险：{{ listText(item.analysis_result.risks) || '无' }}</p>
        <p>证据：JD {{ listText(item.analysis_result.jd_evidence) || '未提供' }}；候选人 {{ listText(item.analysis_result.candidate_evidence) || '未提供' }}</p>
        <p>策略：{{ JSON.stringify(item.analysis_result.applied_strategy || {}) }}</p>
        <div class="card-actions">
          <el-button link @click="viewAnalysisItem(item)">查看详情</el-button>
          <el-button size="small" @click="saveFeedback(item, 'expected')">符合预期</el-button>
          <el-select v-model="feedbackReason" size="small" class="feedback-reason"><el-option label="技术方向不符合" value="technical_direction" /><el-option label="薪资" value="salary" /><el-option label="公司" value="company" /><el-option label="年限/学历" value="experience_or_education" /><el-option label="工作制" value="work_schedule" /><el-option label="地点" value="location" /><el-option label="AI 对 JD 理解错误" value="jd_understanding" /><el-option label="AI 对我的经历理解错误" value="candidate_understanding" /><el-option label="其他" value="other" /></el-select>
          <el-button size="small" @click="saveFeedback(item, 'unexpected')">不符合预期</el-button>
        </div>
      </div>
    </section>
    <el-dialog v-model="showAnalysisDetail" title="Workflow 分析 Item 详情" width="80%">
      <pre>{{ JSON.stringify({ item: selectedAnalysisItem, context: selectedItemContext }, null, 2) }}</pre>
    </el-dialog>
    <section v-if="workflowRun?.status === 'completed'" class="surface-card">
      <h3>本轮完成结果</h3>
      <p>本轮共分析 {{ workflowRun.progress.recommend_count + workflowRun.progress.review_count + workflowRun.progress.reject_count }}；Recommend {{ workflowRun.progress.recommend_count }}；Review {{ workflowRun.progress.review_count }}；Reject {{ workflowRun.progress.reject_count }}；进入待确认 {{ workflowRun.progress.recommend_count }}。</p>
      <div v-for="item in analysisItems.filter((entry) => entry.analysis_result.decision === 'recommend')" :key="`completed-${item.workflow_task_id}`" class="analysis-result-card"><strong>{{ item.job.title }} · {{ item.job.company }}</strong><p>{{ listText(item.analysis_result.reasons) }}</p><p>风险：{{ listText(item.analysis_result.risks) || '无' }}</p><el-button link @click="router.push({ name: 'fine-job-review' })">去待确认</el-button></div>
    </section>
    <template v-if="snapshot">
      <el-alert
        :title="`任务通道：${snapshot.channel}；后端快照 ${snapshot.status === 'ready' ? '可用' : '被预算阻断'}`"
        :description="`实际纳入 ${snapshot.context_characters} 字，估算 ${snapshot.estimated_tokens} token；软预算 ${snapshot.soft_budget_characters} 字。`"
        :type="snapshot.status === 'ready' ? 'success' : 'warning'"
        :closable="false"
        show-icon
      />
      <el-table :data="snapshot.sections" class="context-table">
        <el-table-column prop="section_id" label="Section" min-width="180" />
        <el-table-column prop="section_type" label="类型" min-width="130" />
        <el-table-column label="纳入" width="90">
          <template #default="scope"><el-tag :type="scope.row.included ? 'success' : 'info'">{{ scope.row.included ? '已纳入' : '未注入' }}</el-tag></template>
        </el-table-column>
        <el-table-column prop="character_count" label="字符量" width="100" />
        <el-table-column prop="estimated_tokens" label="估算 token" width="120" />
        <el-table-column prop="source" label="来源" min-width="140" />
        <el-table-column prop="source_version" label="版本" width="90" />
        <el-table-column prop="exclusion_reason" label="排除说明" min-width="240" />
      </el-table>
      <el-collapse class="context-content">
        <el-collapse-item v-for="section in snapshot.sections" :key="section.section_id" :name="section.section_id">
          <template #title>{{ section.section_id }}：{{ section.included ? "实际注入内容" : "未注入说明" }}</template>
          <pre>{{ section.included ? JSON.stringify(section.content, null, 2) : section.exclusion_reason }}</pre>
        </el-collapse-item>
      </el-collapse>
    </template>
  </section>
</template>

<style scoped>
.task-cockpit { display: grid; gap: 16px; }
.inspector-input { display: flex; gap: 12px; max-width: 1100px; flex-wrap: wrap; }
.channel-select { width: 180px; }
.context-table { width: 100%; }
.run-form { max-width: 760px; }
.context-content pre { margin: 0; white-space: pre-wrap; word-break: break-word; }
.codex-config, .analysis-result-card { display: grid; gap: 10px; }
.codex-config { grid-template-columns: minmax(220px, 1fr) 160px; }
.analysis-guidance-card, .analysis-result-card { padding: 14px; border: 1px solid var(--line); border-radius: var(--radius-control); }
.analysis-result-card p { margin: 0; white-space: pre-wrap; }
.feedback-reason { width: 190px; }
</style>
