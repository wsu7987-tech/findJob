<script setup lang="ts">
import { computed, onActivated, onMounted, ref, watch } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { ArrowRight, MoreFilled } from "@element-plus/icons-vue";
import { useRouter } from "vue-router";

import EmptyState from "@/components/EmptyState.vue";
import { formatDateTime } from "@/services/format";
import { useFineJobJobActionsStore } from "@/stores/fineJobJobActions";
import type {
  FineJobJobActionItem,
  FineJobJobActionPriority,
  FineJobJobActionType
} from "@/types";

const store = useFineJobJobActionsStore();
const router = useRouter();

const actionTypeOptions: Array<{ label: string; value: FineJobJobActionType | "" }> = [
  { label: "全部", value: "" },
  { label: "回复", value: "reply_recruiter" },
  { label: "面试", value: "respond_interview" },
  { label: "简历", value: "send_resume" },
  { label: "跟进", value: "followup_recruiter" },
  { label: "询问原因", value: "ask_rejection_reason" },
  { label: "审核草稿", value: "review_draft" }
];
const priorityOptions: Array<{ label: string; value: FineJobJobActionPriority | "" }> = [
  { label: "全部", value: "" },
  { label: "优先处理", value: "urgent" },
  { label: "今日处理", value: "high" },
  { label: "建议处理", value: "normal" }
];

const summaryCards = [
  { key: "urgent", label: "优先处理", type: "danger" },
  { key: "high", label: "今日处理", type: "warning" },
  { key: "normal", label: "建议处理", type: "info" },
  { key: "snoozed", label: "稍后处理", type: "" }
] as const;

const actionTypeLabels: Record<FineJobJobActionType, string> = {
  respond_interview: "回复面试安排",
  send_resume: "发送 / 补充简历",
  reply_recruiter: "回复招聘方",
  review_draft: "审核已生成草稿",
  followup_recruiter: "跟进招聘方",
  ask_rejection_reason: "询问拒绝原因"
};
const priorityLabels: Record<FineJobJobActionPriority, string> = {
  urgent: "优先处理",
  high: "今日处理",
  normal: "建议处理",
  low: "可稍后处理"
};
const stageLabels: Record<string, string> = {
  discovered: "已发现岗位",
  shortlisted: "已进入候选",
  greeted: "已打招呼",
  communicating: "沟通中",
  resume_requested: "HR 请求简历",
  resume_submitted: "已发送简历",
  resume_viewed: "简历已查看",
  under_review: "用人部门评估中",
  interview_scheduling: "面试时间沟通中",
  interviewing: "面试阶段",
  offer: "已获得 Offer",
  rejected: "已被拒绝",
  closed: "岗位关闭"
};
const waitingLabels: Record<string, string> = {
  candidate: "等我回复",
  recruiter: "等招聘方回复",
  none: "当前无需回复",
  unknown: "等待对象待判断"
};

const customSnoozeVisible = ref(false);
const customSnoozeAt = ref<Date | null>(null);
const customSnoozeActionKey = ref<string | null>(null);
const selectedActionKeys = ref<string[]>([]);

const batchActionTypes = new Set<FineJobJobActionType>([
  "respond_interview",
  "reply_recruiter",
  "followup_recruiter",
  "ask_rejection_reason"
]);

const hasSummary = computed(() => Boolean(store.summary));
const hasActiveItems = computed(() => store.items.length > 0);
const hasSnoozedItems = computed(() => store.snoozedItems.length > 0);
const batchEligibleItems = computed(() =>
  store.items.filter((item) => batchActionTypes.has(item.action_type))
);
const selectedCount = computed(() => selectedActionKeys.value.length);
const allBatchItemsSelected = computed(() =>
  batchEligibleItems.value.length > 0
  && batchEligibleItems.value.every((item) => selectedActionKeys.value.includes(item.action_key))
);
const someBatchItemsSelected = computed(() =>
  selectedCount.value > 0 && !allBatchItemsSelected.value
);
const batchResultSummary = computed(() => {
  if (!store.batchResult) return null;
  const counts = { created: 0, already_exists: 0, skipped: 0, failed: 0 };
  for (const result of store.batchResult.results) counts[result.status] += 1;
  return `新生成 ${counts.created}　已有草稿 ${counts.already_exists}　跳过 ${counts.skipped}　失败 ${counts.failed}`;
});

