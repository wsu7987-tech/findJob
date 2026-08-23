<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref } from "vue";
import { ElMessage } from "element-plus";

import { useFineJobBossCaptureStore } from "@/stores/fineJobBossCapture";
import { useFineJobBossExecutorStore } from "@/stores/fineJobBossExecutor";
import { useFineJobPlatformSessionsStore } from "@/stores/fineJobPlatformSessions";
import { useFineJobStrategiesStore } from "@/stores/fineJobStrategies";
import type { FineJobBossCapturedJob } from "@/types";

const captureStore = useFineJobBossCaptureStore();
const executorStore = useFineJobBossExecutorStore();
const platformStore = useFineJobPlatformSessionsStore();
const strategiesStore = useFineJobStrategiesStore();
const form = reactive({
  keyword: "",
  city: "",
  pages: 1,
  includeDetails: false,
  preferCurrentPage: true
});
const aiCommand = ref("");
const filterStrategyId = ref<string | null>(null);
const recommendationStrategyId = ref<string | null>(null);
const selectedJobIds = ref<string[]>([]);
const selectedJobId = ref<string | null>(null);
const detailDrawerOpen = ref(false);
type SortableJobColumn =
  | "is_previously_collected"
  | "filter_status"
  | "title"
  | "company_scale"
  | "salary"
  | "experience"
  | "boss_active_status";
type JobSortOrder = "ascending" | "descending";
type JobSortCriterion = { prop: SortableJobColumn; order: JobSortOrder };
const jobsTable = ref<{
  clearSelection: () => void;
  clearSort: () => void;
  toggleRowSelection: (row: FineJobBossCapturedJob, selected: boolean) => void;
} | null>(null);

const hotCityNames = ["北京", "上海", "广州", "深圳", "杭州", "成都", "武汉", "南京", "苏州"];
const cityOptions = computed(() =>
  [...captureStore.cities].sort((left, right) => {
    const leftHot = hotCityNames.indexOf(left.name);
    const rightHot = hotCityNames.indexOf(right.name);
    if (leftHot >= 0 || rightHot >= 0) {
      if (leftHot < 0) return 1;
      if (rightHot < 0) return -1;
      return leftHot - rightHot;
    }
    return left.name.localeCompare(right.name, "zh-CN");
  })
);

const browserStateLabel = computed(() => {
  if (!captureStore.status?.running) return "未启动";
  if (captureStore.status.is_search_page) return "已定位搜索页";
  return "浏览器已启动";
});
const browserStateType = computed(() => {
  if (!captureStore.status?.running) return "info";
  return captureStore.status.is_search_page ? "success" : "warning";
});
const taskRunning = computed(
  () => captureStore.task?.status === "queued" || captureStore.task?.status === "running"
);
const progressPercentage = computed(() => {
  const task = captureStore.task;
  if (!task?.progress_total) return task?.status === "completed" ? 100 : 0;
  return Math.min(100, Math.round((task.progress_current / task.progress_total) * 100));
});
const preCaptureEstimate = computed(() => {
  if (!form.includeDetails) return "";
  const expectedJobs = form.pages * 30;
  return `${formatDuration(expectedJobs * 25)}～${formatDuration(expectedJobs * 55)}`;
});
const remainingEstimate = computed(() => {
  const task = captureStore.task;
  if (!task || task.estimated_seconds_max <= 0) return "即将完成";
  return `${formatDuration(task.estimated_seconds_min)}～${formatDuration(task.estimated_seconds_max)}`;
});
const currentDetailJob = computed(() =>
  captureStore.task?.jobs.find((job) => job.job_id === selectedJobId.value) ?? null
);
const filterStatusRank = (status?: FineJobBossCapturedJob["filter_status"]) => {
  if (status === "pass") return 0;
  if (status === "review") return 1;
  if (status === "reject") return 2;
  return 3;
};
const firstNumber = (value?: string | null) => {
  const match = value?.match(/\d+(?:\.\d+)?/);
  return match ? Number(match[0]) : Number.POSITIVE_INFINITY;
};
const sortByCollection = (left: FineJobBossCapturedJob, right: FineJobBossCapturedJob) =>
  Number(Boolean(left.is_previously_collected)) - Number(Boolean(right.is_previously_collected));
const sortByFilterStatus = (left: FineJobBossCapturedJob, right: FineJobBossCapturedJob) =>
  filterStatusRank(left.filter_status) - filterStatusRank(right.filter_status);
const sortByTitle = (left: FineJobBossCapturedJob, right: FineJobBossCapturedJob) =>
  String(left.title ?? "").localeCompare(String(right.title ?? ""), "zh-CN", { numeric: true });
