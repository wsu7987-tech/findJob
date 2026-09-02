<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { useRoute } from "vue-router";

import { ApiError, api } from "@/services/api";
import { formatDateTime } from "@/services/format";
import { useFineJobBossCaptureStore } from "@/stores/fineJobBossCapture";
import { useFineJobBossExecutorStore } from "@/stores/fineJobBossExecutor";
import { useFineJobBossHistoryStore } from "@/stores/fineJobBossHistory";
import { useFineJobStrategiesStore } from "@/stores/fineJobStrategies";
import type { FineJobBossHistoryJob, FineJobBossHistorySortField } from "@/types";

const captureStore = useFineJobBossCaptureStore();
const executorStore = useFineJobBossExecutorStore();
const historyStore = useFineJobBossHistoryStore();
const strategiesStore = useFineJobStrategiesStore();
const route = useRoute();
const selectedJob = ref<FineJobBossHistoryJob | null>(null);
const detailDrawerOpen = ref(false);
const recommendationStrategyId = ref<string | null>(null);
const dateRange = ref<string[]>([]);
const filters = reactive({
  query: "",
  searchKeyword: "",
  city: "",
  companyScale: "",
  companyIndustry: "",
  companyStage: "",
  detailStatus: "",
  repeatStatus: "all" as "all" | "first_seen" | "repeated",
  sortBy: "last_collected_at" as FineJobBossHistorySortField,
  sortOrder: "desc" as "asc" | "desc"
});

const companyScaleOptions = [
  "0-20人",
  "20-99人",
  "100-499人",
  "500-999人",
  "1000-9999人",
  "10000人以上"
];
const companyStageOptions = ["未融资", "不需要融资", "天使轮", "A轮", "B轮", "C轮", "D轮及以上", "已上市"];
const companyIndustryOptions = ["人工智能", "互联网", "计算机软件", "电子商务", "云计算", "游戏", "移动互联网"];

const queryPayload = computed(() => ({
  query: filters.query.trim(),
  search_keyword: filters.searchKeyword.trim(),
  city: filters.city,
  company_scale: filters.companyScale,
  company_industry: filters.companyIndustry,
  company_stage: filters.companyStage,
  detail_status: filters.detailStatus,
  repeat_status: filters.repeatStatus,
  collected_from: dateRange.value[0] ? `${dateRange.value[0]}T00:00:00Z` : "",
  collected_to: dateRange.value[1] ? `${dateRange.value[1]}T23:59:59Z` : "",
  sort_by: filters.sortBy,
  sort_order: filters.sortOrder,
  page: historyStore.page,
  page_size: historyStore.pageSize
}));
const detailTaskRunning = computed(
  () => historyStore.detailTask?.status === "queued" || historyStore.detailTask?.status === "running"
);
const historyActionRunning = computed(
  () => detailTaskRunning.value || historyStore.deliveryJobId !== null
);
const detailProgressPercentage = computed(() => {
  const task = historyStore.detailTask;
  if (!task?.progress_total) return 0;
  return Math.min(100, Math.round((task.progress_current / task.progress_total) * 100));
});

const loadHistory = async () => {
  try {
    await historyStore.load(queryPayload.value);
  } catch {
    ElMessage.error(historyStore.error ?? "历史采集记录加载失败");
  }
};

const search = async () => {
  historyStore.page = 1;
  await loadHistory();
};

const reset = async () => {
  Object.assign(filters, {
    query: "",
    searchKeyword: "",
    city: "",
    companyScale: "",
    companyIndustry: "",
    companyStage: "",
    detailStatus: "",
    repeatStatus: "all"
  });
  dateRange.value = [];
  historyStore.page = 1;
  await loadHistory();
};

const handlePageChange = async (value: number) => {
  historyStore.page = value;
  await loadHistory();
};

const handlePageSizeChange = async (value: number) => {
  historyStore.pageSize = value;
  historyStore.page = 1;
  await loadHistory();
};

const handleSortChange = async ({
  prop,
  order
}: {
  prop: string;
  order: "ascending" | "descending" | null;
}) => {
  const fields: Record<string, FineJobBossHistorySortField> = {
    title: "title",
    boss_name: "company_name",
    first_collected_at: "first_collected_at",
    last_collected_at: "last_collected_at",
    collect_count: "collect_count"
  };
  filters.sortBy = order && fields[prop] ? fields[prop] : "last_collected_at";
  filters.sortOrder = order === "ascending" ? "asc" : "desc";
  historyStore.page = 1;
  await loadHistory();
};