const summaryValue = (key: (typeof summaryCards)[number]["key"]) => {
  const value = store.summary?.[key];
  return typeof value === "number" ? value : "—";
};

const stageLabel = (value: string) => stageLabels[value] ?? "进展待确认";
const waitingLabel = (value: string) => waitingLabels[value] ?? "等待对象待判断";
const priorityLabel = (value: FineJobJobActionPriority) => priorityLabels[value];
const actionLabel = (value: FineJobJobActionType) => actionTypeLabels[value];

const formatDuration = (seconds: number) => {
  const days = Math.floor(seconds / 86_400);
  const hours = Math.floor((seconds % 86_400) / 3_600);
  if (days > 0) return `${days} 天${hours ? ` ${hours} 小时` : ""}`;
  if (hours > 0) return `${hours} 小时`;
  return "不足 1 小时";
};

const actionTiming = (item: FineJobJobActionItem) => {
  if (item.overdue_seconds > 0) return `已超期 ${formatDuration(item.overdue_seconds)}`;
  if (item.due_at) return `到期：${formatDateTime(item.due_at)}`;
  if (item.waiting_since_at) return `开始等待：${formatDateTime(item.waiting_since_at)}`;
  return "时间待确认";
};

const actionMoreType = (value: unknown): value is "snooze-1" | "snooze-3" | "snooze-custom" | "dismiss" | "complete" | "restore" =>
  ["snooze-1", "snooze-3", "snooze-custom", "dismiss", "complete", "restore"].includes(String(value));

const isValidReviewDraft = (item: FineJobJobActionItem) =>
  item.action_type === "review_draft"
  && item.reply_task?.status === "awaiting_review"
  && item.reply_task.id === item.primary_action.reply_task_id
  && item.primary_action.query.session_id === item.session_id;

const chatQueryFor = (item: FineJobJobActionItem) => {
  const query: Record<string, string> = {
    ...item.primary_action.query,
    session_id: item.session_id,
    job_id: item.job_id,
    action_type: item.action_type,
    action_key: item.action_key
  };
  if (item.primary_action.action_kind) query.action_kind = item.primary_action.action_kind;
  if (isValidReviewDraft(item) && item.reply_task) query.reply_task_id = item.reply_task.id;
  return query;
};

const openAction = async (item: FineJobJobActionItem) => {
  await router.push({ name: item.primary_action.route_name, query: chatQueryFor(item) });
};

const setActionSelected = (item: FineJobJobActionItem, selected: boolean) => {
  if (!batchActionTypes.has(item.action_type)) return;
  const keys = new Set(selectedActionKeys.value);
  if (selected) keys.add(item.action_key);
  else keys.delete(item.action_key);
  selectedActionKeys.value = [...keys];
};

const selectAllCurrentResults = (selected: boolean) => {
  selectedActionKeys.value = selected
    ? batchEligibleItems.value.map((item) => item.action_key)
    : [];
};

const generateSelectedDrafts = async () => {
  if (selectedCount.value === 0) return;
  try {
    await ElMessageBox.confirm(
      `将为 ${selectedCount.value} 个岗位生成草稿\n不会自动发送`,
      "批量生成草稿",
      { confirmButtonText: "开始生成", cancelButtonText: "取消", type: "warning" }
    );
    const keys = [...selectedActionKeys.value];
    await store.generateDrafts(keys);
    selectedActionKeys.value = [];
    ElMessage.success("批量草稿处理完成");
  } catch (value) {
    if (value === "cancel" || value === "close") return;
    ElMessage.error(store.error ?? "批量生成草稿失败");
  }
};

const openDataRefresh = async () => {
  await router.push({ name: "fine-job-refresh" });
};

