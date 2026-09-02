<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";

import { formatDateTime } from "@/services/format";
import { useFineJobBossExecutorStore } from "@/stores/fineJobBossExecutor";
import { useFineJobDeliveryRunsStore } from "@/stores/fineJobDeliveryRuns";
import type { FineJobBossExecutorQueueAction, FineJobDeliveryRun } from "@/types";

const runsStore = useFineJobDeliveryRunsStore();
const executorStore = useFineJobBossExecutorStore();
const queueQuery = ref("");
const queueState = ref("");
const issueQuery = ref("");
const issueLevel = ref("");
const legacyQuery = ref("");
const legacyStatus = ref("");
let pollTimer: number | null = null;

const dashboard = computed(() => runsStore.dashboard);
const executor = computed(() => dashboard.value?.executor ?? null);
const currentAction = computed(() => dashboard.value?.current_action ?? null);
const filteredQueue = computed(() => (dashboard.value?.queue.actions ?? []).filter((item) => {
  const keyword = queueQuery.value.trim().toLowerCase();
  const matchesKeyword = !keyword || `${item.job_title} ${item.company_name}`.toLowerCase().includes(keyword);
  const matchesState = !queueState.value || item.execution_state === queueState.value;
  return matchesKeyword && matchesState;
}));
const filteredIssues = computed(() => (dashboard.value?.recent_issues ?? []).filter((item) => {
  const keyword = issueQuery.value.trim().toLowerCase();
  const matchesKeyword = !keyword || `${item.message} ${item.action_type} ${item.job_title ?? ""}`.toLowerCase().includes(keyword);
  return matchesKeyword && (!issueLevel.value || item.level === issueLevel.value);
}));
const filteredLegacyRuns = computed(() => (dashboard.value?.legacy_runs ?? []).filter((item) => {
  const keyword = legacyQuery.value.trim().toLowerCase();
  const matchesKeyword = !keyword || `${item.id} ${item.stage} ${item.error_message ?? ""}`.toLowerCase().includes(keyword);
  return matchesKeyword && (!legacyStatus.value || item.status === legacyStatus.value);
}));

const load = async () => {
  try {
    await runsStore.loadDashboard();
  } catch {
    ElMessage.error(runsStore.error ?? "运行状态加载失败");
  }
};

const executorStatusLabel = computed(() => {
  if (!executor.value) return "未配对";
  if (!executor.value.browser_connected) return "FineJob未连接";
  if (executor.value.risk_state !== "none") return "风险暂停";
  return executor.value.queue_state === "running" ? "运行中" : "已暂停";
});

const executorStatusType = computed(() =>
  executorStatusLabel.value === "运行中" ? "success" : executorStatusLabel.value === "风险暂停" ? "danger" : "warning"
);

const executionLabel = (state: string) => ({
  queued: "排队中", opening_page: "正在打开岗位", waiting_page_ready: "等待页面就绪",
  page_verified: "页面已核对", ready_to_dispatch: "等待发送", dispatch_started: "正在发送",
  request_accepted: "平台已受理", succeeded: "已确认沟通", cancellation_requested: "正在取消",
  cancelled: "已取消", blocked: "已阻断", failed_before_dispatch: "发送前失败",
  failed_after_dispatch: "发送后失败", unknown_after_dispatch: "结果未知"
} as Record<string, string>)[state] ?? state;

const verificationLabel = (action: FineJobBossExecutorQueueAction) => ({
  not_required: "无需核验", waiting_refresh: "等待刷新核验", refreshing: "正在刷新",
  waiting_snapshot: "等待页面快照", page_confirmed: "页面已确认", manual_confirmed: "人工已确认",
  pending: "待后续确认", chat_confirmed: "聊天已确认"
} as Record<string, string>)[action.verification_state] ?? action.verification_state;

