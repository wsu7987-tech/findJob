<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";

import { formatDateTime } from "@/services/format";
import { useFineJobWorkflowStore } from "@/stores/fineJobWorkflow";
import type { FineJobReviewItem, FineJobReviewStatus } from "@/types";

const workflowStore = useFineJobWorkflowStore();
const dialogOpen = ref(false);
const activeItem = ref<FineJobReviewItem | null>(null);
const editedMessage = ref("");

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

const openApproval = (item: FineJobReviewItem) => {
  activeItem.value = item;
  editedMessage.value = item.draft_message || `您好，我对贵司的${item.job_title}很感兴趣，希望有机会进一步沟通，谢谢。`;
  dialogOpen.value = true;
};

const approve = async () => {
  const item = activeItem.value;
  if (!item) return;
  if (item.status === "rejected") {
    try {
      await ElMessageBox.confirm(
        "该岗位原结论为不建议。确认后只会加入持久化动作队列，当前版本不会真实发送。",
        "仍要沟通",
        { type: "warning", confirmButtonText: "确认加入队列" }
      );
    } catch {
      return;
    }
  }
  try {
    await workflowStore.approve(item, editedMessage.value, item.status === "rejected");
    dialogOpen.value = false;
    activeItem.value = null;
    ElMessage.success("已加入打招呼动作队列；当前不会真实发送");
  } catch {
    ElMessage.error(workflowStore.error ?? "批准失败");
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

onMounted(() => void loadStatus("pending"));
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
      title="动作队列尚未连接真实浏览器执行器"
      description="批准后只会生成可恢复、可审计的排队动作，不会点击 BOSS 页面或发送消息。"
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
              <el-button link type="primary" @click="openApproval(row)">编辑并批准</el-button>
              <el-button link type="danger" @click="reject(row)">拒绝</el-button>
            </template>
            <el-button v-else-if="row.status === 'rejected'" link type="warning" @click="openApproval(row)">
              仍要沟通
            </el-button>
            <el-tag v-else type="success">{{ row.auto_approved ? "策略自动批准" : "用户批准" }}</el-tag>
          </template>
        </el-table-column>
      </el-table>
    </section>

    <el-dialog v-model="dialogOpen" width="620px" title="确认打招呼内容">
      <template v-if="activeItem">
        <p>{{ activeItem.company_name }} · {{ activeItem.job_title }}</p>
        <el-input
          v-model="editedMessage"
          type="textarea"
          :rows="5"
          maxlength="300"
          show-word-limit
          placeholder="请输入最终打招呼内容"
        />
      </template>
      <template #footer>
        <el-button @click="dialogOpen = false">取消</el-button>
        <el-button
          type="primary"
          :loading="workflowStore.processingId === activeItem?.id"
          :disabled="!editedMessage.trim()"
          @click="approve"
        >
          加入动作队列
        </el-button>
      </template>
    </el-dialog>
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
</style>