const openAnalytics = async () => {
  await router.push({ name: "fine-job-analytics" });
};

const handleActionTypeChange = (value: unknown) => {
  if (value === "" || actionTypeOptions.some((option) => option.value === value)) {
    void store.setActionType(value as FineJobJobActionType | "").catch(() => undefined);
  }
};

const handlePriorityChange = (value: unknown) => {
  if (value === "" || priorityOptions.some((option) => option.value === value)) {
    void store.setPriority(value as FineJobJobActionPriority | "").catch(() => undefined);
  }
};

const startProcessing = async () => {
  const item = store.items[0];
  if (item) await openAction(item);
};

const quickSnooze = async (item: FineJobJobActionItem, days: number) => {
  try {
    await store.snooze(item.action_key, new Date(Date.now() + days * 86_400_000));
    ElMessage.success(`已稍后处理 ${days} 天`);
  } catch {
    ElMessage.error(store.error ?? "稍后处理失败");
  }
};

const openCustomSnooze = (item: FineJobJobActionItem) => {
  customSnoozeActionKey.value = item.action_key;
  customSnoozeAt.value = null;
  customSnoozeVisible.value = true;
};

const submitCustomSnooze = async () => {
  if (!customSnoozeActionKey.value || !customSnoozeAt.value) {
    ElMessage.warning("请选择稍后处理时间");
    return;
  }
  const selectedTime = customSnoozeAt.value instanceof Date
    ? customSnoozeAt.value
    : new Date(String(customSnoozeAt.value));
  if (Number.isNaN(selectedTime.getTime()) || selectedTime.getTime() <= Date.now()) {
    ElMessage.warning("稍后处理时间必须晚于当前时间");
    return;
  }
  try {
    await store.snooze(customSnoozeActionKey.value, selectedTime);
    customSnoozeVisible.value = false;
    ElMessage.success("已设置稍后处理时间");
  } catch {
    ElMessage.error(store.error ?? "稍后处理失败");
  }
};

const dismissAction = async (item: FineJobJobActionItem) => {
  try {
    await store.dismiss(item.action_key);
    ElMessage.success("已忽略这次建议");
  } catch {
    ElMessage.error(store.error ?? "忽略行动失败");
  }
};

const completeAction = async (item: FineJobJobActionItem) => {
  try {
    await store.complete(item.action_key);
    ElMessage.success("已标记为已处理");
  } catch {
    ElMessage.error(store.error ?? "标记已处理失败");
  }
};

const restoreAction = async (item: FineJobJobActionItem) => {
  try {
    await store.restore(item.action_key);
    ElMessage.success("已恢复行动");
  } catch {
    ElMessage.error(store.error ?? "恢复行动失败");
  }
};

const handleActionCommand = async (payload: unknown) => {
  if (!payload || typeof payload !== "object") return;
  const commandPayload = payload as { command?: unknown; item?: unknown };
  const item = commandPayload.item as FineJobJobActionItem | undefined;
  if (!item || !actionMoreType(commandPayload.command)) return;
  if (commandPayload.command === "snooze-1") return quickSnooze(item, 1);
  if (commandPayload.command === "snooze-3") return quickSnooze(item, 3);
  if (commandPayload.command === "snooze-custom") return openCustomSnooze(item);
  if (commandPayload.command === "dismiss") return dismissAction(item);
  if (commandPayload.command === "complete") return completeAction(item);
  return restoreAction(item);
};

const refresh = async () => {
  try {
    await store.refresh();
  } catch {
    // 错误状态由 Store 保存并由页面展示。
  }
};

watch(
  () => store.items.map((item) => `${item.action_key}:${item.action_type}`),
  () => {
    const eligibleKeys = new Set(batchEligibleItems.value.map((item) => item.action_key));
    selectedActionKeys.value = selectedActionKeys.value.filter((key) => eligibleKeys.has(key));
  }
);

onMounted(() => {
  void refresh();
});
onActivated(() => {
  void refresh();
});
</script>

