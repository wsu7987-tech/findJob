<script setup lang="ts">
import { computed, h, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import { useRouter } from "vue-router";
import { ElButton, ElMessage, ElMessageBox } from "element-plus";
import { CopyDocument } from "@element-plus/icons-vue";

import { formatDateTime } from "@/services/format";
import { api } from "@/services/api";
import { useFineJobBossExecutorStore } from "@/stores/fineJobBossExecutor";
import { useFineJobDeliveryRunsStore } from "@/stores/fineJobDeliveryRuns";
import type { FineJobActionLog, FineJobBossExecutorQueueAction } from "@/types";

const runsStore = useFineJobDeliveryRunsStore();
const executorStore = useFineJobBossExecutorStore();
const router = useRouter();
const queueQuery = ref("");
const queueState = ref("");
const issueQuery = ref("");
const issueLevel = ref("");
const testTaskDrawerOpen = ref(false);
const testTaskSubmitting = ref(false);
const testJobDialogOpen = ref(false);
const testJobSaving = ref(false);
const executorSettingsSaving = ref(false);
const testTaskForm = reactive({ jobId: "", taskType: "greeting" as "greeting" | "resume" | "chat", closePageAfterCompletion: false, delaySeconds: 3 });
const testJobForm = reactive({ id: "", encryptJobId: "", jobLink: "" });
const executorSettingsForm = reactive({ taskCooldownMaxSeconds: 4, pageLoadWaitMaxSeconds: 3 });

const dashboard = computed(() => runsStore.dashboard);
const executor = computed(() => dashboard.value?.executor ?? null);
const currentTask = computed(() => dashboard.value?.current_task ?? null);
const filteredQueue = computed(() => (dashboard.value?.queue.actions ?? []).filter((item) => {
  const keyword = queueQuery.value.trim().toLowerCase();
  const matchesKeyword = !keyword || `${item.task_type} ${item.task_detail} ${item.job_title} ${item.company_name}`.toLowerCase().includes(keyword);
  const matchesState = !queueState.value || item.execution_state === queueState.value;
  return matchesKeyword && matchesState;
}));
const filteredIssues = computed(() => (dashboard.value?.recent_issues ?? []).filter((item) => {
  const keyword = issueQuery.value.trim().toLowerCase();
  const matchesKeyword = !keyword || `${item.message} ${item.action_type} ${item.job_title ?? ""}`.toLowerCase().includes(keyword);
  return matchesKeyword && (!issueLevel.value || item.level === issueLevel.value);
}));

const load = async () => {
  try {
    await Promise.all([runsStore.loadDashboard(), executorStore.loadTestJobs()]);
  } catch {
    ElMessage.error(runsStore.error ?? "执行队列加载失败");
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
  if (executor.value?.runtime_phase === "page_opening") {
    return executor.value.runtime_detail || "正在打开任务页面";
  }
  if (executor.value?.runtime_phase === "page_matching") {
    return executor.value.runtime_detail || "任务页面已打开，正在等待插件匹配";
  }
  if (executor.value?.runtime_detail?.startsWith("打开任务页面失败：")) {
    return executor.value.runtime_detail;
  }
  if (currentTask.value) return `正在执行：${currentTask.value.job_title} · ${currentTask.value.company_name}`;
  if ((dashboard.value?.queue.total ?? 0) === 0) return "当前没有待执行任务";
  return "正在等待插件匹配任务页面";
});

const executionLabel = (state: string) => ({
  queued: "待处理", running: "执行中", succeeded: "已完成", cancelled: "已取消",
  blocked: "已阻断", failed: "执行失败", unknown: "结果未知"
} as Record<string, string>)[state] ?? state;
const taskTypeLabel = (action: FineJobBossExecutorQueueAction) => ({
  BOSS_DEFAULT_GREETING: "打招呼",
  BOSS_CHAT_RESUME: "发送简历",
  BOSS_CHAT_MESSAGE: "代聊",
  TEST_DELAY: `测试任务（${({ greeting: "打招呼", resume: "发简历", chat: "代聊" } as Record<string, string>)[action.test_task_type ?? "greeting"] ?? "打招呼"}）`
} as Record<string, string>)[action.task_type] ?? action.task_type;

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

const openIssueLogs = async () => {
  await router.push({ name: "fine-job-logs", query: { level: "issue" } });
};

const openIssueLog = async (log: FineJobActionLog) => {
  await router.push({ name: "fine-job-logs", query: { level: "issue", logId: log.id } });
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
  if (!action.job_id) return;
  try {
    await executorStore.openJob(action.job_id, "history");
    ElMessage.success("已在专用浏览器打开岗位");
  } catch {
    ElMessage.error(executorStore.error ?? "岗位页面打开失败");
  }
};

const copyChatMessage = async (message: string) => {
  try {
    await navigator.clipboard.writeText(message);
    ElMessage.success("本轮信息已复制");
  } catch {
    ElMessage.error("复制信息失败");
  }
};

const chooseChatDraftResolution = async (action: FineJobBossExecutorQueueAction) => {
  if (action.task_type !== "BOSS_CHAT_MESSAGE" || !action.session_id) return undefined;
  const detail = await api.getFineJobChatSession(action.session_id);
  const existingDraftText = (detail.draft?.final_text || detail.draft?.draft_text || "").trim();
  if (!existingDraftText) return undefined;
  try {
    await ElMessageBox.confirm(
      h("div", { class: "draft-conflict-dialog" }, [
        h("p", "当前会话已有草稿。请选择如何处理本轮已取消的发送消息："),
        h("p", { class: "draft-conflict-dialog__label" }, "本轮发送消息"),
        h("pre", { class: "draft-conflict-dialog__message" }, action.task_detail),
        h(ElButton, { link: true, type: "primary", onClick: () => void copyChatMessage(action.task_detail) }, () => "复制信息")
      ]),
      "取消发送确认",
      {
        type: "warning",
        confirmButtonText: "覆盖草稿",
        cancelButtonText: "丢弃本轮信息",
        distinguishCancelAndClose: true,
        closeOnClickModal: false
      }
    );
    return "overwrite" as const;
  } catch (reason) {
    if (reason === "cancel") return "discard" as const;
    return null;
  }
};

const returnToReview = async (action: FineJobBossExecutorQueueAction) => {
  try {
    let draftResolution: "overwrite" | "discard" | undefined;
    if (action.task_source === "chat") {
      const selectedResolution = await chooseChatDraftResolution(action);
      if (selectedResolution === null) return;
      draftResolution = selectedResolution;
      await api.returnFineJobChatSendActionToReview(action.id, draftResolution);
    }
    else await executorStore.returnToReview(action.id);
    await load();
    ElMessage.success(
      action.task_type === "BOSS_CHAT_MESSAGE"
        ? draftResolution === "discard" ? "已取消发送，已丢弃本轮信息" : "已取消发送，消息已恢复草稿"
        : "已取消发送"
    );
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : "取消发送失败");
  }
};

