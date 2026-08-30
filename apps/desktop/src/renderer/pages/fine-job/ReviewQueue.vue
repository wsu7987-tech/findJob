<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { useRouter } from "vue-router";

import { formatDateTime } from "@/services/format";
import { useFineJobBossExecutorStore } from "@/stores/fineJobBossExecutor";
import { useFineJobWorkflowStore } from "@/stores/fineJobWorkflow";
import type { FineJobReviewItem, FineJobReviewStatus } from "@/types";

const router = useRouter();
const workflowStore = useFineJobWorkflowStore();
const executorStore = useFineJobBossExecutorStore();
const selectedRows = ref<FineJobReviewItem[]>([]);
const detailItem = ref<FineJobReviewItem | null>(null);
const detailDrawerOpen = ref(false);
const navigationErrors = ref<Record<string, string>>({});
let executorPollTimer: number | null = null;

const pageDescription = computed(() => ({
  pending: "集中审核推荐岗位和需要判断的岗位，批准后进入默认招呼队列。",
  rejected: "查看 AI 不建议或用户拒绝的岗位，可明确覆盖结论。",
  approved: "跟踪已经批准的岗位及其实际执行状态。",
  dismissed: "保存用户主动归档和被新评估替代的历史事项。"
})[workflowStore.selectedStatus]);

const executorLabel = computed(() => {
  const executor = executorStore.dashboard?.executor;
  if (!executor) return "未配对";
  if (!executor.browser_connected) return "浏览器未连接";
  if (executor.risk_state !== "none") return "风险暂停";
  if (executor.queue_state === "running") return "运行中";
  return "已暂停";
});

const executorType = computed(() => {
  if (executorLabel.value === "运行中") return "success";
  if (["未配对", "浏览器未连接"].includes(executorLabel.value)) return "info";
  return "warning";
});

const loadStatus = async (status: FineJobReviewStatus, resetPage = false) => {
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
  loadStatus(String(name) as FineJobReviewStatus, true);
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
        "该岗位原结论为不建议。确认后会加入 BOSS 默认招呼队列。",
        "仍要沟通",
        { type: "warning", confirmButtonText: "确认加入队列" }
      );
    } catch {
      return;
    }
  }
  try {
    await workflowStore.approve(item, "", item.status === "rejected");
    await executorStore.load();
    ElMessage.success("已加入 BOSS 默认招呼队列");
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

const restore = async (item: FineJobReviewItem) => {
  try {
    await workflowStore.restore(item);
    ElMessage.success("已恢复到待确认");
  } catch {
    ElMessage.error(workflowStore.error ?? "恢复失败");
  }
};

const runBatch = async (operation: "approve" | "reject" | "archive") => {
  if (!selectedRows.value.length) return;
  const labels = { approve: "批准并加入队列", reject: "拒绝", archive: "归档" };
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
    await workflowStore.load("approved");
    ElMessage.success("已取消未发送动作并退回待确认");
  } catch {
    ElMessage.error(executorStore.error ?? "退回待确认失败");
  }
};

const canReturnToReview = (item: FineJobReviewItem) => Boolean(
  item.action_id && ![
    "dispatch_started", "request_accepted", "succeeded", "failed_after_dispatch", "unknown_after_dispatch"
  ].includes(item.execution_state ?? "")
);

const showDetail = (item: FineJobReviewItem) => {
  detailItem.value = item;
  detailDrawerOpen.value = true;
};

const decisionLabel = (decision: FineJobReviewItem["ai_decision"]) =>
  ({ recommend: "建议投递", review: "待判断", reject: "不建议" })[decision];
const decisionType = (decision: FineJobReviewItem["ai_decision"]) =>
  decision === "recommend" ? "success" : decision === "reject" ? "danger" : "warning";