<template>
  <section
    class="page-stack fine-job-page job-actions-page"
    v-loading="store.loading"
    :aria-busy="store.loading"
  >
    <header class="page-heading">
      <div>
        <p class="app-shell__eyebrow">今天要做什么</p>
        <h1>今日行动</h1>
        <p class="secondary-text">按当前岗位状态整理需要你处理的事项。</p>
      </div>
      <div class="job-actions-heading__buttons">
        <el-button plain :loading="store.loading" @click="refresh">刷新</el-button>
        <el-button
          data-testid="batch-generate-button"
          plain
          :disabled="selectedCount === 0 || store.mutating"
          :loading="store.mutating"
          @click="generateSelectedDrafts"
        >批量生成草稿<span v-if="selectedCount">（{{ selectedCount }}）</span></el-button>
        <el-button
          data-testid="start-processing-button"
          type="primary"
          :disabled="!hasActiveItems || store.mutating"
          @click="startProcessing"
        >
          <ArrowRight aria-hidden="true" />
          开始处理
        </el-button>
      </div>
    </header>

    <el-alert
      v-if="store.error"
      :title="store.invalidItemCount ? '今日行动数据提示' : '今日行动加载失败'"
      :description="store.error"
      :type="store.invalidItemCount ? 'warning' : 'error'"
      :closable="false"
      show-icon
    />

    <el-alert
      v-if="batchResultSummary"
      data-testid="batch-result"
      title="批量草稿结果"
      :description="batchResultSummary"
      type="success"
      :closable="false"
      show-icon
    />

    <section v-if="hasSummary" class="job-actions-summary" aria-label="今日行动摘要">
      <article
        v-for="card in summaryCards"
        :key="card.key"
        class="job-actions-summary__card"
        :class="`job-actions-summary__card--${card.type || 'default'}`"
      >
        <span>{{ card.label }}</span>
        <strong :data-testid="`summary-${card.key}`">{{ summaryValue(card.key) }}</strong>
      </article>
    </section>

    <section class="page-panel job-actions-filters">
      <div class="job-actions-filter-row">
        <span class="filter-label">行动类型</span>
        <el-select
          :model-value="store.actionType"
          class="job-actions-filter-select"
          @change="handleActionTypeChange"
        >
          <el-option
            v-for="option in actionTypeOptions"
            :key="option.value || 'all'"
            :label="option.label"
            :value="option.value"
          />
        </el-select>
      </div>
      <div class="job-actions-filter-row">
        <span class="filter-label">优先级</span>
        <el-select
          :model-value="store.priority"
          class="job-actions-filter-select"
          @change="handlePriorityChange"
        >
          <el-option
            v-for="option in priorityOptions"
            :key="option.value || 'all'"
            :label="option.label"
            :value="option.value"
          />
        </el-select>
      </div>
    </section>

    <section v-if="hasSummary && hasActiveItems" class="page-panel">
      <div class="section-heading">
        <div>
          <h2>需要处理</h2>
          <p class="secondary-text">列表顺序沿用后端返回顺序。</p>
        </div>
        <div class="job-actions-selection">
          <el-checkbox
            data-testid="select-all-current"
            :model-value="allBatchItemsSelected"
            :indeterminate="someBatchItemsSelected"
            :disabled="batchEligibleItems.length === 0 || store.mutating"
            @change="selectAllCurrentResults(Boolean($event))"
          >全选当前筛选结果</el-checkbox>
          <span class="secondary-text">{{ store.items.length }} 项</span>
        </div>
      </div>
      <div class="job-actions-list" data-testid="active-action-list">
        <article v-for="item in store.items" :key="item.action_key" class="job-action-card">
          <div class="job-action-card__main">
            <div class="job-action-card__heading">
              <el-checkbox
                v-if="batchActionTypes.has(item.action_type)"
                data-testid="batch-action-checkbox"
                :model-value="selectedActionKeys.includes(item.action_key)"
                :disabled="store.mutating"
                :aria-label="`选择${item.title}`"
                @change="setActionSelected(item, Boolean($event))"
              />
              <div class="job-action-card__title">
                <h3>{{ item.title }}</h3>
                <p>{{ item.company_name }}</p>
              </div>
              <el-tag
                :type="item.priority_tier === 'urgent' ? 'danger' : item.priority_tier === 'high' ? 'warning' : 'info'"
                effect="plain"
              >{{ priorityLabel(item.priority_tier) }}</el-tag>
            </div>
            <div class="job-action-card__meta">
              <span>{{ stageLabel(item.stage) }}</span>
              <span>{{ waitingLabel(item.waiting_on) }}</span>
              <span>{{ actionLabel(item.action_type) }}</span>
              <span>{{ actionTiming(item) }}</span>
            </div>
            <p class="job-action-card__reason"><strong>原因：</strong>{{ item.reason_summary }}</p>
          </div>
          <div class="job-action-card__actions">
            <el-button
              data-testid="action-primary"
              type="primary"
              :disabled="store.mutating"
              @click="openAction(item)"
            >{{ item.primary_action.label }}</el-button>
            <el-dropdown
              trigger="click"
              :disabled="store.mutating"
              @command="handleActionCommand"
            >
              <el-button plain :icon="MoreFilled" aria-label="更多操作">更多</el-button>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item :command="{ command: 'snooze-1', item }">稍后 1 天</el-dropdown-item>
                  <el-dropdown-item :command="{ command: 'snooze-3', item }">稍后 3 天</el-dropdown-item>
                  <el-dropdown-item :command="{ command: 'snooze-custom', item }">自定义稍后时间</el-dropdown-item>
                  <el-dropdown-item divided :command="{ command: 'complete', item }">标记已处理</el-dropdown-item>
                  <el-dropdown-item :command="{ command: 'dismiss', item }">忽略这次建议</el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
          </div>
        </article>
      </div>
    </section>

    <div v-else-if="!store.loading && hasSummary" class="job-actions-empty">
      <EmptyState
        title="今天暂时没有需要处理的岗位"
        description="可以先更新求职数据，或查看求职分析了解当前进展。"
        action-text="去求职数据更新"
        @action="openDataRefresh"
      />
      <el-button plain @click="openAnalytics">查看求职分析</el-button>
    </div>

    <section class="page-panel job-actions-snoozed">
      <div class="section-heading">
        <div>
          <h2>稍后处理</h2>
          <p class="secondary-text">到期后由后端自动恢复为当前行动。</p>
        </div>
        <span class="secondary-text">{{ hasSnoozedItems ? store.snoozedItems.length : summaryValue('snoozed') }} 项</span>
      </div>
      <div v-if="hasSnoozedItems" class="job-actions-list" data-testid="snoozed-action-list">
        <article v-for="item in store.snoozedItems" :key="item.action_key" class="job-action-card job-action-card--snoozed">
          <div class="job-action-card__main">
            <div class="job-action-card__heading">
              <div>
                <h3>{{ item.title }}</h3>
                <p>{{ item.company_name }}</p>
              </div>
              <el-tag type="info" effect="plain">{{ item.snoozed_until ? `稍后：${formatDateTime(item.snoozed_until)}` : "稍后处理" }}</el-tag>
            </div>
            <div class="job-action-card__meta">
              <span>{{ stageLabel(item.stage) }}</span>
              <span>{{ actionLabel(item.action_type) }}</span>
            </div>
            <p class="job-action-card__reason">{{ item.reason_summary }}</p>
          </div>
          <div class="job-action-card__actions">
            <el-button plain :disabled="store.mutating" @click="restoreAction(item)">恢复</el-button>
            <el-dropdown
              trigger="click"
              :disabled="store.mutating"
              @command="handleActionCommand"
            >
              <el-button plain :icon="MoreFilled" aria-label="更多操作">更多</el-button>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item :command="{ command: 'complete', item }">标记已处理</el-dropdown-item>
                  <el-dropdown-item :command="{ command: 'dismiss', item }">忽略这次建议</el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
          </div>
        </article>
      </div>
      <p v-else class="secondary-text">暂无稍后处理的行动。</p>
    </section>

    <el-dialog v-model="customSnoozeVisible" title="自定义稍后处理时间" width="min(460px, 92vw)">
      <el-date-picker
        v-model="customSnoozeAt"
        type="datetime"
        placeholder="选择时间"
        :editable="false"
        class="custom-snooze-picker"
      />
      <template #footer>
        <el-button @click="customSnoozeVisible = false">取消</el-button>
        <el-button type="primary" :loading="store.mutating" @click="submitCustomSnooze">确认</el-button>
      </template>
    </el-dialog>
  </section>
