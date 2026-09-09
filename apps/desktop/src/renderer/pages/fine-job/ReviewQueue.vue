<script setup lang="ts">
import { computed, h, onBeforeUnmount, onMounted, ref } from "vue";
import { ElButton, ElMessage, ElMessageBox } from "element-plus";
import { useRouter } from "vue-router";

import { formatDateTime } from "@/services/format";
import { api } from "@/services/api";
import { useFineJobBossExecutorStore } from "@/stores/fineJobBossExecutor";
import { useFineJobWorkflowStore } from "@/stores/fineJobWorkflow";
import type { FineJobChatReviewTask, FineJobReviewItem, FineJobReviewTab } from "@/types";

const router = useRouter();
const workflowStore = useFineJobWorkflowStore();
const executorStore = useFineJobBossExecutorStore();
const selectedRows = ref<FineJobReviewItem[]>([]);
const detailItem = ref<FineJobReviewItem | null>(null);
const detailDrawerOpen = ref(false);
const navigationErrors = ref<Record<string, string>>({});
let executorPollTimer: number | null = null;

type ReviewQueueRow =
  | { id: string; kind: "greeting"; reviewItem: FineJobReviewItem }
  | { id: string; kind: "chat"; chatTask: FineJobChatReviewTask };

const reviewTabs: Array<{ label: string; name: FineJobReviewTab }> = [
  { label: "待确认", name: "pending" },
  { label: "不建议/已拒绝", name: "rejected" },
  { label: "已执行", name: "executed" },
  { label: "已归档", name: "dismissed" }
];

const reviewPageDescriptions: Record<FineJobReviewTab, string> = {
  pending: "集中审核推荐岗位和需要判断的岗位，批准后创建默认招呼执行任务。",
  approved: "查看已经批准的岗位和执行任务。",
  rejected: "查看 AI 不建议或用户拒绝的岗位，可明确覆盖结论。",
  executed: "查看已经执行完成的打招呼、代聊和简历发送任务。",
  dismissed: "保存用户主动归档和被新评估替代的历史事项。"
};

const pageDescription = computed(() => reviewPageDescriptions[workflowStore.selectedStatus]);
const reviewRows = computed<ReviewQueueRow[]>(() => {
  const greetingRows = workflowStore.items.map((reviewItem) => ({
    id: reviewItem.id,
    kind: "greeting" as const,
    reviewItem
  }));
  const chatTasks = workflowStore.selectedStatus === "pending"
    ? workflowStore.chatReviewTasks
    : workflowStore.selectedStatus === "executed"
      ? workflowStore.chatExecutedTasks
      : [];
  if (!chatTasks.length) return greetingRows;
  // 自动代聊任务与岗位打招呼任务复用主列表，按当前页签展示对应状态。
  return [
    ...chatTasks.map((chatTask) => ({
      id: chatTask.id,
      kind: "chat" as const,
      chatTask
    })),
    ...greetingRows
  ];
});

const executorLabel = computed(() => {
  const executor = executorStore.dashboard?.executor;
  if (!executor) return "未配对";
  if (!executor.browser_connected) return "FineJob未连接";
  if (executor.risk_state !== "none") return "风险暂停";
  if (executor.queue_state === "running") return "运行中";
  return "已暂停";
});

const executorType = computed(() => {
  if (executorLabel.value === "运行中") return "success";
  if (["未配对", "FineJob未连接"].includes(executorLabel.value)) return "info";
  return "warning";
});

const loadStatus = async (status: FineJobReviewTab, resetPage = false) => {
  if (resetPage) workflowStore.page = 1;
  try {
    await workflowStore.load(status);
    selectedRows.value = [];
  } catch {
    ElMessage.error(workflowStore.error ?? "待确认事项加载失败");
  }
};

const search = () => loadStatus(workflowStore.selectedStatus, true);
const handleTabChange = (name: string | number) =>
  loadStatus(String(name) as FineJobReviewTab, true);
const resetFilters = () => {
  workflowStore.query = "";
  workflowStore.decision = "";
  workflowStore.executionState = "";
  workflowStore.createdRange = null;
  void search();
};