const control = async (command: "allow" | "pause" | "resume" | "emergency_stop") => {
  try {
    if (["allow", "resume", "emergency_stop"].includes(command)) {
      await ElMessageBox.confirm(
        command === "emergency_stop" ? "确认立即停止自动打招呼队列？" : "确认允许执行器继续串行处理已批准队列？",
        command === "emergency_stop" ? "紧急停止" : "恢复执行",
        { type: "warning" }
      );
    }
    await executorStore.control(command);
    await load();
    ElMessage.success(command === "pause" ? "执行器已暂停" : "执行器状态已更新");
  } catch (value) {
    if (value !== "cancel" && value !== "close") ElMessage.error(executorStore.error ?? "执行器控制失败");
  }
};

const createPairingCode = async () => {
  try {
    await executorStore.createPairingCode();
  } catch {
    ElMessage.error(executorStore.error ?? "生成配对码失败");
  }
};

const testHeartbeat = async () => {
  try {
    await executorStore.testHeartbeat();
    await load();
    ElMessage.success("心跳测试成功，FineJob已连接");
  } catch {
    await load().catch(() => undefined);
    ElMessage.error(executorStore.error ?? "心跳测试失败");
  }
};

const disconnect = async () => {
  try {
    await ElMessageBox.confirm(
      "断开后需要重新使用配对码连接插件，已保存的岗位和任务不会删除。",
      "断开 BOSS 插件连接",
      { type: "warning", confirmButtonText: "断开连接", cancelButtonText: "取消" }
    );
    await executorStore.disconnect();
    await load();
    ElMessage.success("BOSS 插件已断开");
  } catch (value) {
    if (value !== "cancel" && value !== "close") {
      ElMessage.error(executorStore.error ?? "断开插件连接失败");
    }
  }
};

const openActionJob = async (action: FineJobBossExecutorQueueAction) => {
  try {
    await executorStore.openJob(action.job_id, "history");
    ElMessage.success("已在专用浏览器打开岗位");
  } catch {
    ElMessage.error(executorStore.error ?? "岗位页面打开失败");
  }
};

const returnToReview = async (action: FineJobBossExecutorQueueAction) => {
  try {
    await executorStore.returnToReview(action.id);
    await load();
    ElMessage.success("已退回待确认");
  } catch {
    ElMessage.error(executorStore.error ?? "退回待确认失败");
  }
};

const manualVerify = async (action: FineJobBossExecutorQueueAction) => {
  await openActionJob(action);
  try {
    await ElMessageBox.confirm(
      "请在岗位页面核对：显示“继续沟通”表示已经沟通。",
      "人工核验",
      { confirmButtonText: "确认已沟通", cancelButtonText: "确认未沟通", distinguishCancelAndClose: true }
    );
    await executorStore.manualVerifyUnknown(action.id, true);
  } catch (value) {
    if (value !== "cancel") return;
    await executorStore.manualVerifyUnknown(action.id, false);
  }
  await load();
  ElMessage.success("人工核验结果已保存");
};

const canReturn = (action: FineJobBossExecutorQueueAction) => ![
  "dispatch_started", "request_accepted", "succeeded", "failed_after_dispatch", "unknown_after_dispatch"
].includes(action.execution_state);

const deleteLegacyRun = async (run: FineJobDeliveryRun) => {
  try {
    await ElMessageBox.confirm(
      `确认删除旧任务 ${run.id} 及其候选岗位和所属日志？`,
      "删除旧任务",
      { type: "warning", confirmButtonText: "确认删除" }
    );
    const result = await runsStore.deleteLegacyRun(run.id);
    ElMessage.success(`已删除任务、${result.candidates_deleted} 个候选岗位和 ${result.logs_deleted} 条日志`);
  } catch (value) {
    if (value !== "cancel" && value !== "close") ElMessage.error(runsStore.error ?? "删除旧任务失败");
  }
};

onMounted(() => {
  void load();
  pollTimer = window.setInterval(() => void load(), 5000);
});
onBeforeUnmount(() => {
  if (pollTimer !== null) window.clearInterval(pollTimer);
});
</script>