const sortByCompanyScale = (left: FineJobBossCapturedJob, right: FineJobBossCapturedJob) =>
  firstNumber(left.company_scale) - firstNumber(right.company_scale);
const sortBySalary = (left: FineJobBossCapturedJob, right: FineJobBossCapturedJob) =>
  firstNumber(left.salary) - firstNumber(right.salary);
const experienceOrder = ["经验不限", "在校/应届", "1年以内", "1-3年", "3-5年", "5-10年", "10年以上"];
const activeStatusOrder = ["在线", "刚刚活跃", "今日活跃", "3日内活跃", "本周活跃", "本月活跃"];
const orderedLabelRank = (value: string | undefined, order: string[]) => {
  const index = order.findIndex((item) => value?.includes(item));
  return index >= 0 ? index : order.length;
};
const sortByExperience = (left: FineJobBossCapturedJob, right: FineJobBossCapturedJob) =>
  orderedLabelRank(left.experience, experienceOrder) - orderedLabelRank(right.experience, experienceOrder);
const sortByBossActiveStatus = (left: FineJobBossCapturedJob, right: FineJobBossCapturedJob) =>
  orderedLabelRank(left.boss_active_status, activeStatusOrder) - orderedLabelRank(right.boss_active_status, activeStatusOrder);
const sortComparators: Record<SortableJobColumn, (left: FineJobBossCapturedJob, right: FineJobBossCapturedJob) => number> = {
  is_previously_collected: sortByCollection,
  filter_status: sortByFilterStatus,
  title: sortByTitle,
  company_scale: sortByCompanyScale,
  salary: sortBySalary,
  experience: sortByExperience,
  boss_active_status: sortByBossActiveStatus
};
const defaultJobSort = (): JobSortCriterion[] => [{ prop: "filter_status", order: "ascending" }];
const jobSortCriteria = ref<JobSortCriterion[]>(defaultJobSort());
const sortedJobs = computed(() => {
  const criteria = jobSortCriteria.value.length ? jobSortCriteria.value : defaultJobSort();
  return [...(captureStore.task?.jobs ?? [])].sort((left, right) => {
    for (const criterion of criteria) {
      const result = sortComparators[criterion.prop](left, right);
      if (result !== 0) return criterion.order === "ascending" ? result : -result;
    }
    return 0;
  });
});
const handleJobSortChange = ({ prop, order }: { prop: string; order: JobSortOrder | null }) => {
  if (!(prop in sortComparators)) return;
  const column = prop as SortableJobColumn;
  const remaining = jobSortCriteria.value.filter((criterion) => criterion.prop !== column);
  jobSortCriteria.value = order
    ? [{ prop: column, order }, ...remaining]
    : remaining.length ? remaining : defaultJobSort();
};
const resetJobSort = () => {
  jobSortCriteria.value = defaultJobSort();
  jobsTable.value?.clearSort();
};
const detailStatusSummary = computed(() => {
  const jobs = captureStore.task?.jobs ?? [];
  return {
    selected: selectedJobIds.value.length,
    recommended: jobs.filter((job) => job.recommended).length,
    passed: jobs.filter((job) => job.filter_status === "pass").length,
    rejected: jobs.filter((job) => job.filter_status === "reject").length,
    review: jobs.filter((job) => job.filter_status === "review").length,
    completed: jobs.filter((job) => job.detail_status === "completed").length,
    failed: jobs.filter((job) => job.detail_status === "failed").length
  };
});
const selectedJobs = computed(() =>
  (captureStore.task?.jobs ?? []).filter((job) => selectedJobIds.value.includes(job.job_id || ""))
);
const selectedDetailJobIds = computed(() =>
  selectedJobs.value
    .filter((job) => job.job_id && job.detail_status !== "completed" && job.detail_status !== "collecting")
    .map((job) => job.job_id as string)
);
const selectedDeliveryJobIds = computed(() =>
  selectedJobs.value
    .filter((job) => job.job_id && job.detail_status === "completed" && !job.delivery_evaluation)
    .map((job) => job.job_id as string)
);

onMounted(async () => {
  await Promise.all([
    captureStore.loadStatus(),
    captureStore.loadCities(),
    strategiesStore.load(),
    platformStore.load()
  ]);
  const initialFilter = strategiesStore.filters.find((item) => item.enabled) ?? strategiesStore.filters[0];
  const initialRecommendation = strategiesStore.recommendations.find((item) => item.enabled) ?? strategiesStore.recommendations[0];
  filterStrategyId.value = initialFilter?.id ?? null;
  recommendationStrategyId.value = initialRecommendation?.id ?? null;
  form.keyword = initialFilter?.search_keywords[0] || initialFilter?.title_include_any[0] || "";
  form.city = initialFilter?.cities[0] || "";
  captureStore.resumePolling();
});