const captureHistoryDetails = async (job: FineJobBossHistoryJob) => {
  if (!captureStore.status?.running) {
    ElMessage.warning("请先在平台登录或岗位采集页面打开 BOSS 专用浏览器");
    return;
  }
  try {
    await historyStore.captureDetails(job.id);
    ElMessage.success(`${detailActionLabel(job)}任务已启动`);
  } catch {
    ElMessage.error(historyStore.error ?? "启动岗位详情采集失败");
  }
};

const evaluateHistoryDelivery = async (
  job: FineJobBossHistoryJob,
  contextStaleAction?: "regenerate" | "use_current" | "cancel"
) => {
  if (!recommendationStrategyId.value) {
    ElMessage.warning("请先选择岗位建议投递策略");
    return;
  }
  if (job.detail_status !== "completed") {
    ElMessage.warning("请先完成该岗位详情采集");
    return;
  }
  try {
    const updated = await historyStore.evaluateDelivery(job.id, {
      recommendation_strategy_id: recommendationStrategyId.value,
      context_stale_action: contextStaleAction
    });
    selectedJob.value = updated;
    ElMessage.success("投递建议已获取");
  } catch (errorValue) {
    if (errorValue instanceof ApiError && errorValue.errorCategory === "CONTEXT_STALE_CONFIRMATION_REQUIRED") {
      try {
        await ElMessageBox.confirm(
          "当前岗位评估上下文已过期。请选择本次任务使用的版本。",
          "上下文已过期",
          {
            type: "warning",
            confirmButtonText: "重新生成并继续",
            cancelButtonText: "使用当前版本",
            distinguishCancelAndClose: true
          }
        );
        await evaluateHistoryDelivery(job, "regenerate");
      } catch (action) {
        if (action === "cancel") await evaluateHistoryDelivery(job, "use_current");
      }
      return;
    }
    ElMessage.error(historyStore.error ?? "获取投递建议失败");
  }
};

const detailActionLabel = (job: FineJobBossHistoryJob) =>
  job.detail_status === "completed" || Boolean(job.detail) ? "重新采集" : "采集";

const openDetail = (job: FineJobBossHistoryJob) => {
  selectedJob.value = job;
  detailDrawerOpen.value = true;
};

const openInDedicatedBrowser = async (job: FineJobBossHistoryJob) => {
  try {
    await executorStore.openJob(job.id, "history");
    ElMessage.success("已在FineJob专用浏览器打开该岗位；未执行打招呼");
  } catch {
    ElMessage.error(executorStore.error ?? "打开岗位页面失败");
  }
};

const detailStatusLabel = (status?: string) => ({
  completed: "已采详情",
  failed: "详情失败",
  queued: "等待采集",
  collecting: "采集中",
  not_collected: "未采详情"
}[status || "not_collected"] || "未采详情");

const detailStatusType = (status?: string) => {
  if (status === "completed") return "success";
  if (status === "failed") return "danger";
  if (status === "queued" || status === "collecting") return "warning";
  return "info";
};

const filterStatusLabel = (status?: string | null) => ({
  pass: "通过",
  pass_for_human: "跳过",
  review: "待判断",
  reject: "不通过",
  exclude: "冷却排除"
}[status || ""] || "未筛选");

const filterStatusType = (status?: string | null) => {
  if (status === "pass" || status === "pass_for_human") return "success";
  if (status === "review") return "warning";
  if (status === "reject" || status === "exclude") return "danger";
  return "info";
};

const applicationStatusLabel = (status?: string | null) => ({
  pending_greeting: "待打招呼",
  pending_application: "待投递",
  communicating: "沟通中",
  rejected: "已被拒绝"
}[status || ""] || "");

const applicationStatusType = (status?: string | null) => {
  if (status === "communicating") return "success";
  if (status === "rejected") return "danger";
  if (status === "pending_greeting" || status === "pending_application") return "warning";
  return "info";
};

const filterStrategyName = (strategyId?: string | null) => {
  if (!strategyId) return "未关联";
  return strategiesStore.filters.find((item) => item.id === strategyId)?.name || strategyId;
};

const deliveryDecisionLabel = (decision?: string) =>
  ({ recommend: "建议投递", reject: "不建议", review: "待判断" }[decision || ""] || "未评估");
const deliveryDecisionType = (decision?: string) =>
  decision === "recommend" ? "success" : decision === "reject" ? "danger" : decision === "review" ? "warning" : "info";