<template>
  <section class="page-stack fine-job-page">
    <div class="page-heading">
      <div>
        <p class="panel-eyebrow">Operations Center</p>
        <h1>运行状态</h1>
        <p class="secondary-text">汇总岗位采集、评估、审批、执行队列和 BOSS 执行器的当前数据。</p>
      </div>
      <el-button :loading="runsStore.loading" @click="load">刷新</el-button>
    </div>

    <el-alert v-if="runsStore.error" type="error" title="运行状态加载失败" :description="runsStore.error" show-icon />

    <div class="metric-grid">
      <article class="metric-card"><span>岗位总数</span><strong>{{ dashboard?.metrics.jobs ?? 0 }}</strong><p>历史采集主数据</p></article>
      <article class="metric-card"><span>已完成评估</span><strong>{{ dashboard?.metrics.evaluated_jobs ?? 0 }}</strong><p>按岗位去重</p></article>
      <article class="metric-card"><span>待确认</span><strong>{{ dashboard?.metrics.pending_reviews ?? 0 }}</strong><p>等待用户决策</p></article>
      <article class="metric-card"><span>排队动作</span><strong>{{ dashboard?.metrics.queued_actions ?? 0 }}</strong><p>等待执行器处理</p></article>
      <article class="metric-card"><span>已确认成功</span><strong>{{ dashboard?.metrics.successful_actions ?? 0 }}</strong><p>建立沟通结果</p></article>
      <article class="metric-card"><span>需要处理</span><strong>{{ dashboard?.metrics.issue_actions ?? 0 }}</strong><p>失败、阻断或未知</p></article>
    </div>

    <section class="page-panel executor-card">
      <div class="panel-title-row">
        <div><p class="panel-eyebrow">BOSS Executor</p><h2>BOSS 执行器</h2></div>
        <el-tag :type="executorStatusType">{{ executorStatusLabel }}</el-tag>
      </div>
      <el-empty v-if="!executor || !executor.browser_connected" description="等待BOSS执行器心跳连接">
        <el-button :loading="executorStore.heartbeatTesting" @click="testHeartbeat">心跳测试</el-button>
        <el-button v-if="executor" @click="disconnect">断开连接</el-button>
        <el-button type="primary" @click="createPairingCode">生成插件配对码</el-button>
      </el-empty>
      <template v-else>
        <el-descriptions :column="4" border>
          <el-descriptions-item label="自动权限">{{ executor.permission_state }}</el-descriptions-item>
          <el-descriptions-item label="队列状态">{{ executor.queue_state }}</el-descriptions-item>
          <el-descriptions-item label="风险状态">{{ executor.risk_state }}</el-descriptions-item>
          <el-descriptions-item label="最近心跳">{{ formatDateTime(executor.last_heartbeat_at || '') }}</el-descriptions-item>
        </el-descriptions>
        <div class="executor-actions">
          <el-button :loading="executorStore.heartbeatTesting" @click="testHeartbeat">心跳测试</el-button>
          <el-button @click="disconnect">断开连接</el-button>
          <el-button type="primary" @click="control(executor.permission_state === 'allowed' ? 'resume' : 'allow')">允许并运行</el-button>
          <el-button @click="control('pause')">暂停</el-button>
          <el-button type="danger" plain @click="control('emergency_stop')">紧急停止</el-button>
        </div>
      </template>
      <el-alert
        v-if="executorStore.pairingCode"
        type="warning"
        :closable="false"
        :title="`配对码：${executorStore.pairingCode}`"
        :description="`有效期至 ${formatDateTime(executorStore.pairingExpiresAt || '')}`"
        show-icon
      />
    </section>

    <section v-if="currentAction" class="page-panel current-action">
      <div><p class="panel-eyebrow">Current Action</p><h2>{{ currentAction.job_title }}</h2><p>{{ currentAction.company_name }}</p></div>
      <div class="current-action__status">
        <el-tag type="warning">{{ executionLabel(currentAction.execution_state) }}</el-tag>
        <span>{{ verificationLabel(currentAction) }}</span>
        <span v-if="currentAction.last_error" class="row-error">{{ currentAction.last_error }}</span>
      </div>
    </section>

    <section class="table-panel">
      <div class="panel-title-row"><div><p class="panel-eyebrow">Action Queue</p><h2>执行队列</h2></div><el-tag type="info">{{ dashboard?.queue.total ?? 0 }} 项</el-tag></div>
      <div class="inline-filters">
        <el-input v-model="queueQuery" clearable placeholder="筛选岗位或公司" />
        <el-select v-model="queueState" clearable placeholder="全部执行状态">
          <el-option label="排队中" value="queued" /><el-option label="正在发送" value="dispatch_started" />
          <el-option label="平台已受理" value="request_accepted" /><el-option label="结果未知" value="unknown_after_dispatch" />
          <el-option label="已阻断" value="blocked" />
        </el-select>
      </div>
      <el-table :data="filteredQueue" empty-text="当前筛选条件下暂无动作">
        <el-table-column prop="queue_position" label="#" width="60" />
        <el-table-column prop="job_title" label="岗位" min-width="200" />
        <el-table-column prop="company_name" label="公司" min-width="150" />
        <el-table-column label="执行状态" min-width="150"><template #default="{ row }">{{ executionLabel(row.execution_state) }}</template></el-table-column>
        <el-table-column label="核验状态" min-width="150"><template #default="{ row }">{{ verificationLabel(row) }}</template></el-table-column>
        <el-table-column prop="last_error" label="最近错误" min-width="220" show-overflow-tooltip />
        <el-table-column label="操作" width="190" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="openActionJob(row)">打开岗位</el-button>
            <el-button v-if="canReturn(row)" link @click="returnToReview(row)">退回</el-button>
            <el-button v-if="row.execution_state === 'unknown_after_dispatch'" link type="warning" @click="manualVerify(row)">人工核验</el-button>
          </template>
        </el-table-column>
      </el-table>
    </section>

    <section class="table-panel">
      <div class="panel-title-row"><div><p class="panel-eyebrow">Issues</p><h2>最近异常与警告</h2></div></div>
      <div class="inline-filters">
        <el-input v-model="issueQuery" clearable placeholder="筛选岗位、动作或说明" />
        <el-select v-model="issueLevel" clearable placeholder="全部级别"><el-option label="警告" value="warning" /><el-option label="错误" value="error" /></el-select>
      </div>
      <el-table :data="filteredIssues" empty-text="近期没有异常">
        <el-table-column label="时间" width="180"><template #default="{ row }">{{ formatDateTime(row.created_at) }}</template></el-table-column>
        <el-table-column prop="job_title" label="岗位" min-width="160" />
        <el-table-column prop="action_type" label="动作" min-width="180" />
        <el-table-column prop="message" label="说明" min-width="300" />
      </el-table>
    </section>

    <el-collapse>
      <el-collapse-item name="legacy" title="旧版 dry-run 调试任务">
        <section class="table-panel legacy-panel">
          <div class="inline-filters">
            <el-input v-model="legacyQuery" clearable placeholder="筛选任务 ID、阶段或错误" />
            <el-select v-model="legacyStatus" clearable placeholder="全部状态">
              <el-option label="已完成" value="completed" /><el-option label="已暂停" value="paused" />
              <el-option label="失败" value="failed" /><el-option label="运行中" value="running" />
            </el-select>
          </div>
          <el-table :data="filteredLegacyRuns" empty-text="没有旧版任务">
            <el-table-column prop="id" label="任务 ID" min-width="220" show-overflow-tooltip />
            <el-table-column prop="status" label="状态" width="100" />
            <el-table-column prop="stage" label="阶段" min-width="180" />
            <el-table-column prop="searched_count" label="候选" width="80" />
            <el-table-column label="开始时间" width="180"><template #default="{ row }">{{ formatDateTime(row.started_at) }}</template></el-table-column>
            <el-table-column label="操作" width="90"><template #default="{ row }"><el-button link type="danger" :disabled="['pending', 'running'].includes(row.status)" @click="deleteLegacyRun(row)">删除</el-button></template></el-table-column>
          </el-table>
        </section>
      </el-collapse-item>
    </el-collapse>
  </section>
</template>

<style scoped>
.executor-actions, .inline-filters, .current-action, .current-action__status { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.executor-actions { justify-content: flex-end; margin-top: 16px; }
.inline-filters { margin-bottom: 16px; }
.inline-filters > * { width: min(320px, 100%); }
.current-action { justify-content: space-between; }
.current-action__status { justify-content: flex-end; }
.row-error { color: var(--el-color-danger); }
.legacy-panel { margin-top: 8px; }
</style>