onBeforeUnmount(() => captureStore.stopPolling());

const ensureSearchInput = () => {
  if (!form.keyword.trim() || !form.city.trim()) {
    ElMessage.warning("请先填写搜索关键词和城市");
    return false;
  }
  return true;
};

const startBrowser = async () => {
  try {
    await platformStore.openBossLoginWindow();
    await captureStore.loadStatus();
    ElMessage.success("FineJob 专用 Chrome 已打开，请在浏览器中完成 BOSS 登录");
  } catch {
    ElMessage.error(platformStore.error ?? "打开 BOSS 浏览器失败");
  }
};

const checkLogin = async () => {
  try {
    const response = await platformStore.checkBossLoginStatus();
    response.session.status === "ready"
      ? ElMessage.success("BOSS 登录状态可用")
      : ElMessage.warning(response.detail || "尚未检测到有效登录状态");
  } catch {
    ElMessage.error(platformStore.error ?? "检测 BOSS 登录状态失败");
  }
};

const stopBrowser = async () => {
  try {
    await captureStore.stopBrowser();
    ElMessage.success("FineJob 专用 Chrome 已关闭，登录 profile 已保留");
  } catch {
    ElMessage.error(captureStore.error ?? "关闭 BOSS 浏览器失败");
  }
};

const locateSearchPage = async () => {
  if (!ensureSearchInput()) return;
  try {
    await captureStore.locate({ keyword: form.keyword.trim(), city: form.city.trim() });
    ElMessage.success("已定位到 BOSS 搜索页，可在浏览器中继续调整筛选条件");
  } catch {
    ElMessage.error(captureStore.error ?? "定位 BOSS 搜索页失败");
  }
};

const captureJobs = async () => {
  if (!ensureSearchInput()) return;
  selectedJobIds.value = [];
  try {
    await captureStore.capture({
      keyword: form.keyword.trim(),
      city: form.city.trim(),
      pages: form.pages,
      include_details: form.includeDetails,
      prefer_current_page: form.preferCurrentPage
    });
    ElMessage.success("采集任务已启动，可在本页查看实时进度");
  } catch {
    ElMessage.error(captureStore.error ?? "BOSS 岗位采集失败");
  }
};

const handleSelectionChange = (rows: FineJobBossCapturedJob[]) => {
  selectedJobIds.value = rows.map((row) => row.job_id || "").filter(Boolean);
};

const applySuggestedSelection = async (mode: "strategy" | "ai") => {
  if (mode === "strategy" && !filterStrategyId.value) {
    ElMessage.warning("请先选择岗位筛选策略");
    return;
  }
  if (mode === "ai" && !recommendationStrategyId.value) {
    ElMessage.warning("请先选择岗位建议投递策略");
    return;
  }
  try {
    const ids = mode === "strategy"
      ? await captureStore.applyFilter(filterStrategyId.value!)
      : await captureStore.suggest("ai", aiCommand.value, {
          filterStrategyId: filterStrategyId.value,
          recommendationStrategyId: recommendationStrategyId.value
        });
    selectedJobIds.value = ids;
    await nextTick();
    // 应用筛选后恢复默认状态顺序，避免沿用用户之前的标题排序。
    resetJobSort();
    jobsTable.value?.clearSelection();
    for (const job of captureStore.task?.jobs ?? []) {
      if (job.job_id && ids.includes(job.job_id)) {
        jobsTable.value?.toggleRowSelection(job, true);
      }
    }
    ElMessage.success(`${mode === "ai" ? "AI 初筛" : "筛选策略"}选择 ${ids.length} 个岗位`);
  } catch {
    ElMessage.error(captureStore.error ?? "生成详情采集建议失败");
  }
};

const evaluateDeliveries = async (jobIds: string[]) => {
  if (!recommendationStrategyId.value) {
    ElMessage.warning("请先选择岗位建议投递策略");
    return;
  }
  if (!jobIds.length) {
    ElMessage.warning("请先选择尚未获取投递建议的已完成详情岗位");
    return;
  }
  try {
    const evaluations = await captureStore.evaluateDeliveries(
      recommendationStrategyId.value,
      filterStrategyId.value,
      aiCommand.value,
      jobIds
    );
    const recommended = evaluations.filter((item) => item.decision === "recommend").length;
    const review = evaluations.filter((item) => item.decision === "review").length;
    ElMessage.success(`投递评估完成：建议 ${recommended}，待判断 ${review}`);
  } catch {
    ElMessage.error(captureStore.error ?? "生成投递建议失败");
  }
};