const deliveryEvaluationReasons = (job: FineJobBossHistoryJob) =>
  (job.delivery_evaluation?.reasons ?? []).join("；") || job.recommendation_reason || "暂无评估理由";
const deliveryEvaluationRisks = (job: FineJobBossHistoryJob) =>
  (job.delivery_evaluation?.risks ?? []).join("；");
const deliveryEvaluationMissingFields = (job: FineJobBossHistoryJob) =>
  (job.delivery_evaluation?.missing_fields ?? []).join("、");

onMounted(async () => {
  await Promise.all([
    captureStore.loadCities(),
    captureStore.loadStatus(),
    loadHistory(),
    strategiesStore.load()
  ]);
  const initialRecommendation = strategiesStore.recommendations.find((item) => item.enabled)
    ?? strategiesStore.recommendations[0];
  recommendationStrategyId.value = initialRecommendation?.id ?? null;
  const historyId = String(route.query.history_id || "");
  if (historyId) {
    try {
      selectedJob.value = await api.getFineJobBossCaptureHistoryJob(historyId);
      detailDrawerOpen.value = true;
    } catch (error) {
      ElMessage.error((error as Error).message || "历史岗位详情加载失败");
    }
  }
});

onBeforeUnmount(() => historyStore.stopDetailPolling());

watch(
  () => historyStore.detailTask?.status,
  async (status) => {
    if (status === "completed") {
      const selectedId = selectedJob.value?.id;
      await loadHistory();
      if (selectedId) {
        selectedJob.value = historyStore.items.find((item) => item.id === selectedId) ?? null;
      }
      ElMessage.success("岗位详情采集完成");
      historyStore.clearDetailTask();
    } else if (status === "failed") {
      ElMessage.error(historyStore.detailTask?.error_message || "岗位详情采集失败");
    }
  }
);
</script>

