<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import { api } from "@/services/api";
import { useFineJobWorkflowRunStore } from "@/stores/fineJobWorkflowRun";
import type {
  FineJobFilterStrategy,
  FineJobWorkflowContextSnapshot
} from "@/types";

const workflowRunId = ref("");
const contextChannel = ref("deep_job_search");
const analysisTaskId = ref("");
const snapshot = ref<FineJobWorkflowContextSnapshot | null>(null);
const strategies = ref<FineJobFilterStrategy[]>([]);
const selectedStrategyId = ref("");
const selectedKeywords = ref<string[]>([]);
const selectedCities = ref<string[]>([]);
const targetCount = ref(5);
const candidateTargetCount = ref(15);
const contextSoftBudgetCharacters = ref(12000);
const router = useRouter();
const route = useRoute();
const workflowStore = useFineJobWorkflowRunStore();
const error = ref("");
const workflowRun = computed(() => workflowStore.currentRun);

const selectedStrategy = computed(
  () => strategies.value.find((item) => item.id === selectedStrategyId.value) ?? null
);

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

const createRun = async () => {
  const strategy = selectedStrategy.value;
  if (!strategy?.id || !selectedKeywords.value.length || !selectedCities.value.length) {
    error.value = "请先选择策略，并至少保留一个搜索词和城市。";
    return;
  }
  error.value = "";
  try {
    const run = await workflowStore.create({
      filter_strategy_id: strategy.id,
      target_count: targetCount.value,
      candidate_target_count: candidateTargetCount.value,
      allowed_search_keywords: selectedKeywords.value,
      allowed_cities: selectedCities.value,
      context_soft_budget_characters: contextSoftBudgetCharacters.value
    });
    workflowRunId.value = run?.workflow_run_id ?? "";
    await loadSnapshot();
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

const handoffToCodex = async () => {
  const currentRun = workflowRun.value;
  if (!currentRun || currentRun.status !== "waiting_codex") return;
  await router.push({
    name: "fine-job-codex",
    query: { task: "deep-job-search", workflow_run_id: currentRun.workflow_run_id }
  });
};

onMounted(async () => {
  try {
    strategies.value = (await api.listFineJobFilterStrategies()).strategies.filter((item) => item.enabled);
    const routeRunId = String(route.query.workflow_run_id || "").trim();
    if (routeRunId) {
      workflowRunId.value = routeRunId;
      await workflowStore.refresh(routeRunId);
    } else {
      const restored = await workflowStore.restoreLatest();
      workflowRunId.value = restored?.workflow_run_id ?? "";
    }
    if (workflowRunId.value) await loadSnapshot();
  } catch (value) {
    error.value = value instanceof Error ? value.message : String(value);
  }
});

watch(workflowRun, (run) => {
  if (run) workflowRunId.value = run.workflow_run_id;
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
        <el-option label="分析 Item Context" value="analysis_item" />
      </el-select>
      <el-input v-if="contextChannel === 'analysis_item'" v-model="analysisTaskId" placeholder="Workflow Task ID" />
      <el-button type="primary" :loading="workflowStore.loading" @click="loadSnapshot">查看本轮上下文</el-button>
      <el-button :loading="workflowStore.advancing" :disabled="!workflowRunId" @click="advanceRun">立即推进</el-button>
      <el-button v-if="workflowRun && workflowRun.status !== 'paused' && !['cancelled', 'completed', 'completed_with_errors', 'failed'].includes(workflowRun.status)" @click="pauseRun">暂停</el-button>
      <el-button v-if="workflowRun?.status === 'paused' || (workflowRun?.status === 'waiting_for_user' && ['capture_interrupted', 'browser_not_running'].includes(workflowRun.stop_reason))" :loading="workflowStore.advancing" @click="resumeRun">继续</el-button>
      <el-button v-if="workflowRun && !['cancelled', 'completed', 'completed_with_errors', 'failed'].includes(workflowRun.status)" type="danger" plain @click="cancelRun">停止任务</el-button>
      <el-button v-if="workflowRun?.status === 'waiting_codex'" type="primary" @click="handoffToCodex">交给 Codex 分析</el-button>
    </div>
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
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
    </el-descriptions>
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
</style>