const approve = async (item: FineJobReviewItem) => {
  if (item.status === "rejected") {
    try {
      await ElMessageBox.confirm(
        "该岗位原结论为不建议。确认后会创建 BOSS 默认招呼执行任务。",
        "仍要沟通",
        { type: "warning", confirmButtonText: "确认创建任务" }
      );
    } catch {
      return;
    }
  }
  try {
    await workflowStore.approve(item, "", item.status === "rejected");
    await executorStore.load();
    ElMessage.success("已创建 BOSS 默认招呼执行任务");
  } catch {
    ElMessage.error(workflowStore.error ?? "批准失败");
  }
};

const reject = async (item: FineJobReviewItem) => {
  try {
    await workflowStore.reject(item);
    ElMessage.success("已拒绝该事项");
  } catch {
    ElMessage.error(workflowStore.error ?? "拒绝失败");
  }
};

const archive = async (item: FineJobReviewItem) => {
  try {
    await workflowStore.archive(item);
    ElMessage.success("已归档该事项");
  } catch {
    ElMessage.error(workflowStore.error ?? "归档失败");
  }
};

const deleteGreeting = async (item: FineJobReviewItem) => {
  try {
    await workflowStore.archive(item, "用户删除待确认打招呼任务");
    ElMessage.success("已删除待确认打招呼任务");
  } catch {
    ElMessage.error(workflowStore.error ?? "删除失败");
  }
};

const restore = async (item: FineJobReviewItem) => {
  try {
    await workflowStore.restore(item);
    ElMessage.success("已恢复到待确认");
  } catch {
    ElMessage.error(workflowStore.error ?? "恢复失败");
  }
};

const canRestore = (item: FineJobReviewItem) => item.status === "dismissed" && (
  item.resolution_note.startsWith("用户归档")
  || item.resolution_note.startsWith("用户关联已有聊天会话后归档")
);

const linkChatBatch = async () => {
  try {
    const result = await workflowStore.linkChatBatch();
    const completed = result.confirmed ? `；确认已执行 ${result.confirmed} 项` : "";
    ElMessage.success(`已关联 ${result.matched} 项并归档 ${result.archived} 项${completed}；未匹配 ${result.unmatched} 项`);
  } catch {
    ElMessage.error(workflowStore.error ?? "关联聊天信息失败");
  }
};

const openChat = (sessionId: string) => router.push({
  name: "fine-job-chat",
  query: { session_id: sessionId }
});

const runBatch = async (operation: "approve" | "reject" | "archive") => {
  if (!selectedRows.value.length) return;
  const labels = { approve: "批准并创建任务", reject: "拒绝", archive: "归档" };
  const hasRejected = selectedRows.value.some((item) => item.status === "rejected");
  try {
    await ElMessageBox.confirm(
      `确认对选中的 ${selectedRows.value.length} 个岗位执行“${labels[operation]}”？`,
      "批量操作确认",
      { type: operation === "approve" ? "warning" : "info" }
    );
    const result = await workflowStore.batch(
      selectedRows.value.map((item) => item.id),
      operation,
      operation === "approve" && hasRejected
    );
    await executorStore.load();
    result.failed
      ? ElMessage.warning(`完成 ${result.succeeded} 条，失败 ${result.failed} 条`)
      : ElMessage.success(`已完成 ${result.succeeded} 条`);
  } catch (value) {
    if (value !== "cancel" && value !== "close") {
      ElMessage.error(workflowStore.error ?? "批量操作失败");
    }
  }
};

const openInDedicatedBrowser = async (item: FineJobReviewItem) => {
  navigationErrors.value[item.id] = "";
  try {
    await executorStore.openJob(item.id, "review");
    ElMessage.success("已在 FineJob 专用浏览器打开岗位");
  } catch {
    const message = executorStore.error ?? "打开岗位页面失败";
    navigationErrors.value[item.id] = message;
    ElMessage.error(message);
  }
};