<template>
  <section class="page-stack fine-job-page">
    <div class="page-heading">
      <div>
        <p class="panel-eyebrow">BOSS Capture History</p>
        <h1>历史采集</h1>
        <p class="secondary-text">按岗位去重保存；重复采集会更新最后采集时间和采集次数。</p>
      </div>
      <el-tag type="info">共 {{ historyStore.total }} 个岗位</el-tag>
    </div>

    <el-alert
      v-if="historyStore.error"
      type="error"
      title="历史采集操作失败"
      :description="historyStore.error"
      show-icon
    />

    <section class="page-panel history-filter-panel">
      <el-form label-position="top">
        <div class="history-filter-grid">
          <el-form-item label="岗位 / 公司 / 技能">
            <el-input v-model="filters.query" clearable placeholder="输入关键词" @keyup.enter="search" />
          </el-form-item>
          <el-form-item label="搜索词">
            <el-input v-model="filters.searchKeyword" clearable placeholder="最近采集使用的搜索词" @keyup.enter="search" />
          </el-form-item>
          <el-form-item label="城市">
            <el-select v-model="filters.city" filterable clearable placeholder="全部城市">
              <el-option v-for="city in captureStore.cities" :key="city.code" :label="city.name" :value="city.name" />
            </el-select>
          </el-form-item>
          <el-form-item label="公司规模">
            <el-select v-model="filters.companyScale" filterable clearable allow-create placeholder="全部规模">
              <el-option v-for="scale in companyScaleOptions" :key="scale" :label="scale" :value="scale" />
            </el-select>
          </el-form-item>
          <el-form-item label="公司行业">
            <el-select v-model="filters.companyIndustry" filterable clearable allow-create placeholder="全部行业">
              <el-option v-for="item in companyIndustryOptions" :key="item" :label="item" :value="item" />
            </el-select>
          </el-form-item>
          <el-form-item label="融资阶段">
            <el-select v-model="filters.companyStage" filterable clearable allow-create placeholder="全部阶段">
              <el-option v-for="item in companyStageOptions" :key="item" :label="item" :value="item" />
            </el-select>
          </el-form-item>
          <el-form-item label="详情状态">
            <el-select v-model="filters.detailStatus" clearable placeholder="全部状态">
              <el-option label="已采详情" value="completed" />
              <el-option label="未采详情" value="not_collected" />
              <el-option label="详情失败" value="failed" />
            </el-select>
          </el-form-item>
          <el-form-item label="采集次数">
            <el-select v-model="filters.repeatStatus">
              <el-option label="全部岗位" value="all" />
              <el-option label="仅首次采集" value="first_seen" />
              <el-option label="重复采集过" value="repeated" />
            </el-select>
          </el-form-item>
          <el-form-item label="最后采集日期">
            <el-date-picker
              v-model="dateRange"
              type="daterange"
              value-format="YYYY-MM-DD"
              start-placeholder="开始日期"
              end-placeholder="结束日期"
              range-separator="至"
              clearable
            />
          </el-form-item>
        </div>
      </el-form>
      <div class="history-filter-actions">
        <el-select v-model="recommendationStrategyId" clearable placeholder="选择建议投递策略">
          <el-option
            v-for="item in strategiesStore.recommendations"
            :key="item.id"
            :label="item.name"
            :value="item.id"
          />
        </el-select>
        <el-button type="primary" :loading="historyStore.loading" @click="search">查询</el-button>
        <el-button :disabled="historyStore.loading" @click="reset">重置</el-button>
      </div>
    </section>

    <section
      v-if="historyStore.detailTask && historyStore.detailTask.status !== 'completed'"
      class="page-panel history-detail-progress"
    >
      <div class="panel-title-row">
        <div><p class="panel-eyebrow">Detail Progress</p><h2>岗位详情采集</h2></div>
        <el-tag :type="historyStore.detailTask.status === 'failed' ? 'danger' : 'warning'">
          {{ historyStore.detailTask.status === "failed" ? "失败" : "进行中" }}
        </el-tag>
      </div>
      <el-progress
        :percentage="detailProgressPercentage"
        :status="historyStore.detailTask.status === 'failed' ? 'exception' : undefined"
      />
      <p>{{ historyStore.detailTask.message }}</p>
    </section>

    <section class="page-panel">
      <el-table
        v-loading="historyStore.loading"
        :data="historyStore.items"
        row-key="id"
        max-height="500"
        :default-sort="{ prop: 'last_collected_at', order: 'descending' }"
        empty-text="暂无历史采集岗位"
        @sort-change="handleSortChange"
        @row-click="openDetail"
      >
                <el-table-column label="筛选" width="100">
          <template #default="{ row }">
            <el-tag :type="filterStatusType(row.filter_status)" size="small">
              {{ filterStatusLabel(row.filter_status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="投递状态" width="120">
          <template #default="{ row }">
            <el-tag v-if="row.application_status" :type="applicationStatusType(row.application_status)" size="small">
              {{ applicationStatusLabel(row.application_status) }}
            </el-tag>
            <span v-else class="secondary-text">未设置</span>
          </template>
        </el-table-column>

        <el-table-column prop="title" label="岗位" min-width="180" show-overflow-tooltip sortable="custom" />
        <el-table-column prop="boss_name" label="公司" min-width="190" sortable="custom">
          <template #default="{ row }">
            <span>{{ row.boss_name }}</span>
            <el-tag v-if="row.is_outsourcing_company" type="warning" size="small">外包</el-tag>
            <el-tag v-if="row.is_blacklisted" type="danger" size="small">黑名单</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="salary" label="薪资" width="110" />
                <el-table-column label="投递建议" min-width="130" show-overflow-tooltip>
          <template #default="{ row }">
            {{ row.delivery_evaluation?.summary || row.recommendation_reason || "未获取" }}
          </template>
        </el-table-column>
        <el-table-column prop="company_scale" label="公司规模" width="120" />

        <el-table-column prop="search_keyword" label="搜索词" min-width="130" show-overflow-tooltip />


        <el-table-column prop="company_industry" label="行业" width="120" show-overflow-tooltip />
        <el-table-column prop="company_stage" label="融资阶段" width="110" />
        
        <el-table-column prop="location" label="地点" min-width="130" show-overflow-tooltip />
        <el-table-column prop="experience" label="经验" width="100" />
        <el-table-column prop="degree" label="学历" width="90" />
        <el-table-column prop="first_collected_at" label="首次采集" width="190" sortable="custom">
          <template #default="{ row }">{{ formatDateTime(row.first_collected_at) }}</template>
        </el-table-column>
        <el-table-column prop="last_collected_at" label="最后采集" width="190" sortable="custom">
          <template #default="{ row }">{{ formatDateTime(row.last_collected_at) }}</template>
        </el-table-column>
        <el-table-column prop="collect_count" label="次数" width="100" align="center" sortable="custom" />

        <el-table-column label="记录" width="90">
          <template #default="{ row }">
            <el-tag :type="row.collect_count > 1 ? 'warning' : 'success'" size="small">
              {{ row.collect_count > 1 ? "重复" : "首次" }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="详情" width="110">
          <template #default="{ row }">
            <el-tag :type="detailStatusType(row.detail_status)" size="small">
              {{ detailStatusLabel(row.detail_status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="150" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click.stop="openDetail(row)">详情</el-button>
            <el-button
              link
              type="primary"
              :loading="executorStore.openingJobId === row.id"
              @click.stop="openInDedicatedBrowser(row)"
            >
              打开
            </el-button>
            <el-button
              v-if="row.detail_status === 'completed' && !row.delivery_evaluation"
              link
              type="warning"
              :loading="historyStore.deliveryJobId === row.id"
              :disabled="historyActionRunning || !recommendationStrategyId"
              @click.stop="evaluateHistoryDelivery(row)"
            >
              建议
            </el-button>
            <el-button
              v-else-if="row.detail_status !== 'completed'"
              link
              type="success"
              :loading="historyStore.detailJobId === row.id"
              :disabled="historyActionRunning && historyStore.detailJobId !== row.id"
              @click.stop="captureHistoryDetails(row)"
            >
              {{ detailActionLabel(row) }}
            </el-button>
          </template>
        </el-table-column>
      </el-table>

      <div class="history-pagination">
        <el-pagination
          background
          layout="total, sizes, prev, pager, next, jumper"
          :total="historyStore.total"
          :current-page="historyStore.page"
          :page-size="historyStore.pageSize"
          :page-sizes="[10, 20, 50, 100]"
          @current-change="handlePageChange"
          @size-change="handlePageSizeChange"
        />
      </div>
    </section>

    <el-drawer v-model="detailDrawerOpen" size="48%" :title="selectedJob?.title || '岗位详情'">
      <template v-if="selectedJob">
        <div class="history-detail-heading">
          <h2>{{ selectedJob.title }}</h2>
          <p>{{ selectedJob.boss_name }} · {{ selectedJob.company_scale || "规模未知" }} · {{ selectedJob.location }}</p>
          <div class="history-detail-tags">
            <el-tag>{{ selectedJob.salary || "薪资未知" }}</el-tag>
            <el-tag type="info">已采集 {{ selectedJob.collect_count }} 次</el-tag>
            <el-tag :type="detailStatusType(selectedJob.detail_status)">{{ detailStatusLabel(selectedJob.detail_status) }}</el-tag>
            <el-tag v-if="selectedJob.is_outsourcing_company" type="warning">外包公司</el-tag>
            <el-tag v-if="selectedJob.is_blacklisted" type="danger">公司黑名单</el-tag>
            <el-tag v-if="selectedJob.application_status" :type="applicationStatusType(selectedJob.application_status)">
              {{ applicationStatusLabel(selectedJob.application_status) }}
            </el-tag>
          </div>
        </div>
        <el-divider />
        <h3>采集记录</h3>
        <p>最近搜索词：{{ selectedJob.search_keyword || "未记录" }}</p>
        <p>首次采集：{{ formatDateTime(selectedJob.first_collected_at) }}</p>
        <p>最后采集：{{ formatDateTime(selectedJob.last_collected_at) }}</p>
        <p v-if="selectedJob.detail_collected_at">详情采集：{{ formatDateTime(selectedJob.detail_collected_at) }}</p>
        <el-divider />
        <h3>岗位筛选</h3>
        <p>
          结论：
          <el-tag :type="filterStatusType(selectedJob.filter_status)" size="small">
            {{ filterStatusLabel(selectedJob.filter_status) }}
          </el-tag>
        </p>
        <p v-if="selectedJob.filter_strategy_id">关联策略：{{ filterStrategyName(selectedJob.filter_strategy_id) }}</p>
        <p>原因：{{ selectedJob.filter_reasons?.join("；") || "暂无筛选原因" }}</p>
        <p v-if="selectedJob.filter_missing_fields?.length">
          缺失信息：{{ selectedJob.filter_missing_fields.join("、") }}
        </p>
        <el-divider />
        <h3>技能与标签</h3>
        <p>{{ selectedJob.skills || selectedJob.job_labels || selectedJob.tags || "暂无标签" }}</p>
        <p>行业：{{ selectedJob.company_industry || "未知" }}；融资阶段：{{ selectedJob.company_stage || "未知" }}</p>
        <p>福利：{{ selectedJob.welfare || "暂无" }}</p>
        <p>招聘者活跃：{{ selectedJob.boss_active_status || "未获取" }}</p>
        <el-divider />
        <h3>职位描述</h3>
        <div v-if="selectedJob.detail?.jd" class="job-description">{{ selectedJob.detail.jd }}</div>
        <p v-else class="secondary-text">该岗位尚未采集完整详情。</p>
        <el-divider />
        <h3>投递建议</h3>
        <template v-if="selectedJob.delivery_evaluation">
          <div class="history-evaluation-summary">
            <el-tag :type="deliveryDecisionType(selectedJob.delivery_evaluation.decision)">
              {{ deliveryDecisionLabel(selectedJob.delivery_evaluation.decision) }}
            </el-tag>
            <span class="secondary-text">
              置信度：{{ Math.round(selectedJob.delivery_evaluation.confidence * 100) }}%
            </span>
          </div>
          <p>{{ selectedJob.delivery_evaluation.summary || deliveryEvaluationReasons(selectedJob) }}</p>
          <p v-if="selectedJob.delivery_evaluation.strengths?.length">
            优势：{{ selectedJob.delivery_evaluation.strengths.join("；") }}
          </p>
          <p v-if="selectedJob.delivery_evaluation.gaps?.length">
            差距：{{ selectedJob.delivery_evaluation.gaps.map((item) => item.item).join("；") }}
          </p>
          <p v-if="deliveryEvaluationRisks(selectedJob)" class="evaluation-warning">
            风险：{{ deliveryEvaluationRisks(selectedJob) }}
          </p>
          <p v-if="deliveryEvaluationMissingFields(selectedJob)" class="secondary-text">
            缺失信息：{{ deliveryEvaluationMissingFields(selectedJob) }}
          </p>
          <template v-if="selectedJob.delivery_evaluation.resume_suggestions?.length">
            <h4>简历优化</h4>
            <ul>
              <li
                v-for="item in selectedJob.delivery_evaluation.resume_suggestions"
                :key="`${item.section}-${item.suggestion}`"
              >
                {{ item.section }}：{{ item.suggestion }}<span v-if="item.basis">（{{ item.basis }}）</span>
              </li>
            </ul>
          </template>
          <template v-if="selectedJob.delivery_evaluation.greeting_draft?.text">
            <h4>招呼语草稿</h4>
            <p>{{ selectedJob.delivery_evaluation.greeting_draft.text }}</p>
          </template>
        </template>
        <p v-else class="secondary-text">尚未获取投递建议。</p>
        <el-button
          v-if="selectedJob.detail_status === 'completed'"
          type="warning"
          :loading="historyStore.deliveryJobId === selectedJob.id"
          :disabled="(historyActionRunning && historyStore.deliveryJobId !== selectedJob.id) || !recommendationStrategyId"
          @click="evaluateHistoryDelivery(selectedJob)"
        >
          {{ selectedJob.delivery_evaluation ? "重新获取投递建议" : "获取投递建议" }}
        </el-button>
        <el-divider />
        <el-button
          type="primary"
          :loading="historyStore.detailJobId === selectedJob.id"
          :disabled="historyActionRunning && historyStore.detailJobId !== selectedJob.id"
          @click="captureHistoryDetails(selectedJob)"
        >
          {{ detailActionLabel(selectedJob) }}
        </el-button>
        <el-divider />
        <el-button
          type="primary"
          :loading="executorStore.openingJobId === selectedJob.id"
          @click="openInDedicatedBrowser(selectedJob)"
        >
          在专用浏览器打开（不打招呼）
        </el-button>
        <el-link v-if="selectedJob.job_link" :href="selectedJob.job_link" target="_blank" type="primary">
          打开 BOSS 原始岗位页面
        </el-link>
      </template>
    </el-drawer>
  </section>
</template>

<style scoped>
.history-filter-panel {
  display: grid;
  gap: 12px;
}

.history-detail-progress {
  display: grid;
  gap: 12px;
}

.history-filter-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
  gap: 4px 16px;
}

.history-filter-actions,
.history-detail-tags {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.history-evaluation-summary {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 8px;
}

.evaluation-warning {
  color: var(--el-color-warning);
}

.history-pagination {
  display: flex;
  justify-content: flex-end;
  padding-top: 18px;
}

.history-detail-heading h2 {
  margin-bottom: 6px;
}

.job-description {
  line-height: 1.8;
  white-space: pre-wrap;
}
</style>
