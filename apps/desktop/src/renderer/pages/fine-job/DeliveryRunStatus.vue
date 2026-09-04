<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { CopyDocument } from "@element-plus/icons-vue";

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
const testTaskDrawerOpen = ref(false);
const testTaskSubmitting = ref(false);
const testJobDialogOpen = ref(false);
const testJobSaving = ref(false);
const executorSettingsSaving = ref(false);
const testTaskForm = reactive({ jobId: "", closePageAfterCompletion: false, delaySeconds: 3 });
const testJobForm = reactive({ id: "", encryptJobId: "", jobLink: "" });
const executorSettingsForm = reactive({ taskCooldownMaxSeconds: 4, pageLoadWaitMaxSeconds: 3 });

const dashboard = computed(() => runsStore.dashboard);
const executor = computed(() => dashboard.value?.executor ?? null);
const currentTask = computed(() => dashboard.value?.current_task ?? null);
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
    await Promise.all([runsStore.loadDashboard(), executorStore.loadTestJobs()]);
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

const executorProgressText = computed(() => {
  if (executor.value?.runtime_phase === "task_cooldown") {
    return executor.value.runtime_detail || "任务间隔冷却等待";
  }
  if (currentTask.value) return `正在执行：${currentTask.value.job_title} · ${currentTask.value.company_name}`;
  if ((dashboard.value?.queue.total ?? 0) === 0) return "当前没有待执行任务";
  return "正在等待插件匹配任务页面";
});
const showPairingCode = computed(() =>
  Boolean(executorStore.pairingCode && !executor.value?.browser_connected)
);

const executionLabel = (state: string) => ({
  queued: "待处理", running: "执行中", succeeded: "已完成", cancelled: "已取消",
  blocked: "已阻断", failed: "执行失败", unknown: "结果未知"
} as Record<string, string>)[state] ?? state;

