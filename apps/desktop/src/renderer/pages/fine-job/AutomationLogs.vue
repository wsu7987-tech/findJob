<script setup lang="ts">
import { onMounted, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";

import { formatDateTime } from "@/services/format";
import { useFineJobDeliveryRunsStore } from "@/stores/fineJobDeliveryRuns";
import type { FineJobActionLog } from "@/types";

const runsStore = useFineJobDeliveryRunsStore();
const filters = ref({
  query: "",
  level: "",
  category: "",
  outcome: "",
  source: "",
  actionType: "",
  dateRange: null as [string, string] | null
});
const selectedLog = ref<FineJobActionLog | null>(null);
const detailDrawerOpen = ref(false);
const cleanupBefore = ref("");
const cleanupSource = ref<"all" | "legacy_run" | "main_workflow">("legacy_run");

const load = async (page = runsStore.logPage) => {
  await runsStore.loadRecentLogs({
    query: filters.value.query,
    level: filters.value.level,
    category: filters.value.category,
    outcome: filters.value.outcome,
    source: filters.value.source,
    action_type: filters.value.actionType,
    created_from: filters.value.dateRange?.[0],
    created_to: filters.value.dateRange?.[1],
    page,
    page_size: runsStore.logPageSize
  });
};

const search = () => load(1);
const reset = () => {
  filters.value = { query: "", level: "", category: "", outcome: "", source: "", actionType: "", dateRange: null };
  void search();
};
const applyQuickView = (value: "issues" | "review" | "execution" | "capture") => {
  filters.value = {
    query: "",
    level: value === "issues" ? "issue" : "",
    category: value === "issues" ? "" : value,
    outcome: "",
    source: "",
    actionType: "",
    dateRange: null
  };
  void search();
};

const showDetail = (row: FineJobActionLog) => {
  selectedLog.value = row;
  detailDrawerOpen.value = true;
};

const cleanup = async () => {
  if (!cleanupBefore.value) {
    ElMessage.warning("请先选择清理截止时间");
    return;
  }
  try {
    await ElMessageBox.confirm(
      `将删除 ${cleanupBefore.value} 之前的日志，确认继续？`,
      "清理旧日志",
      { type: "warning", confirmButtonText: "确认清理" }
    );
    const result = await runsStore.cleanupLogs(cleanupBefore.value, cleanupSource.value);
    await search();
    ElMessage.success(`已清理 ${result.deleted} 条日志`);
  } catch (value) {
    if (value !== "cancel" && value !== "close") ElMessage.error(runsStore.error ?? "日志清理失败");
  }
};

const categoryLabel = (value?: string) => ({
  review: "审批", execution: "执行", capture: "采集", chat: "代聊", system: "系统"
} as Record<string, string>)[value ?? ""] ?? value ?? "系统";
const sourceLabel = (value?: string) => value === "legacy_run" ? "旧任务" : "当前主链路";
const outcomeLabel = (value?: string) => ({
  succeeded: "成功", failed: "失败", warning: "警告", info: "信息"
} as Record<string, string>)[value ?? ""] ?? value ?? "信息";
const outcomeType = (value?: string) => value === "failed" ? "danger" : value === "warning" ? "warning" : value === "succeeded" ? "success" : "info";
const actionLabel = (value: string) => ({
  review_approved: "批准岗位", review_rejected: "拒绝岗位", review_archived: "归档事项",
  review_restored: "恢复事项", boss_page_opened: "打开岗位页面",
  boss_task_matched: "匹配执行任务", boss_task_completed: "任务完成",
  boss_return_to_review: "退回待确认", executor_control: "执行器控制", boss_executor_risk: "执行器风险",
  run_created: "创建旧任务", boss_search_started: "开始采集", boss_search_finished: "完成采集",
  boss_collection_paused: "采集暂停", dry_run_guard: "旧任务保护"
} as Record<string, string>)[value] ?? value;

onMounted(() => void load(1));
</script>

<template>
  <section class="page-stack fine-job-page">
    <div class="page-heading">
      <div>
        <p class="panel-eyebrow">Activity History</p>
        <h1>动作日志</h1>
        <p class="secondary-text">按岗位、动作、结果和时间追踪采集、审批及执行过程。</p>
      </div>
      <el-button :loading="runsStore.loading" @click="load()">刷新</el-button>
    </div>

    <el-alert v-if="runsStore.error" type="error" title="动作日志加载失败" :description="runsStore.error" show-icon />

    <section class="page-panel log-filters">
      <div class="quick-views">
        <span class="secondary-text">快捷查看</span>
        <el-button size="small" @click="applyQuickView('issues')">警告与异常</el-button>
        <el-button size="small" @click="applyQuickView('review')">审批操作</el-button>
        <el-button size="small" @click="applyQuickView('execution')">执行过程</el-button>
        <el-button size="small" @click="applyQuickView('capture')">岗位采集</el-button>
      </div>
      <el-form label-position="top">
        <div class="log-filter-grid">
          <el-form-item label="岗位 / 公司 / 说明">
            <el-input v-model="filters.query" clearable placeholder="输入关键词" @keyup.enter="search" />
          </el-form-item>
          <el-form-item label="业务分类">
            <el-select v-model="filters.category" clearable placeholder="全部分类">
              <el-option label="审批" value="review" /><el-option label="执行" value="execution" />
              <el-option label="采集" value="capture" /><el-option label="代聊" value="chat" /><el-option label="系统" value="system" />
            </el-select>
          </el-form-item>
          <el-form-item label="结果">
            <el-select v-model="filters.outcome" clearable placeholder="全部结果">
              <el-option label="成功" value="succeeded" /><el-option label="失败" value="failed" />
              <el-option label="警告" value="warning" /><el-option label="信息" value="info" />
            </el-select>
          </el-form-item>
          <el-form-item label="级别">
            <el-select v-model="filters.level" clearable placeholder="全部级别">
              <el-option label="警告或错误" value="issue" /><el-option label="信息" value="info" />
              <el-option label="警告" value="warning" /><el-option label="错误" value="error" />
            </el-select>
          </el-form-item>
          <el-form-item label="来源">
            <el-select v-model="filters.source" clearable placeholder="全部来源">
              <el-option label="当前主链路" value="main_workflow" /><el-option label="旧版任务" value="legacy_run" />
            </el-select>
          </el-form-item>
          <el-form-item label="具体动作">
            <el-select v-model="filters.actionType" filterable clearable placeholder="全部动作">
              <el-option v-for="item in runsStore.logActionTypes" :key="item" :label="actionLabel(item)" :value="item" />
            </el-select>
          </el-form-item>
          <el-form-item label="发生时间">
            <el-date-picker
              v-model="filters.dateRange"
              type="datetimerange"
              value-format="YYYY-MM-DDTHH:mm:ss[Z]"
              start-placeholder="开始时间"
              end-placeholder="结束时间"
            />
          </el-form-item>
        </div>
        <div class="filter-actions"><el-button type="primary" @click="search">查询</el-button><el-button @click="reset">重置</el-button></div>
      </el-form>
    </section>

    <section class="table-panel">
      <el-table v-loading="runsStore.loading" :data="runsStore.logs" empty-text="当前筛选条件下暂无动作日志" @row-click="showDetail">
        <el-table-column label="时间" width="180"><template #default="{ row }">{{ formatDateTime(row.created_at) }}</template></el-table-column>
        <el-table-column label="对象" min-width="180">
          <template #default="{ row }"><strong>{{ row.job_title || row.run_id || "系统" }}</strong><p class="secondary-text log-company">{{ row.company_name || "" }}</p></template>
        </el-table-column>
        <el-table-column label="分类" width="95"><template #default="{ row }">{{ categoryLabel(row.category) }}</template></el-table-column>
        <el-table-column label="动作" min-width="170"><template #default="{ row }">{{ actionLabel(row.action_type) }}</template></el-table-column>
        <el-table-column label="结果" width="95"><template #default="{ row }"><el-tag :type="outcomeType(row.outcome)">{{ outcomeLabel(row.outcome) }}</el-tag></template></el-table-column>
        <el-table-column prop="message" label="说明" min-width="320" show-overflow-tooltip />
        <el-table-column label="来源" width="110"><template #default="{ row }">{{ sourceLabel(row.source) }}</template></el-table-column>
        <el-table-column label="操作" width="80"><template #default="{ row }"><el-button link type="primary" @click.stop="showDetail(row)">详情</el-button></template></el-table-column>
      </el-table>
      <div class="log-pagination">
        <el-pagination
          v-model:current-page="runsStore.logPage"
          v-model:page-size="runsStore.logPageSize"
          :total="runsStore.logTotal"
          :page-sizes="[25, 50, 100]"
          layout="total, sizes, prev, pager, next"
          @change="load()"
        />
      </div>
    </section>

    <section class="page-panel cleanup-panel">
      <div><h2>清理旧日志</h2><p class="secondary-text">按截止时间清理历史记录，当前筛选结果不影响清理范围。</p></div>
      <div class="cleanup-actions">
        <el-select v-model="cleanupSource"><el-option label="仅旧版任务" value="legacy_run" /><el-option label="仅当前主链路" value="main_workflow" /><el-option label="全部来源" value="all" /></el-select>
        <el-date-picker v-model="cleanupBefore" type="date" value-format="YYYY-MM-DDT00:00:00[Z]" placeholder="删除此日期之前" />
        <el-button type="danger" plain @click="cleanup">清理</el-button>
      </div>
    </section>

    <el-drawer v-model="detailDrawerOpen" size="48%" :title="selectedLog ? actionLabel(selectedLog.action_type) : '日志详情'">
      <template v-if="selectedLog">
        <el-descriptions :column="2" border>
          <el-descriptions-item label="时间">{{ formatDateTime(selectedLog.created_at) }}</el-descriptions-item>
          <el-descriptions-item label="结果">{{ outcomeLabel(selectedLog.outcome) }}</el-descriptions-item>
          <el-descriptions-item label="分类">{{ categoryLabel(selectedLog.category) }}</el-descriptions-item>
          <el-descriptions-item label="来源">{{ sourceLabel(selectedLog.source) }}</el-descriptions-item>
          <el-descriptions-item label="岗位">{{ selectedLog.job_title || "-" }}</el-descriptions-item>
          <el-descriptions-item label="公司">{{ selectedLog.company_name || "-" }}</el-descriptions-item>
        </el-descriptions>
        <h3>说明</h3><p class="log-message">{{ selectedLog.message }}</p>
        <h3>结构化详情</h3><pre class="detail-json">{{ JSON.stringify(selectedLog.detail, null, 2) }}</pre>
      </template>
    </el-drawer>
  </section>
</template>

<style scoped>
.quick-views, .filter-actions, .cleanup-panel, .cleanup-actions { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.quick-views { margin-bottom: 14px; }
.log-filter-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(205px, 1fr)); gap: 4px 16px; }
.log-pagination { display: flex; justify-content: flex-end; padding-top: 18px; }
.log-company { margin: 2px 0 0; }
.cleanup-panel { justify-content: space-between; }
.cleanup-actions > * { width: 190px; }
.log-message { line-height: 1.75; white-space: pre-wrap; }
.detail-json { padding: 14px; overflow: auto; border-radius: 8px; background: var(--el-fill-color-light); white-space: pre-wrap; }
</style>