const returnToReview = async (item: FineJobReviewItem) => {
  if (!item.action_id) return;
  try {
    await executorStore.returnToReview(item.action_id);
    await workflowStore.load(workflowStore.selectedStatus);
    ElMessage.success("已取消未发送动作并退回待确认");
  } catch {
    ElMessage.error(executorStore.error ?? "退回待确认失败");
  }
};

const canReturnToReview = (item: FineJobReviewItem) => Boolean(
  item.action_id && !["running", "succeeded"].includes(item.execution_state ?? "")
);

const handleSelectionChange = (rows: ReviewQueueRow[]) => {
  const greetings: FineJobReviewItem[] = [];
  rows.forEach((row) => {
    if (row.kind === "greeting") greetings.push(row.reviewItem);
  });
  selectedRows.value = greetings;
};

const showDetail = (item: FineJobReviewItem) => {
  detailItem.value = item;
  detailDrawerOpen.value = true;
};

const openHistoryDetail = (item: FineJobReviewItem) => router.push({
  name: "fine-job-capture-history",
  query: { history_id: item.job_id }
});

const openChatJobDetail = (task: FineJobChatReviewTask) => {
  if (!task.job_id) {
    ElMessage.warning("当前代聊任务没有关联岗位详情");
    return;
  }
  router.push({ name: "fine-job-capture-history", query: { history_id: task.job_id } });
};

const deleteReviewItem = async (item: FineJobReviewItem) => {
  try {
    await workflowStore.deleteItem(item);
    ElMessage.success("已删除待确认记录");
  } catch {
    ElMessage.error(workflowStore.error ?? "删除失败");
  }
};

const linkChatReviewTask = async (task: FineJobChatReviewTask) => {
  try {
    const context = await api.linkFineJobChatReviewTask(task.id, task.source);
    if (context.status === "resume_already_sent") {
      await loadStatus("pending");
      ElMessage.success("检测到该会话已发送简历，已取消重复任务");
      return context;
    }
    if (context.status === "new_message") {
      ElMessage.warning(`有新消息：${context.latest_message || "暂无消息内容"}`);
      return context;
    }
    ElMessage.success(task.source === "chat_reply" ? "当前没有新消息" : "当前会话尚未发送简历");
    return context;
  } catch (errorValue) {
    ElMessage.error(errorValue instanceof Error ? errorValue.message : "关联聊天信息失败");
    return null;
  }
};

const hasOtherSendingMessage = async (task: FineJobChatReviewTask) => {
  const detail = await api.getFineJobChatSession(task.session_id);
  const hasPendingReview = detail.reply_tasks.some((item) => (
    item.id !== task.id && item.status === "awaiting_review"
  ));
  const hasQueuedAction = detail.send_actions.some((action) => (
    action.operation_kind === "text" && ["queued", "leased", "dispatching"].includes(action.status)
  ));
  return hasPendingReview || hasQueuedAction;
};

const copyChatMessage = async (message: string) => {
  try {
    await navigator.clipboard.writeText(message);
    ElMessage.success("本轮信息已复制");
  } catch {
    ElMessage.error("复制信息失败");
  }
};

const linkAllChatInformation = async () => {
  try {
    const greetingResult = await workflowStore.linkChatBatch();
    let newMessageCount = 0;
    let resumeCancelledCount = 0;
    let upToDateCount = 0;
    for (const task of workflowStore.chatReviewTasks) {
      const context = await api.linkFineJobChatReviewTask(task.id, task.source);
      if (context.status === "new_message") newMessageCount += 1;
      else if (context.status === "resume_already_sent") resumeCancelledCount += 1;
      else upToDateCount += 1;
    }
    await loadStatus("pending");
    const greetingSummary = greetingResult.matched
      ? `岗位聊天已关联 ${greetingResult.matched} 项`
      : "没有匹配到岗位聊天";
    ElMessage.success(
      `${greetingSummary}；代聊有新消息 ${newMessageCount} 条，已取消重复简历 ${resumeCancelledCount} 条，当前无更新 ${upToDateCount} 条`
    );
  } catch (errorValue) {
    ElMessage.error(errorValue instanceof Error ? errorValue.message : "关联聊天信息失败");
  }
};