const control = async (command: "start" | "pause") => {
  try {
    if (command === "start") {
      await ElMessageBox.confirm(
        "确认让插件开始处理执行队列？",
        "开始运行",
        { type: "warning" }
      );
    }
    await executorStore.control(command);
    ElMessage.success(command === "pause" ? "插件已暂停" : "插件已开始运行");
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

const copyPairingCode = async () => {
  if (!executorStore.pairingCode) return;
  try {
    await navigator.clipboard.writeText(executorStore.pairingCode);
    ElMessage.success("配对码已复制");
  } catch {
    ElMessage.error("配对码复制失败");
  }
};

const normalizeSettingSeconds = (value: number, minSeconds: number) =>
  Math.min(600, Math.max(minSeconds, Math.floor(Number(value) || minSeconds)));

const saveExecutorSettings = async () => {
  executorSettingsSaving.value = true;
  executorSettingsForm.taskCooldownMaxSeconds = normalizeSettingSeconds(executorSettingsForm.taskCooldownMaxSeconds, 4);
  executorSettingsForm.pageLoadWaitMaxSeconds = normalizeSettingSeconds(executorSettingsForm.pageLoadWaitMaxSeconds, 3);
  try {
    await executorStore.updateSettings({
      task_cooldown_max_seconds: executorSettingsForm.taskCooldownMaxSeconds,
      page_load_wait_max_seconds: executorSettingsForm.pageLoadWaitMaxSeconds
    });
    ElMessage.success("执行器配置已保存");
  } catch {
    ElMessage.error(executorStore.error ?? "执行器配置保存失败");
  } finally {
    executorSettingsSaving.value = false;
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

const canReturn = (action: FineJobBossExecutorQueueAction) =>
  !["running", "succeeded"].includes(action.execution_state);

const openCreateTestTask = () => {
  testTaskForm.jobId = executorStore.testJobs[0]?.id ?? "";
  testTaskForm.closePageAfterCompletion = false;
  testTaskForm.delaySeconds = 3;
  testTaskDrawerOpen.value = true;
};

const createTestTask = async () => {
  if (!testTaskForm.jobId) {
    ElMessage.warning("请选择关联测试岗位");
    return;
  }
  testTaskSubmitting.value = true;
  try {
    await executorStore.createTestTask({
      job_id: testTaskForm.jobId,
      close_page_after_completion: testTaskForm.closePageAfterCompletion,
      delay_seconds: testTaskForm.delaySeconds
    });
    testTaskDrawerOpen.value = false;
    await load();
    ElMessage.success("测试任务已加入执行队列");
  } catch {
    ElMessage.error(executorStore.error ?? "新建测试任务失败");
  } finally {
    testTaskSubmitting.value = false;
  }
};

const openEditTestJob = (job: typeof executorStore.testJobs[number]) => {
  testJobForm.id = job.id;
  testJobForm.encryptJobId = job.encrypt_job_id;
  testJobForm.jobLink = job.job_link;
  testJobDialogOpen.value = true;
};

const saveTestJob = async () => {
  testJobSaving.value = true;
  try {
    await executorStore.updateTestJob(testJobForm.id, {
      encrypt_job_id: testJobForm.encryptJobId,
      job_link: testJobForm.jobLink
    });
    testJobDialogOpen.value = false;
    ElMessage.success("测试岗位已保存");
  } catch {
    ElMessage.error(executorStore.error ?? "保存测试岗位失败");
  } finally {
    testJobSaving.value = false;
  }
};

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

watch(
  () => executorStore.dashboard,
  (runtime) => {
    if (!runtime || !runsStore.dashboard) return;
    runsStore.dashboard = {
      ...runsStore.dashboard,
      executor: runtime.executor,
      current_task: runtime.current_task ?? null,
      queue: runtime.queue
    };
  },
  { deep: true }
);

watch(
  executor,
  (value) => {
    if (!value) return;
    executorSettingsForm.taskCooldownMaxSeconds = value.task_cooldown_max_seconds ?? 4;
    executorSettingsForm.pageLoadWaitMaxSeconds = value.page_load_wait_max_seconds ?? 3;
  },
  { immediate: true }
);

onMounted(() => {
  executorStore.startStatusSync();
  void load();
});
onBeforeUnmount(() => {
  executorStore.stopStatusSync();
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
      <article class="metric-card"><span>待处理任务</span><strong>{{ dashboard?.metrics.queued_actions ?? 0 }}</strong><p>等待执行器处理</p></article>
      <article class="metric-card"><span>已确认成功</span><strong>{{ dashboard?.metrics.successful_actions ?? 0 }}</strong><p>建立沟通结果</p></article>
      <article class="metric-card"><span>需要处理</span><strong>{{ dashboard?.metrics.issue_actions ?? 0 }}</strong><p>失败、阻断或未知</p></article>
    </div>

    <section class="page-panel executor-card">
      <div class="panel-title-row">
        <div><p class="panel-eyebrow">BOSS Executor</p><h2>BOSS 执行器</h2></div>
        <el-tag :type="executorStatusType">{{ executorStatusLabel }}</el-tag>
      </div>
      <div class="executor-layout">
        <div class="executor-main">
          <div v-if="!executor || !executor.browser_connected" class="connection-box">
            <div>
              <strong>{{ executor ? "等待插件控制通道" : "尚未配对插件" }}</strong>
              <p class="secondary-text">
                {{ executor ? "插件配对后会自动建立连接；也可以手动发起心跳测试。" : "先生成配对码，再到 BOSS 插件面板输入。" }}
              </p>
            </div>
            <div class="connection-actions">
              <el-button :loading="executorStore.heartbeatTesting" @click="testHeartbeat">心跳测试</el-button>
              <el-button v-if="executor" @click="disconnect">断开连接</el-button>
              <el-button type="primary" @click="createPairingCode">生成配对码</el-button>
            </div>
          </div>
          <template v-else>
            <el-descriptions :column="3" border>
              <el-descriptions-item label="队列状态">{{ executor.queue_state }}</el-descriptions-item>
              <el-descriptions-item label="风险状态">{{ executor.risk_state }}</el-descriptions-item>
              <el-descriptions-item label="最近心跳">{{ formatDateTime(executor.last_heartbeat_at || '') }}</el-descriptions-item>
            </el-descriptions>
            <el-alert
              :title="executorProgressText"
              type="info"
              :closable="false"
              show-icon
              class="current-task-alert"
            />
            <div class="executor-actions">
              <el-button :loading="executorStore.heartbeatTesting" @click="testHeartbeat">心跳测试</el-button>
              <el-button @click="disconnect">断开连接</el-button>
              <el-button
                :type="executor.queue_state === 'running' ? 'warning' : 'primary'"
                @click="control(executor.queue_state === 'running' ? 'pause' : 'start')"
              >{{ executor.queue_state === 'running' ? '暂停' : '开始运行' }}</el-button>
            </div>
          </template>
          <div v-if="showPairingCode" class="pairing-code-box">
            <div>
              <span class="pairing-label">插件配对码</span>
              <strong>{{ executorStore.pairingCode }}</strong>
              <p class="secondary-text">有效期至 {{ formatDateTime(executorStore.pairingExpiresAt || '') }}</p>
            </div>
            <el-button :icon="CopyDocument" @click="copyPairingCode">复制</el-button>
          </div>
        </div>
        <div v-if="executor" class="executor-settings">
          <div>
            <span>任务间隔上限</span>
            <el-input-number
              v-model="executorSettingsForm.taskCooldownMaxSeconds"
              :min="4"
              :max="600"
              :step="1"
              controls-position="right"
            />
          </div>
          <div>
            <span>页面加载等待上限</span>
            <el-input-number
              v-model="executorSettingsForm.pageLoadWaitMaxSeconds"
              :min="3"
              :max="600"
              :step="1"
              controls-position="right"
            />
          </div>
          <el-button type="primary" plain :loading="executorSettingsSaving" @click="saveExecutorSettings">保存配置</el-button>
        </div>
      </div>
    </section>

    <section class="table-panel">
      <div class="panel-title-row">
        <div><p class="panel-eyebrow">Test Jobs</p><h2>测试岗位</h2></div>
        <el-button type="primary" @click="openCreateTestTask">新建测试任务</el-button>
      </div>
      <el-table :data="executorStore.testJobs" empty-text="正在初始化测试岗位">
        <el-table-column prop="title" label="岗位" min-width="150" />
        <el-table-column prop="id" label="岗位 ID" min-width="220" show-overflow-tooltip />
        <el-table-column prop="encrypt_job_id" label="encrypt_job_id" min-width="190" show-overflow-tooltip />
        <el-table-column prop="job_link" label="job_link" min-width="300" show-overflow-tooltip />
        <el-table-column label="操作" width="100" fixed="right">
          <template #default="{ row }"><el-button link type="primary" @click="openEditTestJob(row)">编辑测试岗位</el-button></template>
        </el-table-column>
      </el-table>
    </section>

    <section class="table-panel">
      <div class="panel-title-row"><div><p class="panel-eyebrow">Action Queue</p><h2>执行队列</h2></div><el-tag type="info">{{ dashboard?.queue.total ?? 0 }} 项</el-tag></div>
      <div class="inline-filters">
        <el-input v-model="queueQuery" clearable placeholder="筛选岗位或公司" />
        <el-select v-model="queueState" clearable placeholder="全部执行状态">
          <el-option label="待处理" value="queued" /><el-option label="执行中" value="running" />
          <el-option label="结果未知" value="unknown" />
          <el-option label="已阻断" value="blocked" />
        </el-select>
      </div>
      <el-table :data="filteredQueue" empty-text="当前筛选条件下暂无动作">
        <el-table-column prop="job_title" label="岗位" min-width="200" />
        <el-table-column prop="company_name" label="公司" min-width="150" />
        <el-table-column label="执行状态" min-width="150"><template #default="{ row }">{{ executionLabel(row.execution_state) }}</template></el-table-column>
        <el-table-column prop="last_error" label="最近错误" min-width="220" show-overflow-tooltip />
        <el-table-column label="操作" width="190" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="openActionJob(row)">打开岗位</el-button>
            <el-button v-if="canReturn(row)" link @click="returnToReview(row)">退回</el-button>
          </template>
        </el-table-column>
      </el-table>
    </section>

    <el-drawer v-model="testTaskDrawerOpen" title="新建测试任务" size="440px">
      <el-form label-position="top">
        <el-form-item label="关联测试岗位">
          <el-select v-model="testTaskForm.jobId" placeholder="选择测试岗位" class="form-full-width">
            <el-option v-for="job in executorStore.testJobs" :key="job.id" :label="`${job.title} · ${job.id}`" :value="job.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="执行完成后关闭页面">
          <el-switch v-model="testTaskForm.closePageAfterCompletion" />
          <p class="secondary-text">该开关只影响当前任务。</p>
        </el-form-item>
        <el-form-item label="运行时间（秒）">
          <el-input-number
            v-model="testTaskForm.delaySeconds"
            :min="1"
            :max="600"
            :step="1"
            controls-position="right"
          />
        </el-form-item>
        <el-button type="primary" :loading="testTaskSubmitting" @click="createTestTask">创建测试任务</el-button>
      </el-form>
    </el-drawer>

    <el-dialog v-model="testJobDialogOpen" title="编辑测试岗位" width="560px">
      <el-form label-position="top">
        <el-form-item label="岗位 ID"><el-input :model-value="testJobForm.id" disabled /></el-form-item>
        <el-form-item label="encrypt_job_id"><el-input v-model="testJobForm.encryptJobId" /></el-form-item>
        <el-form-item label="job_link"><el-input v-model="testJobForm.jobLink" /></el-form-item>
      </el-form>
      <template #footer><el-button @click="testJobDialogOpen = false">取消</el-button><el-button type="primary" :loading="testJobSaving" @click="saveTestJob">保存</el-button></template>
    </el-dialog>

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
.executor-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 280px;
  gap: 18px;
  align-items: start;
}
.executor-main {
  min-width: 0;
}
.connection-box,
.pairing-code-box,
.executor-settings {
  border: 1px solid var(--el-border-color-light);
  border-radius: 8px;
  background: var(--el-fill-color-extra-light);
}
.connection-box,
.pairing-code-box {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 16px;
}
.connection-box strong {
  display: block;
  margin-bottom: 4px;
  color: var(--el-text-color-primary);
}
.connection-actions {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
  justify-content: flex-end;
}
.pairing-code-box {
  margin-top: 14px;
  border-color: var(--el-color-warning-light-5);
  background: var(--el-color-warning-light-9);
}
.pairing-label {
  display: block;
  margin-bottom: 6px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.pairing-code-box strong {
  display: block;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 30px;
  line-height: 1.1;
  letter-spacing: 0;
  color: var(--el-text-color-primary);
}
.executor-settings {
  display: grid;
  gap: 12px;
  padding: 14px;
}
.executor-settings > div {
  display: grid;
  gap: 6px;
}
.executor-settings span {
  font-size: 13px;
  color: var(--el-text-color-secondary);
}
.executor-settings :deep(.el-input-number) {
  width: 100%;
}
.executor-actions, .inline-filters { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.executor-actions { justify-content: flex-end; margin-top: 16px; }
.current-task-alert { margin-top: 12px; }
.form-full-width { width: 100%; }
.inline-filters { margin-bottom: 16px; }
.inline-filters > * { width: min(320px, 100%); }
.row-error { color: var(--el-color-danger); }
.legacy-panel { margin-top: 8px; }
@media (max-width: 900px) {
  .executor-layout {
    grid-template-columns: 1fr;
  }
  .connection-box,
  .pairing-code-box {
    align-items: flex-start;
    flex-direction: column;
  }
  .connection-actions {
    justify-content: flex-start;
  }
}
</style>