const markCompleted = async (action: FineJobBossExecutorQueueAction) => {
  try {
    await ElMessageBox.confirm(
      "确认该任务已实际完成？标记后将不再出现在执行队列中。",
      "标记已完成",
      { type: "warning", confirmButtonText: "标记已完成", cancelButtonText: "取消" }
    );
    await executorStore.markCompleted(action.id);
    await load();
    ElMessage.success("任务已标记为完成");
  } catch (value) {
    if (value !== "cancel" && value !== "close") ElMessage.error(executorStore.error ?? "标记完成失败");
  }
};

const requeue = async (action: FineJobBossExecutorQueueAction) => {
  try {
    await ElMessageBox.confirm(
      "确认将该任务重新加入执行队列？插件运行时会再次处理该任务。",
      "重新进入执行队列",
      { type: "warning", confirmButtonText: "重新加入", cancelButtonText: "取消" }
    );
    await executorStore.requeue(action.id);
    await load();
    ElMessage.success("任务已重新加入执行队列");
  } catch (value) {
    if (value !== "cancel" && value !== "close") ElMessage.error(executorStore.error ?? "重新加入执行队列失败");
  }
};

const canReturn = (action: FineJobBossExecutorQueueAction) =>
  action.task_source === "chat"
    ? ["queued", "leased"].includes(action.status)
    : !["running", "succeeded"].includes(action.execution_state);