const executionLabel = (item: FineJobReviewItem) => {
  if (!item.action_id) return item.status === "approved" ? "已批准" : "未入队";
  return ({
    queued: "排队中", opening_page: "正在打开岗位", waiting_page_ready: "等待页面就绪",
    page_verified: "页面已核对", ready_to_dispatch: "等待发送", dispatch_started: "正在发送",
    request_accepted: "平台已受理", succeeded: "已确认沟通", cancellation_requested: "正在取消",
    cancelled: "已取消", blocked: "已阻断", failed_before_dispatch: "发送前失败",
    failed_after_dispatch: "发送后失败", unknown_after_dispatch: "结果未知"
  } as Record<string, string>)[item.execution_state ?? ""] ?? item.execution_state ?? "未知";
};
const gapSummary = (item: FineJobReviewItem) => item.evaluation.gaps.map((gap) => gap.item).join("；");

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
        <el-button v-if="!executorStore.dashboard?.executor" @click="createPairingCode">生成配对码</el-button>
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
              <el-option label="排队中" value="queued" />
              <el-option label="正在发送" value="dispatch_started" />
              <el-option label="平台已受理" value="request_accepted" />
              <el-option label="执行成功" value="succeeded" />
              <el-option label="结果未知" value="unknown_after_dispatch" />
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
      <el-tabs v-model="workflowStore.selectedStatus" @tab-change="handleTabChange">
        <el-tab-pane label="待确认" name="pending" />
        <el-tab-pane label="不建议/已拒绝" name="rejected" />
        <el-tab-pane label="已批准" name="approved" />
        <el-tab-pane label="已归档" name="dismissed" />
      </el-tabs>

      <div v-if="selectedRows.length" class="batch-toolbar">
        <strong>已选择 {{ selectedRows.length }} 项</strong>
        <el-button type="primary" @click="runBatch('approve')">批量批准</el-button>
        <el-button v-if="workflowStore.selectedStatus === 'pending'" type="danger" plain @click="runBatch('reject')">批量拒绝</el-button>
        <el-button @click="runBatch('archive')">批量归档</el-button>
      </div>

      <el-table
        v-loading="workflowStore.loading"
        :data="workflowStore.items"
        row-key="id"
        empty-text="当前筛选条件下暂无事项"
        @selection-change="(rows: FineJobReviewItem[]) => selectedRows = rows"
      >
        <el-table-column v-if="['pending', 'rejected'].includes(workflowStore.selectedStatus)" type="selection" width="46" />
        <el-table-column label="岗位" min-width="210">
          <template #default="{ row }">
            <el-button link type="primary" :loading="executorStore.openingJobId === row.id" @click="openInDedicatedBrowser(row)">
              {{ row.job_title }}
            </el-button>
            <p v-if="navigationErrors[row.id]" class="row-error">{{ navigationErrors[row.id] }}</p>
          </template>
        </el-table-column>
        <el-table-column prop="company_name" label="公司" min-width="150" />
        <el-table-column label="AI 结论" width="115">
          <template #default="{ row }"><el-tag :type="decisionType(row.ai_decision)">{{ decisionLabel(row.ai_decision) }}</el-tag></template>
        </el-table-column>
        <el-table-column label="置信度" width="90">
          <template #default="{ row }">{{ Math.round(row.evaluation.confidence * 100) }}%</template>
        </el-table-column>
        <el-table-column label="关键判断" min-width="240" show-overflow-tooltip>
          <template #default="{ row }">{{ row.evaluation.summary || row.evaluation.reasons.join("；") || "-" }}</template>
        </el-table-column>
        <el-table-column label="执行状态" width="140">
          <template #default="{ row }">{{ executionLabel(row) }}</template>
        </el-table-column>
        <el-table-column label="创建时间" width="175">
          <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="230" fixed="right">
          <template #default="{ row }">
            <el-button link @click="showDetail(row)">详情</el-button>
            <template v-if="row.status === 'pending'">
              <el-button link type="primary" @click="approve(row)">批准</el-button>
              <el-button link type="danger" @click="reject(row)">拒绝</el-button>
              <el-button link @click="archive(row)">归档</el-button>
            </template>
            <template v-else-if="row.status === 'rejected'">
              <el-button link type="warning" @click="approve(row)">仍要沟通</el-button>
              <el-button link @click="archive(row)">归档</el-button>
            </template>
            <el-button v-else-if="row.status === 'dismissed' && row.resolution_note.startsWith('用户归档')" link type="primary" @click="restore(row)">恢复</el-button>
            <el-button v-else-if="canReturnToReview(row)" link type="danger" @click="returnToReview(row)">退回</el-button>
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
          <el-descriptions-item label="置信度">{{ Math.round(detailItem.evaluation.confidence * 100) }}%</el-descriptions-item>
          <el-descriptions-item label="创建时间">{{ formatDateTime(detailItem.created_at) }}</el-descriptions-item>
        </el-descriptions>
        <div class="review-detail">
          <h3>评估摘要</h3>
          <p>{{ detailItem.evaluation.summary || detailItem.evaluation.reasons.join("；") }}</p>
          <h3 v-if="detailItem.evaluation.strengths.length">优势</h3>
          <p v-if="detailItem.evaluation.strengths.length">{{ detailItem.evaluation.strengths.join("；") }}</p>
          <h3 v-if="detailItem.evaluation.gaps.length">差距</h3>
          <p v-if="detailItem.evaluation.gaps.length">{{ gapSummary(detailItem) }}</p>
          <h3 v-if="detailItem.evaluation.risks.length">风险</h3>
          <p v-if="detailItem.evaluation.risks.length" class="evaluation-warning">{{ detailItem.evaluation.risks.join("；") }}</p>
          <h3 v-if="detailItem.evaluation.hard_requirements.length">硬性条件</h3>
          <div v-if="detailItem.evaluation.hard_requirements.length" class="tag-list">
            <el-tag v-for="item in detailItem.evaluation.hard_requirements" :key="item.name">{{ item.name }} · {{ item.status }}</el-tag>
          </div>
          <h3>招呼语草稿</h3>
          <p>{{ detailItem.draft_message || "暂无招呼语草稿" }}</p>
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
.review-filter-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 4px 16px; }
.batch-toolbar { padding: 10px 0 14px; }
.review-pagination { display: flex; justify-content: flex-end; padding-top: 18px; }
.row-error { margin: 2px 0 0; color: var(--el-color-danger); font-size: 12px; }
.review-detail { padding-top: 18px; line-height: 1.75; }
.review-detail h3 { margin: 18px 0 6px; }
.evaluation-warning { color: var(--el-color-warning); }
</style>