const evaluateSingleDelivery = async (job: FineJobBossCapturedJob) => {
  if (job.detail_status !== "completed" || !job.job_id) {
    ElMessage.warning("请先完成该岗位详情采集");
    return;
  }
  await evaluateDeliveries([job.job_id]);
};

const captureSelectedDetails = async () => {
  if (!selectedDetailJobIds.value.length) {
    ElMessage.warning("请先选择需要采集详情的岗位");
    return;
  }
  try {
    await captureStore.captureDetails(selectedDetailJobIds.value);
    ElMessage.success(`已开始采集选中的 ${selectedDetailJobIds.value.length} 个岗位详情`);
  } catch {
    ElMessage.error(captureStore.error ?? "启动岗位详情采集失败");
  }
};

const captureSingleDetail = async (job: FineJobBossCapturedJob) => {
  const jobId = job.job_id;
  if (!jobId) return;
  const force = job.detail_status === "completed" || Boolean(job.detail);
  try {
    await captureStore.captureDetails([jobId], force);
    ElMessage.success(`${detailActionLabel(job)}任务已启动`);
  } catch {
    ElMessage.error(captureStore.error ?? "启动岗位详情采集失败");
  }
};

const detailActionLabel = (job: FineJobBossCapturedJob) =>
  job.detail_status === "completed" || Boolean(job.detail) ? "重新采集详情" : "采集详情";
const deliveryDetailActionLabel = (job: FineJobBossCapturedJob) =>
  job.delivery_evaluation ? "重新获取投递建议" : "获取投递建议";

const openDetail = (job: FineJobBossCapturedJob) => {
  selectedJobId.value = job.job_id || null;
  detailDrawerOpen.value = true;
};

const openInDedicatedBrowser = async (job: FineJobBossCapturedJob) => {
  if (!job.job_id) return;
  try {
    await executorStore.openJob(job.job_id, "capture");
    ElMessage.success("已在FineJob专用浏览器打开该岗位；未执行打招呼");
  } catch {
    ElMessage.error(executorStore.error ?? "打开岗位页面失败");
  }
};

const canSelectJob = (job: FineJobBossCapturedJob) =>
  job.detail_status !== "queued" && job.detail_status !== "collecting";

const detailStatusLabel = (status?: string) =>
  ({
    not_collected: "未采集",
    queued: "等待采集",
    collecting: "正在采集",
    completed: "已完成",
    failed: "采集失败"
  })[status || "not_collected"] || "未采集";

const detailStatusType = (status?: string) => {
  if (status === "completed") return "success";
  if (status === "failed") return "danger";
  if (status === "collecting" || status === "queued") return "warning";
  return "info";
};

const filterStatusLabel = (status?: string) => ({ pass: "通过", reject: "排除", review: "待判断" }[status || ""] || "未筛选");
const filterStatusType = (status?: string) => status === "pass" ? "success" : status === "reject" ? "danger" : status === "review" ? "warning" : "info";
const deliveryDecisionLabel = (decision?: string) => ({ recommend: "建议投递", reject: "不建议", review: "待判断" }[decision || ""] || "未评估");
const deliveryDecisionType = (decision?: string) => decision === "recommend" ? "success" : decision === "reject" ? "danger" : decision === "review" ? "warning" : "info";
const deliveryEvaluationReasons = (job: FineJobBossCapturedJob) =>
  (job.delivery_evaluation?.reasons ?? []).join("；") || job.recommendation_reason || "暂无评估理由";
const deliveryEvaluationRisks = (job: FineJobBossCapturedJob) =>
  (job.delivery_evaluation?.risks ?? []).join("；");
const deliveryEvaluationMissingFields = (job: FineJobBossCapturedJob) =>
  (job.delivery_evaluation?.missing_fields ?? []).join("、");
const filterReasonText = (job: FineJobBossCapturedJob) => [
  ...(job.filter_reasons ?? []),
  ...(job.filter_missing_fields ?? []).map((item) => `缺少：${item}`)
].join("；") || "尚未应用筛选策略";

function formatDuration(seconds: number) {
  if (seconds < 60) return `${Math.max(1, Math.ceil(seconds))} 秒`;
  const minutes = Math.ceil(seconds / 60);
  if (minutes < 60) return `${minutes} 分钟`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest ? `${hours} 小时 ${rest} 分钟` : `${hours} 小时`;
}
</script>