</template>

<style scoped>
.job-actions-page { display: grid; gap: 18px; }
.job-actions-heading__buttons { display: flex; align-items: center; gap: 10px; }
.job-actions-heading__buttons .el-button { display: inline-flex; align-items: center; gap: 6px; }
.job-actions-summary { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 14px; }
.job-actions-summary__card { display: grid; gap: 8px; min-height: 104px; padding: 18px; border: 1px solid var(--el-border-color-lighter); border-radius: 16px; background: var(--el-bg-color); box-shadow: 0 10px 24px rgba(32, 50, 41, 0.06); }
.job-actions-summary__card span { color: var(--el-text-color-secondary); }
.job-actions-summary__card strong { font-size: 30px; line-height: 1; }
.job-actions-summary__card--danger strong { color: var(--el-color-danger); }
.job-actions-summary__card--warning strong { color: var(--el-color-warning-dark-2); }
.job-actions-summary__card--info strong { color: var(--el-color-primary); }
.job-actions-filters { display: flex; flex-wrap: wrap; gap: 24px; align-items: center; }
.job-actions-filter-row { display: flex; align-items: center; gap: 12px; }
.filter-label { min-width: 76px; font-weight: 600; }
.job-actions-filter-select { width: 150px; }
.job-actions-selection { display: flex; align-items: center; gap: 16px; }
.section-heading { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; margin-bottom: 16px; }
.section-heading h2, .section-heading p { margin: 0; }
.job-actions-list { display: grid; gap: 12px; max-height: 680px; overflow-y: auto; padding-right: 4px; }
.job-action-card { display: flex; justify-content: space-between; gap: 18px; padding: 18px; border: 1px solid var(--el-border-color-lighter); border-radius: 14px; background: var(--el-bg-color); }
.job-action-card--snoozed { background: var(--el-fill-color-lighter); }
.job-action-card__main { min-width: 0; flex: 1 1 auto; }
.job-action-card__heading { display: flex; justify-content: space-between; align-items: flex-start; gap: 14px; }
.job-action-card__title { min-width: 0; flex: 1 1 auto; }
.job-action-card__heading h3, .job-action-card__heading p { margin: 0; }
.job-action-card__heading p { color: var(--el-text-color-secondary); }
.job-action-card__meta { display: flex; flex-wrap: wrap; gap: 8px 16px; margin-top: 12px; color: var(--el-text-color-secondary); font-size: 13px; }
.job-action-card__reason { margin: 12px 0 0; line-height: 1.6; overflow-wrap: anywhere; }
.job-action-card__actions { display: flex; align-items: center; align-self: center; gap: 8px; flex: 0 0 auto; }
.job-actions-empty { display: grid; justify-items: center; gap: 12px; }
.custom-snooze-picker { width: 100%; }
@media (max-width: 900px) {
  .job-actions-summary { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .job-action-card { flex-direction: column; }
  .job-action-card__actions { align-self: flex-start; }
}
@media (max-width: 560px) {
  .job-actions-summary { grid-template-columns: 1fr; }
  .job-actions-filters, .job-actions-filter-row { align-items: stretch; flex-direction: column; }
  .job-actions-filter-select { width: 100%; }
  .job-actions-heading__buttons { width: 100%; }
  .job-actions-heading__buttons .el-button { flex: 1; }
}
</style>