const openCreateTestTask = () => {
  testTaskForm.jobId = executorStore.testJobs[0]?.id ?? "";
  testTaskForm.taskType = "greeting";
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
      test_task_type: testTaskForm.taskType,
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
  void load().then(() => {
    if (!executor.value?.browser_connected && !executorStore.pairingCode) {
      void createPairingCode();
    }
  });
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
        <h1>执行队列</h1>
        <p class="secondary-text">查看 BOSS 执行器连接状态、待执行动作与异常记录。</p>
      </div>
      <el-button :loading="runsStore.loading" @click="load">刷新</el-button>
    </div>

    <el-alert v-if="runsStore.error" type="error" title="执行队列加载失败" :description="runsStore.error" show-icon />

    <section class="page-panel executor-card">
      <div class="panel-title-row">
        <div><p class="panel-eyebrow">BOSS Executor</p><h2>BOSS 执行器</h2></div>
        <el-tag :type="executorStatusType">{{ executorStatusLabel }}</el-tag>
      </div>
      <div class="executor-layout">
        <div class="executor-main">
          <div v-if="!executor || !executor.browser_connected" class="connection-box">
            <div class="pairing-code-content">
              <span class="pairing-label">插件配对码</span>
              <strong>{{ executorStore.pairingCode || "正在生成配对码" }}</strong>
            </div>
            <div class="connection-actions">
              <el-button :icon="CopyDocument" :disabled="!executorStore.pairingCode" @click="copyPairingCode">复制</el-button>
              <el-button type="primary" @click="createPairingCode">重新生成配对码</el-button>
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
        </div>
        <div v-if="executor?.browser_connected" class="executor-settings">
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
        <el-table-column label="任务类型" min-width="120"><template #default="{ row }">{{ taskTypeLabel(row) }}</template></el-table-column>
        <el-table-column label="任务详情" min-width="280" show-overflow-tooltip><template #default="{ row }">{{ row.task_detail }}</template></el-table-column>
        <el-table-column prop="job_title" label="岗位" min-width="200" />
        <el-table-column prop="company_name" label="公司" min-width="150" />
        <el-table-column label="执行状态" min-width="150"><template #default="{ row }">{{ executionLabel(row.execution_state) }}</template></el-table-column>
        <el-table-column prop="last_error" label="最近错误" min-width="220" show-overflow-tooltip />
        <el-table-column label="操作" width="365" fixed="right">
          <template #default="{ row }">
            <el-button v-if="row.job_id" link type="primary" @click="openActionJob(row)">打开岗位</el-button>
            <el-button v-if="canReturn(row)" link @click="returnToReview(row)">{{ row.task_source === "chat" ? "取消发送" : "退回待确认" }}</el-button>
            <el-button v-if="['blocked', 'unknown'].includes(row.execution_state)" link type="success" @click="markCompleted(row)">标记已完成</el-button>
            <el-button v-if="['blocked', 'unknown'].includes(row.execution_state)" link type="warning" @click="requeue(row)">重新进入执行队列</el-button>
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
        <el-form-item label="测试任务类型">
          <el-radio-group v-model="testTaskForm.taskType">
            <el-radio value="greeting">打招呼</el-radio>
            <el-radio value="resume">发简历</el-radio>
            <el-radio value="chat">代聊</el-radio>
          </el-radio-group>
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
      <div class="panel-title-row"><div><p class="panel-eyebrow">Issues</p><h2>最近异常与警告</h2></div><el-button link type="primary" @click="openIssueLogs">查看全部日志</el-button></div>
      <div class="inline-filters">
        <el-input v-model="issueQuery" clearable placeholder="筛选岗位、动作或说明" />
        <el-select v-model="issueLevel" clearable placeholder="全部级别"><el-option label="警告" value="warning" /><el-option label="错误" value="error" /></el-select>
      </div>
      <el-table :data="filteredIssues" empty-text="近期没有异常" @row-click="openIssueLog">
        <el-table-column label="时间" width="180"><template #default="{ row }">{{ formatDateTime(row.created_at) }}</template></el-table-column>
        <el-table-column prop="job_title" label="岗位" min-width="160" />
        <el-table-column prop="action_type" label="动作" min-width="180" />
        <el-table-column prop="message" label="说明" min-width="300" />
      </el-table>
    </section>

    <el-collapse>
      <el-collapse-item name="test-jobs" title="测试岗位">
        <section class="table-panel test-jobs-panel">
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
.executor-settings {
  border: 1px solid var(--el-border-color-light);
  border-radius: 8px;
  background: var(--el-fill-color-extra-light);
}
.connection-box {
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
.pairing-label {
  display: block;
  margin-bottom: 6px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.pairing-code-content strong {
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
.test-jobs-panel { margin-top: 8px; }
@media (max-width: 900px) {
  .executor-layout {
    grid-template-columns: 1fr;
  }
  .connection-box,
  .connection-actions {
    justify-content: flex-start;
  }
}
</style>