const chooseChatDraftResolution = async (task: FineJobChatReviewTask) => {
  if (task.source !== "chat_reply") return undefined;
  const detail = await api.getFineJobChatSession(task.session_id);
  const existingDraftText = (detail.draft?.final_text || detail.draft?.draft_text || "").trim();
  if (!existingDraftText) return undefined;
  try {
    await ElMessageBox.confirm(
      h("div", { class: "draft-conflict-dialog" }, [
        h("p", "当前会话草稿已有内容。请选择如何处理本轮待确认消息："),
        h("p", { class: "draft-conflict-dialog__label" }, "本轮信息"),
        h("pre", { class: "draft-conflict-dialog__message" }, task.task_detail),
        h(ElButton, { link: true, type: "primary", onClick: () => void copyChatMessage(task.task_detail) }, () => "复制信息")
      ]),
      "取消待确认任务",
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

const approveChatReviewTask = async (task: FineJobChatReviewTask) => {
  const context = await linkChatReviewTask(task);
  if (!context || context.cancelled) return;
  if (context.has_new_message) {
    ElMessage.warning("当前会话有新消息，请进入聊天信息页查看后重新生成回复");
    return;
  }
  try {
    if (task.source === "chat_reply" && await hasOtherSendingMessage(task)) {
      await ElMessageBox.confirm(
        "当前有一条消息处于待确认或发送中，仍要发送本条消息吗？",
        "确认发送",
        { type: "warning", confirmButtonText: "继续发送", cancelButtonText: "暂不发送" }
      );
    }
    if (task.source === "chat_reply") {
      await api.confirmFineJobChatReply(task.id, {
        final_text: task.task_detail,
        based_on_message_id: task.based_on_message_id ?? "",
        based_on_session_version: task.based_on_session_version ?? 0
      });
    } else {
      await api.confirmFineJobChatResumeAction(task.id);
    }
    await loadStatus("pending");
    await executorStore.load();
    ElMessage.success("任务已进入执行队列");
  } catch (errorValue) {
    if (errorValue === "cancel" || errorValue === "close") return;
    ElMessage.error(errorValue instanceof Error ? errorValue.message : "确认任务失败");
  }
};

const rejectChatReviewTask = async (task: FineJobChatReviewTask) => {
  try {
    let draftResolution: "overwrite" | "discard" | undefined;
    if (task.source === "chat_reply") {
      const selectedResolution = await chooseChatDraftResolution(task);
      if (selectedResolution === null) return;
      draftResolution = selectedResolution;
      await api.cancelFineJobChatReply(task.id, draftResolution);
    }
    else await api.cancelFineJobChatResumeAction(task.id);
    await loadStatus("pending");
    ElMessage.success(
      task.source === "chat_reply"
        ? draftResolution === "discard" ? "已取消待确认任务，已丢弃本轮信息" : "已取消待确认任务，消息已恢复草稿"
        : "已取消待确认任务"
    );
  } catch (errorValue) {
    ElMessage.error(errorValue instanceof Error ? errorValue.message : "取消任务失败");
  }
};

const decisionLabel = (decision: FineJobReviewItem["ai_decision"]) =>
  ({ recommend: "建议投递", review: "待判断", reject: "不建议" })[decision];
const decisionType = (decision: FineJobReviewItem["ai_decision"]) =>
  decision === "recommend" ? "success" : decision === "reject" ? "danger" : "warning";
const executionLabel = (item: FineJobReviewItem) => {
  if (!item.action_id) return item.status === "approved" ? "已批准" : "未入队";
  return ({
    queued: "待处理", running: "执行中", succeeded: "已完成",
    cancelled: "已取消", blocked: "已阻断", failed: "执行失败", unknown: "结果未知"
  } as Record<string, string>)[item.execution_state ?? ""] ?? item.execution_state ?? "未知";
};
const chatExecutionLabel = (task: FineJobChatReviewTask) => {
  if (workflowStore.selectedStatus !== "executed") return "待确认";
  return ({ accepted: "已发送", failed: "发送失败", unknown: "结果未知" } as Record<string, string>)[task.execution_state ?? ""]
    ?? "已执行";
};
const evaluationReasons = (evaluation: FineJobReviewItem["evaluation"]) =>
  Array.isArray(evaluation?.reasons) ? evaluation.reasons : [];
const evaluationStrengths = (evaluation: FineJobReviewItem["evaluation"]) =>
  Array.isArray(evaluation?.strengths) ? evaluation.strengths : [];
const evaluationGaps = (evaluation: FineJobReviewItem["evaluation"]) =>
  Array.isArray(evaluation?.gaps) ? evaluation.gaps : [];
const evaluationRisks = (evaluation: FineJobReviewItem["evaluation"]) =>
  Array.isArray(evaluation?.risks) ? evaluation.risks : [];
const evaluationHardRequirements = (evaluation: FineJobReviewItem["evaluation"]) =>
  Array.isArray(evaluation?.hard_requirements) ? evaluation.hard_requirements : [];
const evaluationSummary = (evaluation: FineJobReviewItem["evaluation"]) =>
  (typeof evaluation?.summary === "string" && evaluation.summary) || evaluationReasons(evaluation).join("；") || "-";
const confidencePercent = (evaluation: FineJobReviewItem["evaluation"]) => {
  const confidence = Number(evaluation?.confidence);
  return Number.isFinite(confidence) ? Math.round(confidence * 100) : 0;
};
const gapSummary = (item: FineJobReviewItem) =>
  evaluationGaps(item.evaluation).map((gap) => gap.item).join("；");

const createPairingCode = async () => {
  try {
    await executorStore.createPairingCode();
  } catch {
    ElMessage.error(executorStore.error ?? "生成配对码失败");
  }
};

onMounted(() => {
  void loadStatus("pending");
  void executorStore.load();
  executorPollTimer = window.setInterval(() => void executorStore.load().catch(() => undefined), 5000);
});
onBeforeUnmount(() => {
  if (executorPollTimer !== null) window.clearInterval(executorPollTimer);
});
</script>

<template>
  <section class="page-stack fine-job-page">
    <div class="page-heading">
      <div>
        <p class="panel-eyebrow">Review Queue</p>
        <h1>待确认</h1>
        <p class="secondary-text">{{ pageDescription }}</p>
      </div>
      <el-button :loading="workflowStore.loading" @click="loadStatus(workflowStore.selectedStatus)">刷新</el-button>
    </div>

    <el-alert v-if="workflowStore.error" type="error" title="待确认操作失败" :description="workflowStore.error" show-icon />

    <section class="page-panel executor-summary">
      <div>
        <span class="secondary-text">BOSS 执行器</span>
        <div class="executor-summary__main">
          <el-tag :type="executorType">{{ executorLabel }}</el-tag>
          <strong>队列 {{ executorStore.dashboard?.queue.total ?? 0 }}</strong>
          <span v-if="executorStore.dashboard?.executor?.last_heartbeat_at" class="secondary-text">
            最近心跳 {{ formatDateTime(executorStore.dashboard.executor.last_heartbeat_at) }}
          </span>
        </div>
      </div>
      <div class="card-actions">
        <el-button v-if="!executorStore.dashboard?.executor || !executorStore.dashboard.executor.browser_connected" @click="createPairingCode">生成配对码</el-button>
        <el-button type="primary" plain @click="router.push({ name: 'fine-job-runs' })">查看运行状态</el-button>
      </div>
    </section>

    <el-alert
      v-if="executorStore.pairingCode"
      type="warning"
      :closable="false"
      :title="`配对码：${executorStore.pairingCode}`"
      :description="`有效期至 ${formatDateTime(executorStore.pairingExpiresAt || '')}`"
      show-icon
    />

    <section class="page-panel review-filters">
      <el-form label-position="top">
        <div class="review-filter-grid">
          <el-form-item label="岗位 / 公司">
            <el-input v-model="workflowStore.query" clearable placeholder="输入关键词" @keyup.enter="search" />
          </el-form-item>
          <el-form-item label="AI 结论">
            <el-select v-model="workflowStore.decision" clearable placeholder="全部结论">
              <el-option label="建议投递" value="recommend" />
              <el-option label="待判断" value="review" />
              <el-option label="不建议" value="reject" />
            </el-select>
          </el-form-item>
          <el-form-item label="执行状态">
            <el-select v-model="workflowStore.executionState" clearable placeholder="全部状态">
              <el-option label="待处理" value="queued" />
              <el-option label="执行中" value="running" />
              <el-option label="执行成功" value="succeeded" />
              <el-option label="执行失败" value="failed" />
              <el-option label="结果未知" value="unknown" />
              <el-option label="已阻断" value="blocked" />
            </el-select>
          </el-form-item>
          <el-form-item label="创建时间">
            <el-date-picker
              v-model="workflowStore.createdRange"
              type="datetimerange"
              value-format="YYYY-MM-DDTHH:mm:ss[Z]"
              start-placeholder="开始时间"
              end-placeholder="结束时间"
            />
          </el-form-item>
        </div>
        <div class="filter-actions">
          <el-button type="primary" @click="search">查询</el-button>
          <el-button @click="resetFilters">重置</el-button>
        </div>
      </el-form>
    </section>

    <section class="page-panel">
      <el-radio-group
        v-model="workflowStore.selectedStatus"
        class="review-status-tabs"
        data-testid="review-status-filter"
        @change="handleTabChange"
      >
        <el-radio-button
          v-for="tab in reviewTabs"
          :key="tab.name"
          :value="tab.name"
        >
          {{ tab.label }}
        </el-radio-button>
      </el-radio-group>

      <div v-if="selectedRows.length" class="batch-toolbar">
        <strong>已选择 {{ selectedRows.length }} 项</strong>
        <el-button type="primary" @click="runBatch('approve')">批量批准</el-button>
        <el-button v-if="workflowStore.selectedStatus === 'pending'" type="danger" plain @click="runBatch('reject')">批量拒绝</el-button>
        <el-button @click="runBatch('archive')">批量归档</el-button>
      </div>
      <div v-if="['pending', 'rejected'].includes(workflowStore.selectedStatus)" class="batch-toolbar">
        <el-button
          type="success"
          :loading="workflowStore.loading"
          @click="workflowStore.selectedStatus === 'pending' ? linkAllChatInformation() : linkChatBatch()"
        >关联聊天信息</el-button>
        <span class="secondary-text">
          {{ workflowStore.selectedStatus === 'pending'
            ? '统一检查岗位关联、代聊是否有新消息，并自动取消已发送的重复简历任务。'
            : '按当前筛选条件关联全部分页记录，匹配到同岗位聊天会话后归档。' }}
        </span>
      </div>

      <el-table
        v-loading="workflowStore.loading"
        :data="reviewRows"
        row-key="id"
        empty-text="当前筛选条件下暂无事项"
        @selection-change="handleSelectionChange"
      >
        <el-table-column v-if="['pending', 'rejected'].includes(workflowStore.selectedStatus)" type="selection" width="46" />
        <el-table-column label="任务类型" width="125">
          <template #default="{ row }">
            <span v-if="row.kind === 'greeting'">打招呼</span>
            <el-button v-else link type="primary" @click="openChat(row.chatTask.session_id)">{{ row.chatTask.task_type }}</el-button>
          </template>
        </el-table-column>
        <el-table-column label="岗位" min-width="210">
          <template #default="{ row }">
            <el-button v-if="row.kind === 'greeting'" link type="primary" @click="showDetail(row.reviewItem)">
              {{ row.reviewItem.job_title }}
            </el-button>
            <el-button v-else link type="primary" @click="openChatJobDetail(row.chatTask)">
              {{ row.chatTask.job_title || "-" }}
            </el-button>
          </template>
        </el-table-column>
        <el-table-column label="公司" min-width="220">
          <template #default="{ row }">
            <div v-if="row.kind === 'greeting'" class="company-cell">
              <span>{{ row.reviewItem.company_name }}</span>
              <el-tag v-if="row.reviewItem.company_type === 'outsourcing'" type="warning" size="small">外包公司</el-tag>
              <el-tag
                v-if="row.reviewItem.company_chat_session_id"
                class="company-chat-tag"
                type="success"
                size="small"
                @click="openChat(row.reviewItem.company_chat_session_id)"
              >有过沟通</el-tag>
            </div>
            <span v-else>{{ row.chatTask.company_name || row.chatTask.peer_name || "-" }}</span>
          </template>
        </el-table-column>

        <el-table-column label="任务详情" min-width="260" show-overflow-tooltip>
          <template #default="{ row }">
            {{ row.kind === 'greeting'
              ? row.reviewItem.final_message || row.reviewItem.draft_message || "待生成招呼语"
              : row.chatTask.task_detail }}
          </template>
        </el-table-column>
        <el-table-column label="AI 结论" width="115">
          <template #default="{ row }">
            <el-tag v-if="row.kind === 'greeting'" :type="decisionType(row.reviewItem.ai_decision)">{{ decisionLabel(row.reviewItem.ai_decision) }}</el-tag>
            <span v-else>-</span>
          </template>
        </el-table-column>
        <el-table-column label="置信度" width="90">
          <template #default="{ row }">{{ row.kind === 'greeting' ? `${confidencePercent(row.reviewItem.evaluation)}%` : "-" }}</template>
        </el-table-column>
        <el-table-column label="关键判断" min-width="240" show-overflow-tooltip>
          <template #default="{ row }">{{ row.kind === 'greeting' ? evaluationSummary(row.reviewItem.evaluation) : row.chatTask.peer_name || "待确认发送" }}</template>
        </el-table-column>
        <el-table-column label="执行状态" width="140">
          <template #default="{ row }">
            <el-tag v-if="row.kind === 'chat' && row.chatTask.has_new_message" type="warning">有新消息</el-tag>
            <el-tag v-else-if="row.kind === 'chat' && row.chatTask.resume_already_sent" type="success">已发送简历</el-tag>
            <span v-else>{{ row.kind === 'greeting' ? executionLabel(row.reviewItem) : chatExecutionLabel(row.chatTask) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="创建时间" width="175">
          <template #default="{ row }">{{ formatDateTime(row.kind === 'greeting' ? row.reviewItem.created_at : row.chatTask.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="180" fixed="right">
          <template #default="{ row }">
            <template v-if="row.kind === 'chat'">
              <template v-if="workflowStore.selectedStatus === 'pending'">
                <el-button link type="primary" @click="approveChatReviewTask(row.chatTask)">批准</el-button>
                <el-button link type="danger" @click="rejectChatReviewTask(row.chatTask)">取消</el-button>
              </template>
            </template>
            <template v-else-if="row.reviewItem.status === 'pending'">
              <el-button link type="primary" @click="approve(row.reviewItem)">批准</el-button>
              <el-button link type="danger" @click="reject(row.reviewItem)">拒绝</el-button>
              <el-button link type="danger" @click="deleteGreeting(row.reviewItem)">删除</el-button>
            </template>
            <template v-else-if="row.reviewItem.status === 'rejected'">
              <el-button link type="warning" @click="approve(row.reviewItem)">仍要沟通</el-button>
              <el-button link @click="archive(row.reviewItem)">归档</el-button>
              <el-button link type="danger" @click="deleteReviewItem(row.reviewItem)">删除</el-button>
            </template>
            <template v-else-if="row.reviewItem.status === 'dismissed'">
              <el-button v-if="canRestore(row.reviewItem)" link type="primary" @click="restore(row.reviewItem)">恢复</el-button>
              <el-button link type="danger" @click="deleteReviewItem(row.reviewItem)">删除</el-button>
            </template>
            <el-button v-else-if="canReturnToReview(row.reviewItem)" link type="danger" @click="returnToReview(row.reviewItem)">退回</el-button>
          </template>
        </el-table-column>
      </el-table>

      <div class="review-pagination">
        <el-pagination
          v-model:current-page="workflowStore.page"
          v-model:page-size="workflowStore.pageSize"
          :total="workflowStore.total"
          :page-sizes="[20, 50, 100]"
          layout="total, sizes, prev, pager, next"
          @change="loadStatus(workflowStore.selectedStatus)"
        />
      </div>
    </section>

    <el-drawer v-model="detailDrawerOpen" size="52%" :title="detailItem?.job_title || '岗位评估详情'">
      <template v-if="detailItem">
        <div class="detail-heading">
          <div><h2>{{ detailItem.job_title }}</h2><p class="secondary-text">{{ detailItem.company_name }}</p></div>
          <el-button type="primary" plain @click="openInDedicatedBrowser(detailItem)">专用浏览器打开</el-button>
        </div>
        <el-descriptions :column="2" border>
          <el-descriptions-item label="AI 结论">{{ decisionLabel(detailItem.ai_decision) }}</el-descriptions-item>
          <el-descriptions-item label="执行状态">{{ executionLabel(detailItem) }}</el-descriptions-item>
          <el-descriptions-item label="置信度">{{ confidencePercent(detailItem.evaluation) }}%</el-descriptions-item>
          <el-descriptions-item label="创建时间">{{ formatDateTime(detailItem.created_at) }}</el-descriptions-item>
        </el-descriptions>
        <div class="review-detail">
          <h3>评估摘要</h3>
          <p>{{ evaluationSummary(detailItem.evaluation) }}</p>
          <h3 v-if="evaluationStrengths(detailItem.evaluation).length">优势</h3>
          <p v-if="evaluationStrengths(detailItem.evaluation).length">{{ evaluationStrengths(detailItem.evaluation).join("；") }}</p>
          <h3 v-if="evaluationGaps(detailItem.evaluation).length">差距</h3>
          <p v-if="evaluationGaps(detailItem.evaluation).length">{{ gapSummary(detailItem) }}</p>
          <h3 v-if="evaluationRisks(detailItem.evaluation).length">风险</h3>
          <p v-if="evaluationRisks(detailItem.evaluation).length" class="evaluation-warning">{{ evaluationRisks(detailItem.evaluation).join("；") }}</p>
          <h3 v-if="evaluationHardRequirements(detailItem.evaluation).length">硬性条件</h3>
          <div v-if="evaluationHardRequirements(detailItem.evaluation).length" class="tag-list">
            <el-tag v-for="item in evaluationHardRequirements(detailItem.evaluation)" :key="item.name">{{ item.name }} · {{ item.status }}</el-tag>
          </div>
          <h3>招呼语草稿</h3>
          <p>{{ detailItem.draft_message || "暂无招呼语草稿" }}</p>
          <el-button type="primary" plain @click="openHistoryDetail(detailItem)">查看更多详细信息</el-button>
          <el-link v-if="detailItem.job_link" :href="detailItem.job_link" target="_blank" type="info">打开 BOSS 原始链接</el-link>
        </div>
      </template>
    </el-drawer>
  </section>
</template>

<style scoped>
.executor-summary, .executor-summary__main, .batch-toolbar, .detail-heading, .filter-actions, .tag-list {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.executor-summary, .detail-heading { justify-content: space-between; }
.executor-summary__main { margin-top: 8px; }
.review-status-tabs { margin-bottom: 14px; }
.review-filter-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 4px 16px; }
.batch-toolbar { padding: 10px 0 14px; }
.review-pagination { display: flex; justify-content: flex-end; padding-top: 18px; }
.row-error { margin: 2px 0 0; color: var(--el-color-danger); font-size: 12px; }
.company-cell { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; }
.company-chat-tag { cursor: pointer; }
.review-detail { padding-top: 18px; line-height: 1.75; }
.review-detail h3 { margin: 18px 0 6px; }
.evaluation-warning { color: var(--el-color-warning); }
</style>