<template>
  <section class="page-stack fine-job-page">
    <div class="page-heading">
      <div>
        <p class="panel-eyebrow">BOSS Capture</p>
        <h1>岗位采集</h1>
        <p class="secondary-text">
          可以一次采集列表和全部详情，也可以先获得列表，再手工或按建议选择详情。
        </p>
      </div>
      <div class="card-actions">
        <el-tag :type="browserStateType">{{ browserStateLabel }}</el-tag>
        <el-button :loading="captureStore.loadingStatus" @click="captureStore.loadStatus()">
          刷新状态
        </el-button>
      </div>
    </div>

    <el-alert
      v-if="captureStore.error || platformStore.error"
      type="error"
      title="BOSS 岗位采集操作失败"
      :description="captureStore.error || platformStore.error || ''"
      show-icon
    />

    <section class="page-panel capture-browser-panel">
      <div class="panel-title-row">
        <div><p class="panel-eyebrow">Browser</p><h2>专用浏览器</h2></div>
        <span class="secondary-text">CDP 端口：{{ captureStore.status?.cdp_port ?? 9222 }}</span>
      </div>
      <div class="platform-actions">
        <el-button type="primary" :loading="platformStore.openingLogin" @click="startBrowser">
          打开 BOSS 浏览器
        </el-button>
        <el-button type="success" plain :disabled="!captureStore.status?.running" :loading="platformStore.checking" @click="checkLogin">
          检测登录状态
        </el-button>
        <el-button :disabled="!captureStore.status?.running || taskRunning" :loading="captureStore.locating" @click="locateSearchPage">
          定位到搜索页
        </el-button>
        <el-button type="danger" plain :disabled="!captureStore.status?.running || taskRunning" :loading="captureStore.stopping" @click="stopBrowser">
          关闭专用浏览器
        </el-button>
      </div>
      <div class="capture-current-page">
        <span>登录状态：{{ platformStore.bossReady ? "已登录" : "待检测" }}</span>
        <span>当前页面</span>
        <code>{{ captureStore.status?.current_url || "尚未识别 FineJob 管理的页面" }}</code>
      </div>
    </section>

    <section class="page-panel">
      <div class="panel-title-row">
        <div><p class="panel-eyebrow">Search</p><h2>采集条件</h2></div>
        <span class="secondary-text">未定位时会根据这里的条件自动打开搜索页。</span>
      </div>
      <el-form label-position="top" class="intent-form">
        <div class="form-grid">
          <el-form-item label="搜索关键词">
            <el-input v-model="form.keyword" placeholder="例如：Python" />
          </el-form-item>
          <el-form-item label="城市">
            <el-select v-model="form.city" filterable :loading="captureStore.loadingCities" placeholder="搜索并选择城市">
              <el-option v-for="city in cityOptions" :key="city.code" :label="city.name" :value="city.name" />
            </el-select>
          </el-form-item>
          <el-form-item label="采集页数">
            <el-input-number v-model="form.pages" :min="1" :max="10" />
          </el-form-item>
        </div>
        <div class="capture-options">
          <el-switch v-model="form.preferCurrentPage" />
          <span>优先采集当前 BOSS 搜索页；当前页无效时自动定位</span>
        </div>
        <div class="capture-options">
          <el-switch v-model="form.includeDetails" />
          <span>采集列表后，自动获取全部岗位详情（不建议）</span>
        </div>
        <el-alert
          v-if="form.includeDetails"
          type="warning"
          :title="`详情会逐个打开采集，预计约 ${preCaptureEstimate}`"
          description="该时间按每页约 30 个岗位估算；列表完成后会按实际数量重新计算。遇到登录失效、安全验证或风控提示时任务会停止并显示原因。"
          :closable="false"
          show-icon
        />
      </el-form>
      <div class="platform-actions capture-submit">
        <el-button type="primary" size="large" :disabled="!captureStore.status?.running || taskRunning" :loading="captureStore.capturing" @click="captureJobs">
          开始采集
        </el-button>
      </div>
    </section>

    <section v-if="captureStore.task && captureStore.task.status !== 'completed'" class="page-panel capture-progress-panel">
      <div class="panel-title-row">
        <div><p class="panel-eyebrow">Progress</p><h2>采集进度</h2></div>
        <el-tag :type="captureStore.task.status === 'failed' ? 'danger' : 'warning'">
          {{ captureStore.task.status === "failed" ? "失败" : "进行中" }}
        </el-tag>
      </div>
      <el-progress :percentage="progressPercentage" :status="captureStore.task.status === 'failed' ? 'exception' : undefined" />
      <p>{{ captureStore.task.message }}</p>
      <div class="capture-metrics">
        <span>岗位 {{ captureStore.task.jobs_collected }}</span>
        <span>详情完成 {{ captureStore.task.details_completed }}</span>
        <span>详情失败 {{ captureStore.task.details_failed }}</span>
        <span>预计剩余 {{ remainingEstimate }}</span>
      </div>
      <p v-if="captureStore.task.current_job" class="secondary-text">
        当前岗位：{{ captureStore.task.current_job.title }} / {{ captureStore.task.current_job.company }}
      </p>
    </section>

    <section v-if="captureStore.task?.jobs.length" class="page-panel">
      <div class="panel-title-row">
        <div><p class="panel-eyebrow">Jobs</p><h2>岗位列表</h2></div>
        <div class="capture-metrics">
          <span>本次 {{ captureStore.task.jobs.length }}</span>
          <span>新岗位 {{ captureStore.task.jobs.length - captureStore.task.duplicate_jobs_count }}</span>
          <span>历史岗位 {{ captureStore.task.duplicate_jobs_count }}</span>
          <span>已选择 {{ detailStatusSummary.selected }}</span>
          <span>筛选通过 {{ detailStatusSummary.passed }}</span>
          <span>待判断 {{ detailStatusSummary.review }}</span>
          <span>已排除 {{ detailStatusSummary.rejected }}</span>
          <span>建议投递 {{ detailStatusSummary.recommended }}</span>
          <span>详情完成 {{ detailStatusSummary.completed }}</span>
          <span>失败 {{ detailStatusSummary.failed }}</span>
        </div>
      </div>

      <div class="detail-actions">
        <el-select v-model="filterStrategyId" clearable placeholder="选择岗位筛选策略">
          <el-option v-for="item in strategiesStore.filters" :key="item.id" :label="item.name" :value="item.id" />
        </el-select>
        <el-button :disabled="taskRunning" :loading="captureStore.suggesting" @click="applySuggestedSelection('strategy')">
          应用筛选策略
        </el-button>
        <el-select v-model="recommendationStrategyId" clearable placeholder="选择建议投递策略">
          <el-option v-for="item in strategiesStore.recommendations" :key="item.id" :label="item.name" :value="item.id" />
        </el-select>
        <el-input v-model="aiCommand" :disabled="taskRunning" placeholder="本次额外要求（可选）" clearable />
        <el-button type="primary" plain :disabled="taskRunning" :loading="captureStore.suggesting" @click="applySuggestedSelection('ai')">
          AI 初筛详情岗位
        </el-button>
        <el-button type="success" :disabled="taskRunning || !selectedDetailJobIds.length" @click="captureSelectedDetails">
          采集选中的 {{ selectedDetailJobIds.length }} 个岗位详情
        </el-button>
        <el-button
          type="warning"
          :disabled="taskRunning || !recommendationStrategyId || !selectedDeliveryJobIds.length"
          :loading="captureStore.suggesting"
          @click="evaluateDeliveries(selectedDeliveryJobIds)"
        >
          获取未评估岗位投递建议（{{ selectedDeliveryJobIds.length }}）
        </el-button>
      </div>

      <el-table
        ref="jobsTable"
        :data="sortedJobs"
        row-key="job_id"
        max-height="560"
        empty-text="本次没有采集到岗位"
        @sort-change="handleJobSortChange"
        @selection-change="handleSelectionChange"
        @row-click="openDetail"
      >
        <el-table-column type="selection" width="48" reserve-selection :selectable="canSelectJob" />
        <el-table-column prop="is_previously_collected" label="采集" width="90" sortable="custom">
          <template #default="scope">
            <el-tag :type="scope.row.is_previously_collected ? 'warning' : 'success'" size="small">
              {{ scope.row.is_previously_collected ? "历史岗位" : "新岗位" }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="filter_status" label="筛选" min-width="100" sortable="custom">
          <template #default="scope">
            <el-tooltip :content="filterReasonText(scope.row)">
              <el-tag :type="filterStatusType(scope.row.filter_status)" size="small">{{ filterStatusLabel(scope.row.filter_status) }}</el-tag>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column label="投递建议" min-width="100">
          <template #default="scope">
            <el-tooltip :content="scope.row.recommendation_reason || '尚未生成投递建议'">
              <el-tag :type="deliveryDecisionType(scope.row.delivery_evaluation?.decision)" size="small">
                {{ deliveryDecisionLabel(scope.row.delivery_evaluation?.decision) }}
              </el-tag>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column prop="title" label="岗位" min-width="180" sortable="custom" />
        <el-table-column prop="boss_name" label="公司" min-width="150" />
        <el-table-column prop="salary" label="薪资" width="110" sortable="custom" />
        <el-table-column prop="company_scale" label="公司规模" width="120" sortable="custom" />
        <el-table-column prop="company_industry" label="行业" width="120" show-overflow-tooltip />
        <el-table-column prop="location" label="地点" min-width="140" />
        <el-table-column prop="experience" label="经验" width="100" sortable="custom" />
        <el-table-column prop="degree" label="学历" width="90" />
        <el-table-column prop="boss_active_status" label="招聘者活跃" width="120" sortable="custom">
          <template #default="scope">
            <span :class="{ 'secondary-text': !scope.row.boss_active_status }">{{ scope.row.boss_active_status || "未获取" }}</span>
          </template>
        </el-table-column>

        <el-table-column label="详情" width="110">
          <template #default="scope">
            <el-tag :type="detailStatusType(scope.row.detail_status)" size="small">
              {{ detailStatusLabel(scope.row.detail_status) }}
            </el-tag>
          </template>
        </el-table-column>

        <el-table-column label="操作" width="285" fixed="right">
          <template #default="scope">
            <el-button link type="primary" @click.stop="openDetail(scope.row)">查看详情</el-button>
            <el-button
              link
              type="primary"
              :loading="executorStore.openingJobId === scope.row.job_id"
              @click.stop="openInDedicatedBrowser(scope.row)"
            >
              专用浏览器打开
            </el-button>
            <el-button
              v-if="scope.row.detail_status === 'completed' && !scope.row.delivery_evaluation"
              link
              type="warning"
              :disabled="taskRunning || !scope.row.job_id || !recommendationStrategyId"
              :loading="captureStore.suggesting"
              @click.stop="evaluateSingleDelivery(scope.row)"
            >
              获取投递详情
            </el-button>
            <el-button
              v-else-if="scope.row.detail_status !== 'completed'"
              link
              type="success"
              :disabled="taskRunning || !scope.row.job_id"
              @click.stop="captureSingleDetail(scope.row)"
            >
              {{ detailActionLabel(scope.row) }}
            </el-button>
          </template>
        </el-table-column>
      </el-table>

      <div v-if="captureStore.task.jobs_path" class="capture-output-path">
        <span>列表文件</span><code>{{ captureStore.task.jobs_path }}</code>
        <template v-if="captureStore.task.details_path">
          <span>详情文件</span><code>{{ captureStore.task.details_path }}</code>
        </template>
      </div>
    </section>

    <el-drawer v-model="detailDrawerOpen" size="48%" :title="currentDetailJob?.title || '岗位详情'">
      <template v-if="currentDetailJob">
        <div class="job-detail-heading">
          <h2>{{ currentDetailJob.title }}</h2>
          <p>{{ currentDetailJob.boss_name }} · {{ currentDetailJob.location }}</p>
          <div class="detail-tags">
            <el-tag>{{ currentDetailJob.salary || "薪资未知" }}</el-tag>
            <el-tag v-if="currentDetailJob.experience" type="info">{{ currentDetailJob.experience }}</el-tag>
            <el-tag v-if="currentDetailJob.degree" type="info">{{ currentDetailJob.degree }}</el-tag>
            <el-tag v-if="currentDetailJob.company_industry" type="info">{{ currentDetailJob.company_industry }}</el-tag>
            <el-tag :type="currentDetailJob.boss_active_status ? 'success' : 'info'">招聘者：{{ currentDetailJob.boss_active_status || "未获取" }}</el-tag>
            <el-tag :type="detailStatusType(currentDetailJob.detail_status)">{{ detailStatusLabel(currentDetailJob.detail_status) }}</el-tag>
            <el-tag v-if="currentDetailJob.is_previously_collected" type="warning">历史岗位</el-tag>
          </div>
        </div>

        <el-divider />
        <h3>推荐信息</h3>
        <template v-if="currentDetailJob.delivery_evaluation">
          <div class="detail-evaluation-summary">
            <el-tag :type="deliveryDecisionType(currentDetailJob.delivery_evaluation.decision)">
              {{ deliveryDecisionLabel(currentDetailJob.delivery_evaluation.decision) }}
            </el-tag>
            <span class="secondary-text">
              置信度：{{ Math.round(currentDetailJob.delivery_evaluation.confidence * 100) }}%
            </span>
          </div>
          <p>{{ currentDetailJob.delivery_evaluation.summary || deliveryEvaluationReasons(currentDetailJob) }}</p>
          <p v-if="currentDetailJob.delivery_evaluation.strengths?.length">
            优势：{{ currentDetailJob.delivery_evaluation.strengths.join("；") }}
          </p>
          <p v-if="currentDetailJob.delivery_evaluation.gaps?.length">
            差距：{{ currentDetailJob.delivery_evaluation.gaps.map((item) => item.item).join("；") }}
          </p>
          <p v-if="deliveryEvaluationRisks(currentDetailJob)" class="evaluation-warning">
            风险：{{ deliveryEvaluationRisks(currentDetailJob) }}
          </p>
          <p v-if="deliveryEvaluationMissingFields(currentDetailJob)" class="secondary-text">
            缺失信息：{{ deliveryEvaluationMissingFields(currentDetailJob) }}
          </p>
          <template v-if="currentDetailJob.delivery_evaluation.resume_suggestions?.length">
            <h4>简历优化</h4>
            <ul>
              <li
                v-for="item in currentDetailJob.delivery_evaluation.resume_suggestions"
                :key="`${item.section}-${item.suggestion}`"
              >
                {{ item.section }}：{{ item.suggestion }}<span v-if="item.basis">（{{ item.basis }}）</span>
              </li>
            </ul>
          </template>
          <template v-if="currentDetailJob.delivery_evaluation.greeting_draft?.text">
            <h4>招呼语草稿</h4>
            <p>{{ currentDetailJob.delivery_evaluation.greeting_draft.text }}</p>
          </template>
        </template>
        <p v-else-if="currentDetailJob.recommended">{{ currentDetailJob.recommendation_reason }}</p>
        <p v-else class="secondary-text">当前没有策略或 AI 推荐记录。</p>

        <el-divider />
        <h3>技能与标签</h3>
        <p>{{ currentDetailJob.skills || currentDetailJob.job_labels || currentDetailJob.tags || "暂无标签" }}</p>

        <el-divider />
        <h3>职位描述</h3>
        <p v-if="currentDetailJob.detail_status === 'collecting'" class="secondary-text">正在采集该岗位详情……</p>
        <el-alert v-else-if="currentDetailJob.detail_status === 'failed'" type="error" :title="currentDetailJob.detail_error || '详情采集失败'" show-icon />
        <div v-else-if="currentDetailJob.detail?.jd" class="job-description">{{ currentDetailJob.detail.jd }}</div>
        <p v-else class="secondary-text">尚未获取完整职位描述，可选择该岗位后采集详情。</p>
        <el-button
          v-if="currentDetailJob.detail_status !== 'queued' && currentDetailJob.detail_status !== 'collecting'"
          type="primary"
          :disabled="taskRunning"
          @click="captureSingleDetail(currentDetailJob)"
        >
          {{ detailActionLabel(currentDetailJob) }}
        </el-button>
        <el-button
          v-if="currentDetailJob.detail_status === 'completed'"
          type="warning"
          :disabled="taskRunning || !recommendationStrategyId"
          :loading="captureStore.suggesting"
          @click="evaluateSingleDelivery(currentDetailJob)"
        >
          {{ deliveryDetailActionLabel(currentDetailJob) }}
        </el-button>

        <el-divider />
        <h3>来源</h3>
        <p class="secondary-text">列表采集：{{ currentDetailJob.list_collected_at || "未知" }}</p>
        <p v-if="currentDetailJob.first_collected_at" class="secondary-text">首次采集：{{ currentDetailJob.first_collected_at }}</p>
        <p v-if="currentDetailJob.collect_count" class="secondary-text">累计采集：{{ currentDetailJob.collect_count }} 次</p>
        <p v-if="currentDetailJob.detail_collected_at" class="secondary-text">详情采集：{{ currentDetailJob.detail_collected_at }}</p>
        <el-button
          type="primary"
          :loading="executorStore.openingJobId === currentDetailJob.job_id"
          @click="openInDedicatedBrowser(currentDetailJob)"
        >
          在专用浏览器打开（不打招呼）
        </el-button>
        <el-link v-if="currentDetailJob.job_link" :href="currentDetailJob.job_link" target="_blank" type="primary">
          打开 BOSS 原始岗位页面
        </el-link>
      </template>
    </el-drawer>
  </section>
</template>

<style scoped>
.capture-browser-panel,
.capture-progress-panel,
.capture-submit,
.session-summary {
  display: grid;
  gap: 16px;
}

.capture-current-page,
.capture-output-path {
  display: grid;
  gap: 6px;
  color: var(--el-text-color-secondary);
}

.capture-current-page code,
.capture-output-path code {
  overflow-wrap: anywhere;
  color: var(--el-text-color-regular);
}

.capture-options,
.capture-metrics,
.detail-actions,
.detail-tags {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.capture-options {
  margin: 12px 0;
}

.detail-actions {
  margin-bottom: 16px;
}

.detail-actions .el-input {
  min-width: 260px;
  flex: 1;
}

.job-detail-heading h2 {
  margin-bottom: 6px;
}

.detail-evaluation-summary {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 8px;
}

.evaluation-warning {
  color: var(--el-color-warning);
}

.job-description {
  line-height: 1.8;
  white-space: pre-wrap;
}
</style>
