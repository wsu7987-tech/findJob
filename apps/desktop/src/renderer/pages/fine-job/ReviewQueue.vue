<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";

import { formatDateTime } from "@/services/format";
import { useFineJobWorkflowStore } from "@/stores/fineJobWorkflow";
import { useFineJobBossExecutorStore } from "@/stores/fineJobBossExecutor";
import type {
  FineJobBossExecutorQueueAction,
  FineJobReviewItem,
  FineJobReviewStatus
} from "@/types";

const workflowStore = useFineJobWorkflowStore();
const executorStore = useFineJobBossExecutorStore();
let executorPollTimer: number | null = null;
let countdownTimer: number | null = null;
const nowMs = ref(Date.now());

const pageDescription = computed(() => {
  if (workflowStore.selectedStatus === "rejected") {
    return "AI 不建议或用户拒绝的岗位保留在这里；你仍可明确覆盖结论并加入动作队列。";
  }
  if (workflowStore.selectedStatus === "approved") {
    return "已经由用户或已确认自动化策略批准的动作。";
  }
  return "推荐但未授权自动化、以及 AI 判断为待确认的岗位会进入这里。";
});

const loadStatus = async (status: FineJobReviewStatus) => {
  try {
    await workflowStore.load(status);
  } catch {
    ElMessage.error(workflowStore.error ?? "待确认事项加载失败");
  }
};

