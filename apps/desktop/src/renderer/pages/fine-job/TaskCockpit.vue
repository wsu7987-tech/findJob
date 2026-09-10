<script setup lang="ts">
import { computed, onMounted, ref } from "vue";

import { api } from "@/services/api";
import type {
  FineJobFilterStrategy,
  FineJobWorkflowContextSnapshot,
  FineJobWorkflowRun
} from "@/types";

const workflowRunId = ref("");
const contextChannel = ref("deep_job_search");
const analysisTaskId = ref("");
const snapshot = ref<FineJobWorkflowContextSnapshot | null>(null);
const workflowRun = ref<FineJobWorkflowRun | null>(null);
const strategies = ref<FineJobFilterStrategy[]>([]);
const selectedStrategyId = ref("");
const selectedKeywords = ref<string[]>([]);
const selectedCities = ref<string[]>([]);
const targetCount = ref(5);
const candidateTargetCount = ref(15);
const contextSoftBudgetCharacters = ref(12000);
const error = ref("");
const loading = ref(false);
const creating = ref(false);
const advancing = ref(false);

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
  loading.value = true;
  error.value = "";
  try {
    const channel = contextChannel.value === "analysis_item"
      ? `analysis_item:${analysisTaskId.value.trim()}`
      : contextChannel.value;
    if (contextChannel.value === "analysis_item" && !analysisTaskId.value.trim()) {
      error.value = "查看单个分析 Item Context 前，请输入 Workflow Task ID。";
      return;
    }
    const [loadedSnapshot, loadedRun] = await Promise.all([
      api.getFineJobWorkflowContextSnapshot(identifier, channel),
      api.getFineJobWorkflowRun(identifier)
    ]);
    snapshot.value = loadedSnapshot;
    workflowRun.value = loadedRun;
  } catch (value) {
    error.value = value instanceof Error ? value.message : String(value);
  } finally {
    loading.value = false;
  }
};

const createRun = async () => {
  const strategy = selectedStrategy.value;
  if (!strategy?.id || !selectedKeywords.value.length || !selectedCities.value.length) {
    error.value = "请先选择策略，并至少保留一个搜索词和城市。";
    return;
  }
  creating.value = true;
  error.value = "";
  try {
    workflowRun.value = await api.createFineJobDeepJobSearchRun({
      filter_strategy_id: strategy.id,
      target_count: targetCount.value,
      candidate_target_count: candidateTargetCount.value,
      allowed_search_keywords: selectedKeywords.value,
      allowed_cities: selectedCities.value,
      context_soft_budget_characters: contextSoftBudgetCharacters.value
    });
    workflowRunId.value = workflowRun.value.workflow_run_id;
    await loadSnapshot();
  } catch (value) {
    error.value = value instanceof Error ? value.message : String(value);
  } finally {
    creating.value = false;
  }
};

const advanceRun = async () => {
  const identifier = workflowRunId.value.trim();
  if (!identifier) return;
  advancing.value = true;
  error.value = "";
  try {
    workflowRun.value = await api.advanceFineJobWorkflowRun(identifier);
    await loadSnapshot();
  } catch (value) {
    error.value = value instanceof Error ? value.message : String(value);
  } finally {
    advancing.value = false;
  }
};

const resumeRun = async () => {
  const identifier = workflowRunId.value.trim();
  if (!identifier) return;
  advancing.value = true;
  error.value = "";
  try {
    workflowRun.value = await api.resumeFineJobWorkflowRun(identifier);
  } catch (value) {
    error.value = value instanceof Error ? value.message : String(value);
  } finally {
    advancing.value = false;
  }
};

onMounted(async () => {
  try {
    strategies.value = (await api.listFineJobFilterStrategies()).strategies.filter((item) => item.enabled);
  } catch (value) {
    error.value = value instanceof Error ? value.message : String(value);
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
        <el-form-item label="本轮要完成的推荐岗位数">
          <el-input-number v-model="targetCount" :min="1" :max="100" />
        </el-form-item>
        <el-form-item label="候选池目标">
          <el-input-number v-model="candidateTargetCount" :min="targetCount" :max="500" />
          <p class="secondary-text">默认值为 15；候选池达到该数值后，只补齐 {{ targetCount }} 个高优先级 JD，再进入 Codex 分析。</p>
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
        <el-button type="primary" :loading="creating" @click="createRun">建立 Run</el-button>
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
      <el-button type="primary" :loading="loading" @click="loadSnapshot">查看本轮上下文</el-button>
      <el-button :loading="advancing" :disabled="!workflowRunId" @click="advanceRun">推进 Run</el-button>
      <el-button v-if="workflowRun?.status === 'waiting_for_user' && ['capture_interrupted', 'browser_not_running'].includes(workflowRun.stop_reason)" :loading="advancing" @click="resumeRun">确认恢复</el-button>
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
      <el-descriptions-item label="目标推荐数">{{ workflowRun.completion_contract?.target_count ?? targetCount }}</el-descriptions-item>
      <el-descriptions-item label="正式 recommend">{{ workflowRun.completed_count }}</el-descriptions-item>
      <el-descriptions-item label="剩余目标">{{ workflowRun.remaining_count }}</el-descriptions-item>
      <el-descriptions-item label="fresh candidates">{{ workflowRun.telemetry.fresh_candidates ?? 0 }}</el-descriptions-item>
      <el-descriptions-item label="当前状态">{{ workflowRun.status }} / {{ workflowRun.current_step }}</el-descriptions-item>
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