const approve = async (item: FineJobReviewItem) => {
  if (item.status === "rejected") {
    try {
      await ElMessageBox.confirm(
        "该岗位原结论为不建议。确认后会加入BOSS默认招呼队列；只有插件另行开启自动打招呼权限才会执行。",
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
    ElMessage.success("已加入BOSS默认招呼队列");
  } catch {
    ElMessage.error(workflowStore.error ?? "批准失败");
  }
};

const openInDedicatedBrowser = async (item: FineJobReviewItem) => {
  try {
    await executorStore.openJob(item.id, "review");
    ElMessage.success("已打开对应岗位详情页；该操作不会打招呼");
  } catch {
    ElMessage.error(executorStore.error ?? "打开岗位页面失败");
  }
};

const actionFor = (item: FineJobReviewItem) =>
  executorStore.dashboard?.queue.actions.find((action) => action.job_id === item.job_id) ?? null;
const canReturnToReview = (item: FineJobReviewItem) => {
  const action = actionFor(item);
  return Boolean(
    action && ![
      "dispatch_started", "request_accepted", "succeeded", "failed_after_dispatch", "unknown_after_dispatch"
    ].includes(action.execution_state)
  );
};

const returnToReview = async (item: FineJobReviewItem) => {
  const action = actionFor(item);
  if (!action) return;
  try {
    await executorStore.returnToReview(action.id);
    await workflowStore.load("approved");
    ElMessage.success("已取消未发送动作并返回待确认列表");
  } catch {
    ElMessage.error(executorStore.error ?? "退回待确认失败");
  }
};

const manualVerifyUnknown = async (action: FineJobBossExecutorQueueAction) => {
  try {
    await executorStore.openJob(action.job_id, "review");
  } catch {
    ElMessage.error(executorStore.error ?? "无法打开未知错误岗位");
    return;
  }

  let contacted: boolean;
  try {
    await ElMessageBox.confirm(
      "对应岗位详情页已经打开。请等待页面加载并核对按钮：显示“继续沟通”请选择“确认已沟通”；显示“立即沟通”请选择“确认未沟通”。关闭窗口不会修改状态。",
      "人工核验未知错误",
      {
        type: "warning",
        confirmButtonText: "确认已沟通",
        cancelButtonText: "确认未沟通",
        distinguishCancelAndClose: true,
        closeOnClickModal: false
      }
    );
    contacted = true;
  } catch (value) {
    if (value !== "cancel") return;
    contacted = false;
  }

  try {
    await executorStore.manualVerifyUnknown(action.id, contacted);
    await workflowStore.load(workflowStore.selectedStatus);
    ElMessage.success(contacted ? "已记录人工确认：岗位已沟通" : "已返回待确认列表，重新批准前不会发送");
  } catch {
    ElMessage.error(executorStore.error ?? "人工核验结果保存失败");
  }
};

const createPairingCode = async () => {
  try {
    await executorStore.createPairingCode();
    ElMessage.success("配对码已生成，请在5分钟内输入插件面板");
  } catch {
    ElMessage.error(executorStore.error ?? "生成配对码失败");
  }
};

const reject = async (item: FineJobReviewItem) => {
  try {
    await workflowStore.reject(item);
    ElMessage.success("已拒绝该打招呼动作");
  } catch {
    ElMessage.error(workflowStore.error ?? "拒绝失败");
  }
};

const decisionLabel = (decision: FineJobReviewItem["ai_decision"]) =>
  ({ recommend: "建议投递", review: "待确认", reject: "不建议" })[decision];
const decisionType = (decision: FineJobReviewItem["ai_decision"]) =>
  decision === "recommend" ? "success" : decision === "reject" ? "danger" : "warning";
const statusLabel = (status: FineJobReviewStatus) =>
  ({ pending: "待确认", approved: "已批准", rejected: "不建议/已拒绝", dismissed: "已忽略" })[status];
const handleTabChange = (name: string | number) =>
  loadStatus(String(name) as FineJobReviewStatus);
const gapSummary = (item: FineJobReviewItem) =>
  item.evaluation.gaps.map((gap) => gap.item).join("；");
const dimensionEntries = (item: FineJobReviewItem) =>
  Object.entries(item.evaluation.match_dimensions);
const dimensionLabels: Record<string, string> = {
  job_direction: "岗位方向",
  core_skills: "核心技能",
  experience: "工作经验",
  project_relevance: "项目相关",
  industry_relevance: "行业相关",
  salary_location: "薪资地点",
  job_relevance: "岗位相关"
};
const dimensionLabel = (name: string) => dimensionLabels[name] ?? name;
const requirementType = (status: "pass" | "fail" | "unknown") =>
  status === "pass" ? "success" : status === "fail" ? "danger" : "warning";

const verificationRemaining = (action: FineJobBossExecutorQueueAction) => {
  if (!action.verification_due_at) return null;
  return Math.max(0, Math.ceil((new Date(action.verification_due_at).getTime() - nowMs.value) / 1000));
};

const executorActionLabel = (action: FineJobBossExecutorQueueAction) => {
  if (action.execution_state === "request_accepted") {
    if (action.verification_state === "waiting_refresh") {
      return `平台已受理，${verificationRemaining(action) ?? 0}秒后刷新验证`;
    }
    if (action.verification_state === "refreshing") return "正在刷新当前岗位页面";
    if (action.verification_state === "waiting_snapshot") return "正在确认是否已建立沟通";
    if (action.verification_state === "pending") return "已提交，页面暂未确认";
    if (action.verification_state === "not_required") return "平台已受理，待后续验证";
  }
  if (action.verification_state === "page_confirmed") return "页面验证成功";
  if (action.verification_state === "manual_confirmed") return "人工核验完成";
  if (action.execution_state === "unknown_after_dispatch") return "未知错误";
  return action.execution_state;
};

onMounted(() => {
  void loadStatus("pending");
  void executorStore.load();
  executorPollTimer = window.setInterval(() => void executorStore.load().catch(() => undefined), 3000);
  countdownTimer = window.setInterval(() => { nowMs.value = Date.now(); }, 1000);
});
onBeforeUnmount(() => {
  if (executorPollTimer !== null) window.clearInterval(executorPollTimer);
  if (countdownTimer !== null) window.clearInterval(countdownTimer);
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
      <el-button :loading="workflowStore.loading" @click="loadStatus(workflowStore.selectedStatus)">
        刷新
      </el-button>
    </div>

    <el-alert
      type="info"
      :closable="false"
      show-icon
      title="打开岗位页面与打招呼是两个独立动作"
      description="批准只会加入默认招呼队列；插件必须已配对且用户主动允许自动打招呼，才会串行执行。"
    />

    <el-alert
      v-if="workflowStore.error"
      type="error"
      title="待确认操作失败"
      :description="workflowStore.error"
      show-icon
    />

    <div class="metric-grid">
      <article class="metric-card">
        <span>当前列表</span>
        <strong>{{ workflowStore.total }}</strong>
        <p>{{ statusLabel(workflowStore.selectedStatus) }}</p>
      </article>
      <article class="metric-card">
        <span>待执行动作</span>
        <strong>{{ workflowStore.queuedActions.length }}</strong>
        <p>SQLite 持久化队列</p>
      </article>
    </div>

    <section class="page-panel executor-panel">
      <div class="executor-heading">
        <div>
          <h2>BOSS执行器</h2>
          <p class="secondary-text">
            {{ executorStore.dashboard?.executor
              ? `插件 ${executorStore.dashboard.executor.plugin_version} · ${executorStore.dashboard.executor.queue_state}`
              : "尚未配对插件" }}
          </p>
        </div>
        <el-button :loading="executorStore.loading" @click="createPairingCode">生成插件配对码</el-button>
      </div>
      <el-alert
        v-if="executorStore.pairingCode"
        type="warning"
        :closable="false"
        :title="`配对码：${executorStore.pairingCode}`"
        :description="`有效期至 ${formatDateTime(executorStore.pairingExpiresAt || '')}`"
        show-icon
      />
      <div v-if="executorStore.dashboard?.executor" class="executor-status-grid">
        <span>自动权限：{{ executorStore.dashboard.executor.permission_state }}</span>
        <span>队列：{{ executorStore.dashboard.executor.queue_state }}</span>
        <span>风险：{{ executorStore.dashboard.executor.risk_state }}</span>
        <span>插件浏览器：{{ executorStore.dashboard.executor.browser_connected ? "正常" : "未连接" }}</span>
      </div>
      <el-table
        v-if="executorStore.dashboard?.queue.actions.length"
        :data="executorStore.dashboard.queue.actions"
        size="small"
        row-key="id"
      >
        <el-table-column prop="job_title" label="打招呼队列" min-width="180" />
        <el-table-column prop="company_name" label="公司" min-width="150" />
        <el-table-column label="执行状态" min-width="240">
          <template #default="{ row }">{{ executorActionLabel(row) }}</template>
        </el-table-column>
        <el-table-column prop="page_open_attempts" label="打开次数" width="90" />
        <el-table-column label="操作" width="120">
          <template #default="{ row }">
            <el-button
              v-if="row.execution_state === 'unknown_after_dispatch'"
              link
              type="warning"
              :loading="executorStore.openingJobId === row.job_id"
              @click="manualVerifyUnknown(row)"
            >人工核验</el-button>
          </template>
        </el-table-column>
      </el-table>
    </section>

    <section class="page-panel">
      <el-tabs
        v-model="workflowStore.selectedStatus"
        @tab-change="handleTabChange"
      >
        <el-tab-pane label="待确认" name="pending" />
        <el-tab-pane label="不建议/已拒绝" name="rejected" />
        <el-tab-pane label="已批准" name="approved" />
      </el-tabs>

      <el-table
        v-loading="workflowStore.loading"
        :data="workflowStore.items"
        row-key="id"
        empty-text="当前分类暂无事项"
      >
        <el-table-column type="expand">
          <template #default="{ row }">
            <div class="review-detail">
              <h3>评估摘要</h3>
              <p>{{ row.evaluation.summary || row.evaluation.reasons.join("；") }}</p>
              <p v-if="row.evaluation.strengths.length">
                <strong>优势：</strong>{{ row.evaluation.strengths.join("；") }}
              </p>
              <p v-if="row.evaluation.gaps.length">
                <strong>差距：</strong>{{ gapSummary(row) }}
              </p>
              <p v-if="row.evaluation.risks.length" class="evaluation-warning">
                <strong>风险：</strong>{{ row.evaluation.risks.join("；") }}
              </p>
              <template v-if="row.evaluation.hard_requirements.length">
                <h3>硬性条件</h3>
                <div class="requirement-list">
                  <el-tag
                    v-for="requirement in row.evaluation.hard_requirements"
                    :key="requirement.name"
                    :type="requirementType(requirement.status)"
                  >
                    {{ requirement.name }} · {{ requirement.status }}
                  </el-tag>
                </div>
              </template>
              <template v-if="dimensionEntries(row).length">
                <h3>匹配维度</h3>
                <div class="dimension-grid">
                  <div v-for="entry in dimensionEntries(row)" :key="entry[0]">
                    <span>{{ dimensionLabel(entry[0]) }}</span>
                    <el-progress :percentage="Math.round(entry[1] * 100)" :stroke-width="8" />
                  </div>
                </div>
              </template>
              <template v-if="row.evaluation.resume_suggestions.length">
                <h3>简历优化</h3>
                <ul>
                  <li v-for="item in row.evaluation.resume_suggestions" :key="`${item.section}-${item.suggestion}`">
                    {{ item.section }}：{{ item.suggestion }}<span v-if="item.basis">（{{ item.basis }}）</span>
                  </li>
                </ul>
              </template>
              <h3>招呼语草稿</h3>
              <p>{{ row.draft_message || "不建议岗位暂未生成招呼语" }}</p>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="岗位" min-width="190">
          <template #default="{ row }">
            <el-link v-if="row.job_link" :href="row.job_link" target="_blank" type="primary">
              {{ row.job_title }}
            </el-link>
            <span v-else>{{ row.job_title }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="company_name" label="公司" min-width="150" />
        <el-table-column label="AI 结论" width="120">
          <template #default="{ row }">
            <el-tag :type="decisionType(row.ai_decision)">{{ decisionLabel(row.ai_decision) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="置信度" width="100">
          <template #default="{ row }">{{ Math.round(row.evaluation.confidence * 100) }}%</template>
        </el-table-column>
        <el-table-column label="创建时间" width="190">
          <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="200" fixed="right">
          <template #default="{ row }">
            <template v-if="row.status === 'pending'">
              <el-button link type="primary" @click="approve(row)">批准并加入默认招呼队列</el-button>
              <el-button link type="danger" @click="reject(row)">拒绝</el-button>
            </template>
            <el-button v-else-if="row.status === 'rejected'" link type="warning" @click="approve(row)">
              仍要沟通
            </el-button>
            <template v-else>
              <el-tag type="success">{{ row.auto_approved ? "策略自动批准" : "用户批准" }}</el-tag>
              <el-button
                v-if="canReturnToReview(row)"
                link
                type="danger"
                @click="returnToReview(row)"
              >退回待确认</el-button>
            </template>
            <el-button
              link
              type="primary"
              :loading="executorStore.openingJobId === row.id"
              @click="openInDedicatedBrowser(row)"
            >打开岗位</el-button>
          </template>
        </el-table-column>
      </el-table>
    </section>
  </section>
</template>

<style scoped>
.review-detail {
  padding: 8px 32px 18px;
  line-height: 1.7;
}

.review-detail h3 {
  margin: 12px 0 4px;
}

.evaluation-warning {
  color: var(--el-color-warning);
}

.requirement-list {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.dimension-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 8px 18px;
}

.executor-panel,
.executor-heading,
.executor-status-grid {
  display: grid;
  gap: 12px;
}

.executor-heading {
  grid-template-columns: 1fr auto;
  align-items: center;
}

.executor-status-grid {
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
}
</style>
