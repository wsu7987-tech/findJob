<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { useRouter } from "vue-router";

import { useFineJobBossCaptureStore } from "@/stores/fineJobBossCapture";
import { useFineJobBossExecutorStore } from "@/stores/fineJobBossExecutor";
import { useFineJobCodexStore } from "@/stores/fineJobCodex";
import { useFineJobPlatformSessionsStore } from "@/stores/fineJobPlatformSessions";
import { useFineJobStrategiesStore } from "@/stores/fineJobStrategies";
import { useFineJobWorkflowRunStore } from "@/stores/fineJobWorkflowRun";
import {
  resubmitWorkflowCodexSubmit,
  retryWorkflowCodexHandoff,
  triggerWorkflowCodexHandoff
} from "@/services/workflowCodexHandoff";
import type {
  FineJobBossCapturedJob,
  FineJobBossCaptureTask,
  FineJobSmartCapture,
  FineJobWorkflowAnalysisItem,
  FineJobWorkflowContextSnapshot
} from "@/types";
import { ApiError, api } from "@/services/api";

const captureStore = useFineJobBossCaptureStore();
const executorStore = useFineJobBossExecutorStore();
const codexStore = useFineJobCodexStore();
const platformStore = useFineJobPlatformSessionsStore();
const strategiesStore = useFineJobStrategiesStore();
const workflowStore = useFineJobWorkflowRunStore();
const router = useRouter();
const form = reactive({
  keyword: "",
  city: "",
  pages: 1,
  includeDetails: false,
  preferCurrentPage: true
});
const moreFiltersOpen = ref(false);
const bossFilters = reactive({
  jobType: "",
  salary: "",
  payType: [] as string[],
  partTime: [] as string[],
  experience: [] as string[],
  degree: [] as string[],
  scale: [] as string[],
  stage: [] as string[]
});
const fullTimeSalaryOptions = [
  { label: "3k以下", value: "402" },
  { label: "3-5k", value: "403" },
  { label: "5-10k", value: "404" },
  { label: "10-20k", value: "405" },
  { label: "20-50k", value: "406" },
  // BOSS 对这两个薪资文案使用同一个查询值，保留不同选项以便用户按页面文案选择。
  { label: "50k以上", value: "406-plus" }
];
const payTypeOptions = [
  { label: "日结", value: "2501" },
  { label: "周结", value: "2502" },
  { label: "月结", value: "2503" },
  { label: "完工结", value: "2504" }
];
const partTimeOptions = [
  { label: "周末/节假日", value: "2701" },
  { label: "寒暑假", value: "2702" },
  { label: "短期兼职", value: "2703" },
  { label: "长期兼职", value: "2704" },
  { label: "工作日", value: "2705" },
  { label: "夜班", value: "2706" }
];
const experienceOptions = [
  { label: "在校生", value: "108" },
  { label: "应届生", value: "102" },
  { label: "经验不限", value: "101" },
  { label: "一年以内", value: "103" },
  { label: "1-3年", value: "104" },
  { label: "3-5年", value: "105" },
  { label: "5-10年", value: "106" },
  { label: "10年以上", value: "107" }
];
const degreeOptions = [
  { label: "初中及以下", value: "209" },
  { label: "中专/中技", value: "208" },
  { label: "高中", value: "206" },
  { label: "大专", value: "202" },
  { label: "本科", value: "203" },
  { label: "硕士", value: "204" },
  { label: "博士", value: "205" }
];
const scaleOptions = [
  { label: "0-20人", value: "301" },
  { label: "20-99人", value: "302" },
  { label: "100-499人", value: "303" },
  { label: "500-999人", value: "304" },
  { label: "1000-9999人", value: "305" },
  { label: "10000人以上", value: "306" }
];
const stageOptions = [
  { label: "未融资", value: "801" },
  { label: "天使轮", value: "802" },
  { label: "A轮", value: "803" },
  { label: "B轮", value: "804" },
  { label: "C轮", value: "805" },
  { label: "D轮及以上", value: "806" },
  { label: "已上市", value: "807" },
  { label: "不需要融资", value: "808" }
];
const strategyJobTypeCodes: Record<string, string> = {
  full_time: "1901",
  part_time: "1903"
};
const optionValues = (labels: string[], options: { label: string; value: string }[]) =>
  labels.flatMap((label) => options.filter((option) => option.label === label).map((option) => option.value));
const aiCommand = ref("");
const filterStrategyId = ref<string | null>(null);
const recommendationStrategyId = ref<string | null>(null);
type CaptureConditionTab = "smart" | "custom";
// 岗位采集页默认进入智能采集；恢复中的智能任务也在同一 Tab 展示。
const activeCaptureConditionTab = ref<CaptureConditionTab>("smart");
const smartFilterStrategyId = ref<string | null>(null);
const smartSelectedKeywords = ref<string[]>([]);
const smartSelectedCities = ref<string[]>([]);
const smartCandidateTargetCount = ref(15);
const smartDeliveryTargetEnabled = ref(false);
const smartRecommendationStrategyId = ref<string | null>(null);
const smartCodexModel = ref("");
const smartCodexReasoningEffort = ref<"minimal" | "low" | "medium" | "high" | "xhigh">("medium");
const smartCodexModels = ref<Array<{ id: string; label?: string | null }>>([]);
const smartCodexModelLoadError = ref("");
const smartAnalysisGuidance = ref("");
const smartRecommendTarget = ref(5);
const smartEnableReviewTarget = ref(false);
const smartReviewTarget = ref(1);
const smartTargetMode = ref<"any" | "all">("all");
const smartAnalyzeAllCandidates = ref(false);
const smartStopAfterCurrentBatch = ref(false);
const smartAnalysisBatchSize = ref(5);
const smartAfterAnalysisBatch = ref<"auto_continue" | "wait_for_user">("auto_continue");
const smartCodexHandoff = ref<"auto" | "manual">("auto");
const smartContextSoftBudgetCharacters = ref(12000);
const smartContextChannel = ref("deep_job_search");
const smartContextSnapshot = ref<FineJobWorkflowContextSnapshot | null>(null);
const smartWorkflowRunId = ref("");
const smartRunLookupId = ref("");
const smartWorkflowJobs = ref<FineJobBossCapturedJob[]>([]);
const smartCaptureTaskRefsKey = ref("");
const smartCaptureTaskIds = ref<string[]>([]);
const smartAnalysisItems = ref<FineJobWorkflowAnalysisItem[]>([]);
const smartSelectedAnalysisItem = ref<FineJobWorkflowAnalysisItem | null>(null);
const smartSelectedAnalysisContext = ref<Record<string, unknown> | null>(null);
const smartAnalysisDetailOpen = ref(false);
const smartFeedbackReason = ref("technical_direction");
const smartControlLoading = ref(false);
const smartCaptureControlLoading = ref(false);
const currentSmartCapture = ref<FineJobSmartCapture | null>(null);
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

const smartWorkflowRun = computed(() => {
  const run = workflowStore.currentRun;
  return run?.workflow_run_id === smartWorkflowRunId.value ? run : null;
});
const smartCaptureRunning = computed(() =>
  ["pending", "running", "pausing"].includes(currentSmartCapture.value?.status ?? "")
);
const smartCaptureResumable = computed(() =>
  ["paused", "waiting_next_batch", "interrupted"].includes(currentSmartCapture.value?.status ?? "")
);
const smartCaptureCanStart = computed(() =>
  !currentSmartCapture.value
  || ["completed", "stopped", "failed"].includes(currentSmartCapture.value.status)
);
const smartActiveAnalysisItem = computed(() =>
  smartAnalysisItems.value.find((item) => item.status === "running") ?? null
);
const smartPendingReviewItems = computed(() => smartAnalysisItems.value.filter(
  (item) => item.status === "succeeded" && item.analysis_result.review_status === "pending"
));
const smartAnalysisResultItems = computed(() => smartAnalysisItems.value.filter(
  (item) => item.status === "succeeded"
));
const smartCompletionProgress = computed(() => smartWorkflowRun.value?.completion_progress);
const smartPrefetchProgress = computed(() =>
  smartWorkflowRun.value?.prefetch ?? smartWorkflowRun.value?.telemetry.prefetch
);
const smartSearchPlanner = computed(() => smartWorkflowRun.value?.telemetry.search_planner ?? null);
const smartPlannerMetric = (key: string) => Number(
  smartSearchPlanner.value?.current_combination?.metrics?.[key] ?? 0
);
const smartPlannerFilterText = computed(() => {
  const labels = smartSearchPlanner.value?.current_combination?.platform_filter_labels ?? [];
  return labels.length ? labels.join(" + ") : "Baseline {}";
});
const smartPlannerReasonText = computed(() => {
  const reason = smartSearchPlanner.value?.last_transition?.reason ?? "";
  const labels: Record<string, string> = {
    duplicate_pool_skew: "历史重复池高度集中，尝试其它平台筛选值",
    filter_quality_exhaustion: "Fresh 岗位主要被策略拒绝，收窄高频失败维度",
    low_novelty_exhausted: "当前组合的低新鲜度窗口已达到切换条件",
    low_qualified_yield: "当前组合的合格 Fresh 产出偏低，尝试新的平台筛选值",
    approved_city_next: "当前关键词的城市 Scope 已耗尽，进入下一个批准城市",
    approved_keyword_next: "当前关键词的批准城市均已耗尽，进入下一个批准关键词",
    approved_platform_search_space_exhausted: "当前 Scope 的合理平台组合已耗尽",
    baseline: "先执行当前 Scope 的 Baseline 搜索"
  };
  return labels[reason] || reason || "等待当前组合采集结果";
});
const smartPlannerNextActionText = computed(() => {
  const transition = smartSearchPlanner.value?.last_transition;
  if (!transition) return "开始 Baseline 搜索";
  if (transition.reason === "baseline") {
    const status = smartSearchPlanner.value?.current_combination?.status;
    if (status === "running") return "执行 Baseline 搜索";
    if (status === "pending") return "开始 Baseline 搜索";
    return "Baseline 搜索已完成";
  }
  if (transition.action === "ADD_FILTER") return `增加${transition.selected_axis}平台筛选`;
  if (transition.action === "REMOVE_FILTER") return `移除${transition.selected_axis}平台筛选`;
  if (transition.action === "REPLACE_FILTER") return `替换${transition.selected_axis}平台筛选值`;
  if (transition.reason === "approved_city_next") return "切换下一个批准城市并从 Baseline 开始";
  if (transition.reason === "approved_keyword_next") return "切换下一个批准关键词并从 Baseline 开始";
  return transition.action === "SWITCH_COMBINATION" ? "切换下一个搜索组合" : "继续当前组合";
});
const smartActiveCaptureStatus = computed(() => {
  const tasks = smartWorkflowRun.value?.tasks ?? [];
  return [...tasks]
    .reverse()
    .find((task) => task.task_type === "deep_job_search" && task.capture && ["pending", "running"].includes(task.status))
    ?.capture ?? null;
});
const smartCaptureStatusText = computed(() => {
  const capture = smartActiveCaptureStatus.value;
  if (!capture) return "";
  const statusLabels: Record<string, string> = {
    queued: "已排队", running: "运行中", completed: "已完成", failed: "失败", unavailable: "状态暂不可用"
  };
  const stageLabels: Record<string, string> = {
    queued: "等待采集器", list_collecting: "采集岗位列表", list_continuing: "继续采集岗位列表", details_collecting: "采集岗位详情"
  };
  const progress = capture.progress_total > 0 ? `${capture.progress_current}/${capture.progress_total}` : "";
  return [
    `采集任务：${statusLabels[capture.status] || capture.status}`,
    stageLabels[capture.stage] ? `阶段：${stageLabels[capture.stage]}` : "",
    progress ? `进度：${progress}` : "",
    capture.message || ""
  ].filter(Boolean).join("；");
});
const hasCurrentSmartWorkflowCodexSession = computed(() => Boolean(
  smartWorkflowRun.value?.codex_session_ref
    && codexStore.status === "running"
    && smartWorkflowRun.value.codex_session_ref === codexStore.sessionRef
));
const hasEndedSmartWorkflowCodexSession = computed(() => Boolean(
  smartWorkflowRun.value?.codex_session_ref?.startsWith("runtime:")
    && !hasCurrentSmartWorkflowCodexSession.value
));
const canResubmitSmartWorkflowCodex = computed(() => {
  const run = smartWorkflowRun.value;
  const handoff = run?.analysis_handoff;
  return Boolean(
    handoff?.attempt_status === "prompt_written"
      && handoff.codex_session_ref === codexStore.sessionRef
      && run?.codex_session_ref === codexStore.sessionRef
      && codexStore.status === "running"
  );
});
const canRetrySmartWorkflowCodex = computed(() => Boolean(
  smartWorkflowRun.value?.analysis_handoff?.attempt_status === "prompt_written"
    && smartWorkflowRun.value.analysis_handoff.retry_available
));
const smartWorkflowCodexEntry = computed(() => {
  const run = smartWorkflowRun.value;
  const handoff = run?.analysis_handoff;
  if (!run || run.status !== "waiting_codex" || !handoff) return null;
  const handoffMode = run.completion_contract?.execution_policy?.codex_handoff ?? "auto";
  if (handoffMode === "auto") {
    return ["prompt_written", "started"].includes(handoff.attempt_status) || run.codex_session_ref
      ? { action: "view" as const, label: "查看 Codex" }
      : null;
  }
  if (handoff.attempt_status === "prompt_written") return { action: "view" as const, label: "等待 Codex 开始" };
  if (hasCurrentSmartWorkflowCodexSession.value && handoff.attempt_status === "started" && handoff.codex_processing) {
    return { action: "view" as const, label: "Codex 分析中" };
  }
  if (hasCurrentSmartWorkflowCodexSession.value && handoff.needs_next_batch_handoff) {
    return { action: "continue" as const, label: "继续分析下一批" };
  }
  if (hasCurrentSmartWorkflowCodexSession.value) return { action: "view" as const, label: "查看 Codex 分析" };
  if (handoff.needs_initial_codex_handoff || handoff.needs_next_batch_handoff || handoff.recovery_available) {
    return { action: "submit" as const, label: "交给 Codex 分析" };
  }
  return null;
});
const smartWorkflowHandoffStatus = computed(() => {
  const run = smartWorkflowRun.value;
  const handoff = run?.analysis_handoff;
  if (!run || run.status !== "waiting_codex" || !handoff) return "";
  if ((run.completion_contract?.execution_policy?.codex_handoff ?? "auto") !== "auto") return "";
  if (handoff.attempt_status === "prompt_written") return "等待 Codex 开始";
  if (handoff.attempt_status === "started") return "Codex 分析中";
  if (handoff.needs_initial_codex_handoff || handoff.needs_next_batch_handoff) return "准备 Codex";
  return "";
});
const currentTaskBelongsToSmartWorkflow = computed(() => {
  const currentTaskId = captureStore.task?.id;
  if (!currentTaskId) return false;
  if (smartCaptureTaskIds.value.includes(currentTaskId)) return true;
  const run = workflowStore.currentRun;
  return Boolean(
    run?.workflow_type === "deep_job_search"
    && !["completed", "completed_with_errors", "cancelled", "failed"].includes(run.status)
    && run.tasks?.some((task) => task.task_type === "deep_job_search" && task.operation_ref_id === currentTaskId)
  );
});
const currentTaskIsSmartCapture = computed(
  () => captureStore.task?.capture_source === "smart" || currentTaskBelongsToSmartWorkflow.value
);
const currentTaskIsCustomCapture = computed(
  () => Boolean(captureStore.task) && !currentTaskIsSmartCapture.value
);
const jobDisplayKey = (job: FineJobBossCapturedJob) =>
  String(job.job_id || job.history_record_id || job.id || job.encrypt_job_id || "");
const mergeDisplayJobs = (...sources: FineJobBossCapturedJob[][]) => {
  const jobs = new Map<string, FineJobBossCapturedJob>();
  for (const source of sources) {
    for (const job of source) {
      const key = jobDisplayKey(job);
      if (key) jobs.set(key, job);
    }
  }
  return [...jobs.values()];
};
const workflowDisplayJobs = computed(() => {
  // 智能采集期间只接入属于当前智能任务的采集快照，避免带入旧的自定义采集岗位。
  if (currentSmartCapture.value) {
    const currentTaskId = captureStore.task?.id;
    const currentSmartJobs = currentTaskId && smartCaptureTaskIds.value.includes(currentTaskId)
      ? captureStore.task?.jobs ?? []
      : [];
    return mergeDisplayJobs(smartWorkflowJobs.value, currentSmartJobs);
  }
  return captureStore.task?.jobs ?? [];
});

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
const selectedBossFilters = computed<Record<string, string>>(() => {
  const filters: Record<string, string> = {};
  const addMultiValue = (key: string, values: string[]) => {
    if (values.length) filters[key] = values.join(",");
  };

  if (bossFilters.jobType) filters.jobType = bossFilters.jobType;
  if (bossFilters.jobType === "1901" && bossFilters.salary) {
    filters.salary = bossFilters.salary === "406-plus" ? "406" : bossFilters.salary;
  }
  if (bossFilters.jobType === "1903") {
    addMultiValue("payType", bossFilters.payType);
    addMultiValue("partTime", bossFilters.partTime);
  }
  addMultiValue("experience", bossFilters.experience);
  addMultiValue("degree", bossFilters.degree);
  addMultiValue("scale", bossFilters.scale);
  addMultiValue("stage", bossFilters.stage);
  return filters;
});

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
const customCaptureRunning = computed(
  () => currentTaskIsCustomCapture.value && taskRunning.value
);
const captureProgressVisible = computed(() => {
  const task = captureStore.task;
  if (!task || task.status === "completed") return false;
  // 失败任务保留进度面板用于展示失败原因；排队和执行中的任务展示实时进度。
  return task.status === "failed" || task.status === "queued" || task.status === "running";
});
const canContinueCapture = computed(
  () => Boolean(
    captureStore.status?.running
    && captureStore.task?.status === "completed"
    && currentTaskIsCustomCapture.value
    && captureStore.task.continuation_available
  )
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
  workflowDisplayJobs.value.find((job) => jobDisplayKey(job) === selectedJobId.value) ?? null
);
const filterStatusRank = (status?: FineJobBossCapturedJob["filter_status"]) => {
  if (status === "pass") return 0;
  if (status === "review") return 1;
  if (status === "reject") return 2;
  if (status === "exclude") return 2;
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
  return [...workflowDisplayJobs.value].sort((left, right) => {
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
  const jobs = workflowDisplayJobs.value;
  return {
    selected: selectedJobIds.value.length,
    recommended: jobs.filter((job) => job.recommended).length,
    passed: jobs.filter((job) => job.filter_status === "pass").length,
    rejected: jobs.filter((job) => job.filter_status === "reject" || job.filter_status === "exclude").length,
    review: jobs.filter((job) => job.filter_status === "review").length,
    completed: jobs.filter((job) => job.detail_status === "completed").length,
    failed: jobs.filter((job) => job.detail_status === "failed").length
  };
});
const captureStatusLabel = (status?: string) => ({
  queued: "等待执行",
  running: "正在采集",
  completed: "采集完成",
  failed: "采集失败"
}[status || ""] || "尚未开始");
const captureStageLabel = (stage?: string) => ({
  queued: "等待执行",
  list_collecting: "采集岗位列表",
  list_continue_queued: "准备继续采集",
  list_continuing: "继续采集岗位列表",
  details_collecting: "采集岗位详情",
  details_stopped: "用户已停止详情采集",
  list_stopped: "用户已停止采集"
}[stage || ""] || stage || "尚未开始");
const captureOverview = computed(() => {
  const task = captureStore.task;
  const jobs = workflowDisplayJobs.value;
  return {
    status: captureStatusLabel(task?.status),
    stage: captureStageLabel(task?.stage),
    message: task?.message || "还没有开始岗位采集。",
    keyword: task?.keyword || form.keyword || "—",
    city: task?.city || form.city || "—",
    pages: task?.total_pages_loaded ?? 0,
    jobs: jobs.length,
    freshJobs: jobs.filter((job) => !job.is_previously_collected).length,
    duplicateJobs: jobs.filter((job) => job.is_previously_collected).length,
    passed: jobs.filter((job) => job.filter_status === "pass").length,
    review: jobs.filter((job) => job.filter_status === "review").length,
    rejected: jobs.filter((job) => job.filter_status === "reject" || job.filter_status === "exclude").length,
    detailsCompleted: jobs.filter((job) => job.detail_status === "completed").length,
    detailsFailed: jobs.filter((job) => job.detail_status === "failed").length,
    hasMore: task?.has_more === true,
    continuationAvailable: task?.continuation_available === true,
    currentJob: task?.current_job
  };
});
const selectedSmartFilterStrategy = computed(() =>
  strategiesStore.filters.find((item) => item.id === smartFilterStrategyId.value) ?? null
);
const selectedSmartRecommendationStrategy = computed(() =>
  strategiesStore.recommendations.find((item) => item.id === smartRecommendationStrategyId.value) ?? null
);
const selectedJobs = computed(() =>
  workflowDisplayJobs.value.filter((job) => selectedJobIds.value.includes(jobDisplayKey(job)))
);
const selectedWorkflowJobIds = computed(() =>
  selectedJobs.value
    .filter((job) => job.detail_status === "completed")
    .map((job) => String(job.history_record_id || job.id || job.job_id || ""))
    .filter(Boolean)
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

const fallbackSmartCodexModels = [
  { id: "gpt-5.6", label: "GPT-5.6 Sol（gpt-5.6）" },
  { id: "gpt-5.6-terra", label: "GPT-5.6 Terra（gpt-5.6-terra）" },
  { id: "gpt-5.6-luna", label: "GPT-5.6 Luna（gpt-5.6-luna）" }
];

const loadCachedSmartCodexModels = () => {
  if (typeof window === "undefined") return [];
  try {
    const cached = JSON.parse(window.localStorage.getItem("fine-job.codex-model-options") ?? "null");
    if (!Array.isArray(cached?.models)) return [];
    return cached.models.filter(
      (item: unknown): item is { id: string; label?: string | null } =>
        Boolean(item && typeof item === "object" && typeof (item as { id?: unknown }).id === "string")
    );
  } catch {
    return [];
  }
};

const mergeSmartCodexModels = (
  models: Array<{ id: string; label?: string | null }>,
  configuredModel: string | null | undefined
) => {
  const merged = new Map<string, { id: string; label?: string | null }>();
  for (const model of [...models, ...fallbackSmartCodexModels]) {
    const id = model.id.trim();
    if (id) merged.set(id, { ...model, id });
  }
  const configuredId = configuredModel?.trim();
  if (configuredId && !merged.has(configuredId)) {
    merged.set(configuredId, { id: configuredId, label: `${configuredId}（当前配置）` });
  }
  return [...merged.values()];
};

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
  smartFilterStrategyId.value = initialFilter?.id ?? null;
  smartSelectedKeywords.value = [...(initialFilter?.search_keywords ?? [])];
  smartSelectedCities.value = [...(initialFilter?.cities ?? [])];
  recommendationStrategyId.value = initialRecommendation?.id ?? null;
  smartRecommendationStrategyId.value = initialRecommendation?.id ?? null;
  try {
    const config = await api.getConfig();
    smartCodexModel.value = config.codex_model || "";
    smartCodexReasoningEffort.value = (config.codex_reasoning_effort as typeof smartCodexReasoningEffort.value) || "medium";
    try {
      const result = await api.listCodexModels(config.codex_cli_path || "codex");
      smartCodexModels.value = mergeSmartCodexModels(result.models, config.codex_model);
    } catch (value) {
      smartCodexModels.value = mergeSmartCodexModels(loadCachedSmartCodexModels(), config.codex_model);
      smartCodexModelLoadError.value = value instanceof Error ? value.message : "Codex 模型目录加载失败，可直接输入模型 ID。";
    }
  } catch (value) {
    smartCodexModels.value = mergeSmartCodexModels(loadCachedSmartCodexModels(), "");
    smartCodexModelLoadError.value = value instanceof Error ? value.message : "Codex 配置加载失败，可直接输入模型 ID。";
  }
  const { smart_capture: restoredCapture } = await api.getCurrentFineJobSmartCapture();
  await applyCurrentSmartCapture(restoredCapture);
  // 进入页面时优先展示正在执行的任务；普通采集任务没有智能 Run，因此落到自定义采集。
  if (taskRunning.value && !currentTaskBelongsToSmartWorkflow.value) {
    activeCaptureConditionTab.value = "custom";
  }
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

const handleJobTypeChange = () => {
  // 求职类型切换后，清除已不属于当前类型的附属筛选条件。
  bossFilters.salary = "";
  bossFilters.payType = [];
  bossFilters.partTime = [];
};

const selectBossFiltersFromStrategy = () => {
  const strategy = strategiesStore.filters.find((item) => item.id === filterStrategyId.value);
  if (!strategy) {
    ElMessage.warning("请先选择岗位筛选策略");
    return;
  }

  // BOSS 搜索页只支持单个求职类型；策略同时包含多种类型时按全职、兼职的顺序应用。
  bossFilters.jobType = strategy.job_types
    .map((type) => strategyJobTypeCodes[type])
    .find(Boolean) ?? "";
  bossFilters.salary = "";
  bossFilters.payType = [];
  bossFilters.partTime = [];
  bossFilters.experience = strategy.experiences.flatMap((value) =>
    value === "在校/应届" ? ["108", "102"] : optionValues([value], experienceOptions)
  );
  bossFilters.degree = optionValues(
    strategy.degrees.filter((value) => value !== "学历不限"),
    degreeOptions
  );
  bossFilters.scale = optionValues(strategy.company_scales, scaleOptions);
  bossFilters.stage = optionValues(strategy.company_stages, stageOptions);

  const monthlySalaryTarget = Math.max(
    Number(strategy.monthly_salary_min ?? 0),
    Number(strategy.monthly_salary_max_at_least ?? 0)
  );
  if (bossFilters.jobType === "1901" && monthlySalaryTarget > 0) {
    bossFilters.salary = monthlySalaryTarget >= 20
      ? "406"
      : monthlySalaryTarget >= 10
        ? "405"
        : monthlySalaryTarget >= 5
          ? "404"
          : monthlySalaryTarget >= 3
            ? "403"
            : "402";
  }
  ElMessage.success(`已应用“${strategy.name}”中的 BOSS 可用筛选条件`);
};

const syncSmartStrategyScope = () => {
  const strategy = selectedSmartFilterStrategy.value;
  smartSelectedKeywords.value = [...(strategy?.search_keywords ?? [])];
  smartSelectedCities.value = [...(strategy?.cities ?? [])];
};

const resetSmartWorkflowView = () => {
  // 自定义采集只切换岗位列表与采集控制，已有驾驶舱镜像继续保持原状态。
  currentSmartCapture.value = null;
  smartWorkflowJobs.value = [];
  smartCaptureTaskRefsKey.value = "";
  smartCaptureTaskIds.value = [];
  selectedJobIds.value = [];
  selectedJobId.value = null;
  jobsTable.value?.clearSelection();
};

const applyCurrentSmartCapture = async (capture: FineJobSmartCapture | null) => {
  currentSmartCapture.value = capture;
  if (!capture) {
    smartWorkflowJobs.value = [];
    if (captureStore.task?.capture_source === "smart") captureStore.clearTask();
    return;
  }
  activeCaptureConditionTab.value = "smart";
  smartWorkflowJobs.value = [...(capture.jobs ?? [])];
  const batch = capture.current_batch as Partial<FineJobBossCaptureTask> | null;
  if (batch?.id && batch.status && batch.stage && Array.isArray(batch.jobs)) {
    captureStore.setTask(batch as FineJobBossCaptureTask);
  } else if (captureStore.task?.id !== capture.current_batch_id) {
    captureStore.clearTask();
  }
  const linkedRun = capture.workflow_run ?? null;
  if (!linkedRun) {
    return;
  }
  const displayedRun = workflowStore.currentRun;
  if (
    !displayedRun
    || displayedRun.workflow_run_id !== linkedRun.workflow_run_id
    || displayedRun.updated_at !== linkedRun.updated_at
  ) {
    workflowStore.setRun(linkedRun);
  }
  smartWorkflowRunId.value = linkedRun.workflow_run_id;
  smartRunLookupId.value = linkedRun.workflow_run_id;
  smartAnalysisGuidance.value = linkedRun.completion_contract?.analysis_guidance?.text || "";
  smartCodexHandoff.value = linkedRun.completion_contract?.execution_policy?.codex_handoff ?? "auto";
  await loadSmartContextSnapshot();
  await loadSmartAnalysisItems();
  if (!workflowStore.pollingActive) workflowStore.startPolling();
};

const refreshCurrentSmartCapture = async () => {
  const { smart_capture: capture } = await api.getCurrentFineJobSmartCapture();
  await applyCurrentSmartCapture(capture);
  return capture;
};

const showCollectionStartBlocked = async (errorValue: unknown, targetLabel: string) => {
  if (errorValue instanceof ApiError && errorValue.errorCategory === "COLLECTION_TASK_ACTIVE") {
    await ElMessageBox.alert(errorValue.message, `无法开始${targetLabel}`, {
      type: "warning",
      confirmButtonText: "知道了"
    });
    return;
  }
  ElMessage.error(errorValue instanceof Error ? errorValue.message : `启动${targetLabel}失败`);
};

const ensureCollectionStartAvailable = async (targetLabel: string) => {
  const { active_task: activeTask } = await api.getFineJobActiveCollectionTask();
  if (!activeTask) return true;
  const activeLabel = activeTask.kind === "smart" ? "智能采集" : "自定义采集";
  await ElMessageBox.alert(
    `当前${activeLabel}尚未结束，请先停止${activeLabel}后再开始${targetLabel}。`,
    `无法开始${targetLabel}`,
    { type: "warning", confirmButtonText: "知道了" }
  );
  return false;
};

const startSmartCapture = async () => {
  const strategy = selectedSmartFilterStrategy.value;
  if (!strategy?.id || !smartSelectedKeywords.value.length || !smartSelectedCities.value.length) {
    ElMessage.warning("请先完成岗位筛选策略、搜索词和城市选择");
    return;
  }
  try {
    if (!await ensureCollectionStartAvailable("智能采集")) return;
    smartCaptureControlLoading.value = true;
    const capture = await api.createFineJobSmartCapture({
      filter_strategy_id: strategy.id,
      candidate_target_count: smartCandidateTargetCount.value,
      allowed_search_keywords: smartSelectedKeywords.value,
      allowed_cities: smartSelectedCities.value,
      pages: form.pages,
      include_details: form.includeDetails,
      prefer_current_page: form.preferCurrentPage
    });
    await applyCurrentSmartCapture(capture);
    ElMessage.success("智能采集任务已启动");
  } catch (errorValue) {
    await showCollectionStartBlocked(errorValue, "智能采集");
  } finally {
    smartCaptureControlLoading.value = false;
  }
};

const pauseCurrentSmartCapture = async () => {
  if (!currentSmartCapture.value) return;
  try {
    smartCaptureControlLoading.value = true;
    await applyCurrentSmartCapture(
      await api.pauseFineJobSmartCapture(currentSmartCapture.value.smart_capture_id)
    );
    ElMessage.success("正在安全暂停岗位采集任务");
  } catch (errorValue) {
    ElMessage.error(errorValue instanceof Error ? errorValue.message : "暂停岗位采集失败");
  } finally {
    smartCaptureControlLoading.value = false;
  }
};

const resumeCurrentSmartCapture = async () => {
  if (!currentSmartCapture.value) return;
  try {
    smartCaptureControlLoading.value = true;
    await applyCurrentSmartCapture(
      await api.resumeFineJobSmartCapture(currentSmartCapture.value.smart_capture_id)
    );
    ElMessage.success("岗位采集任务已继续");
  } catch (errorValue) {
    ElMessage.error(errorValue instanceof Error ? errorValue.message : "继续岗位采集失败");
  } finally {
    smartCaptureControlLoading.value = false;
  }
};

const stopCurrentSmartCapture = async () => {
  if (!currentSmartCapture.value) return;
  try {
    smartCaptureControlLoading.value = true;
    await applyCurrentSmartCapture(
      await api.stopFineJobSmartCapture(currentSmartCapture.value.smart_capture_id)
    );
    ElMessage.success("岗位采集任务已停止");
  } catch (errorValue) {
    ElMessage.error(errorValue instanceof Error ? errorValue.message : "停止岗位采集失败");
  } finally {
    smartCaptureControlLoading.value = false;
  }
};

const pauseSmartWorkflow = async () => {
  try {
    smartControlLoading.value = true;
    await workflowStore.pause();
    await refreshCurrentSmartCapture();
    ElMessage.success("正在同步暂停驾驶舱任务和关联岗位采集。");
  } catch (errorValue) {
    ElMessage.error(errorValue instanceof Error ? errorValue.message : "暂停智能任务失败");
  } finally {
    smartControlLoading.value = false;
  }
};

const resumeSmartWorkflow = async () => {
  try {
    smartControlLoading.value = true;
    await workflowStore.resume();
    await refreshCurrentSmartCapture();
    ElMessage.success("智能任务已继续推进。");
  } catch (errorValue) {
    ElMessage.error(errorValue instanceof Error ? errorValue.message : "继续智能任务失败");
  } finally {
    smartControlLoading.value = false;
  }
};

const stopSmartWorkflow = async () => {
  try {
    smartControlLoading.value = true;
    const run = await workflowStore.cancel();
    await refreshCurrentSmartCapture();
    if (run) await syncSmartCaptureTask(run);
    ElMessage.success("已发送停止请求，正在等待当前采集步骤结束。");
  } catch (errorValue) {
    ElMessage.error(errorValue instanceof Error ? errorValue.message : "停止智能任务失败");
  } finally {
    smartControlLoading.value = false;
  }
};

const saveSmartAnalysisGuidance = async () => {
  const run = smartWorkflowRun.value;
  if (!run) return;
  try {
    const updated = await api.updateFineJobWorkflowAnalysisGuidance(
      run.workflow_run_id,
      smartAnalysisGuidance.value
    );
    workflowStore.setRun(updated);
    ElMessage.success("本 Run 分析指导已保存");
  } catch (errorValue) {
    ElMessage.error(errorValue instanceof Error ? errorValue.message : "保存本 Run 分析指导失败");
  }
};

const restoreSmartWorkflowRun = async () => {
  const linkedWorkflowRunId = currentSmartCapture.value?.workflow_run_id?.trim() ?? "";
  const mirrorWorkflowRunId = linkedWorkflowRunId || smartWorkflowRunId.value.trim();
  if (!mirrorWorkflowRunId) {
    ElMessage.warning("当前岗位采集任务没有关联驾驶舱任务");
    return;
  }
  const workflowRunId = smartRunLookupId.value.trim();
  if (workflowRunId && workflowRunId !== mirrorWorkflowRunId) {
    smartRunLookupId.value = mirrorWorkflowRunId;
    ElMessage.warning("Workflow Run 操作区保持当前驾驶舱镜像任务");
    return;
  }
  try {
    const run = await workflowStore.refresh(mirrorWorkflowRunId);
    if (!run || run.workflow_type !== "deep_job_search") {
      ElMessage.warning("该 Run 不是智能岗位采集任务");
      return;
    }
    smartWorkflowRunId.value = run.workflow_run_id;
    smartRunLookupId.value = run.workflow_run_id;
    smartAnalysisGuidance.value = run.completion_contract?.analysis_guidance?.text || "";
    smartCodexHandoff.value = run.completion_contract?.execution_policy?.codex_handoff ?? "auto";
    await syncSmartCaptureTask(run);
    await loadSmartContextSnapshot();
    await loadSmartAnalysisItems();
    workflowStore.startPolling();
    ElMessage.success("已恢复智能采集 Run");
  } catch (errorValue) {
    ElMessage.error(errorValue instanceof Error ? errorValue.message : "恢复智能采集 Run 失败");
  }
};

const listSmartText = (value: unknown) => Array.isArray(value)
  ? value.map((item) => String(item)).join("；")
  : "";

const loadSmartContextSnapshot = async (showError = false) => {
  const run = smartWorkflowRun.value;
  if (!run) return;
  try {
    smartContextSnapshot.value = await api.getFineJobWorkflowContextSnapshot(
      run.workflow_run_id,
      smartContextChannel.value
    );
  } catch (errorValue) {
    smartContextSnapshot.value = null;
    if (showError) {
      ElMessage.error(errorValue instanceof Error ? errorValue.message : "加载 Context 快照失败");
    }
  }
};

const advanceSmartWorkflowRun = async () => {
  const run = smartWorkflowRun.value;
  if (!run) return;
  try {
    // 推进前刷新当前 Run，确保采集任务关联与后续快照基于最新状态。
    await workflowStore.refresh(run.workflow_run_id);
    workflowStore.startPolling();
    await workflowStore.advance();
    if (workflowStore.currentRun) {
      await syncSmartCaptureTask(workflowStore.currentRun);
    }
    await loadSmartContextSnapshot();
    await loadSmartAnalysisItems();
  } catch (errorValue) {
    ElMessage.error(errorValue instanceof Error ? errorValue.message : "推进智能采集 Run 失败");
  }
};

const loadSmartAnalysisItems = async (showError = false) => {
  const run = smartWorkflowRun.value;
  if (!run) return;
  try {
    smartAnalysisItems.value = (await api.listFineJobWorkflowAnalysisItems(run.workflow_run_id)).items;
  } catch (errorValue) {
    if (showError) {
      ElMessage.error(errorValue instanceof Error ? errorValue.message : "加载分析队列失败");
    }
  }
};

const viewSmartAnalysisItem = async (item: FineJobWorkflowAnalysisItem) => {
  const run = smartWorkflowRun.value;
  if (!run) return;
  try {
    smartSelectedAnalysisItem.value = item;
    smartSelectedAnalysisContext.value = await api.getFineJobWorkflowAnalysisItemContext(
      run.workflow_run_id,
      item.workflow_task_id
    );
    smartAnalysisDetailOpen.value = true;
  } catch (errorValue) {
    ElMessage.error(errorValue instanceof Error ? errorValue.message : "加载分析详情失败");
  }
};

const saveSmartAnalysisFeedback = async (
  item: FineJobWorkflowAnalysisItem,
  sentiment: "expected" | "unexpected"
) => {
  const run = smartWorkflowRun.value;
  if (!run) return;
  try {
    await api.saveFineJobWorkflowAnalysisFeedback(run.workflow_run_id, item.workflow_task_id, {
      sentiment,
      reason: sentiment === "unexpected" ? smartFeedbackReason.value : undefined
    });
    await loadSmartAnalysisItems(true);
    ElMessage.success("分析反馈已保存");
  } catch (errorValue) {
    ElMessage.error(errorValue instanceof Error ? errorValue.message : "保存分析反馈失败");
  }
};

const openSmartWorkflowCodex = async (action: "submit" | "continue" | "view") => {
  const run = smartWorkflowRun.value;
  if (!run) return;
  if (action !== "view") {
    const result = await triggerWorkflowCodexHandoff(run, codexStore, "manual");
    workflowStore.setRun(result.run);
    result.status === "submitted"
      ? ElMessage.success(result.message)
      : ElMessage.warning(result.message);
    return;
  }
  await router.push({
    name: "fine-job-codex",
    query: {
      task: "deep-job-search",
      workflow_run_id: run.workflow_run_id,
      workflow_action: action
    }
  });
};

const resubmitSmartWorkflowCodex = async () => {
  const run = smartWorkflowRun.value;
  if (!run) return;
  const result = await resubmitWorkflowCodexSubmit(run, codexStore);
  workflowStore.setRun(result.run);
  result.status === "enter_submitted"
    ? ElMessage.success(result.message)
    : ElMessage.warning(result.message);
};

const retrySmartWorkflowCodex = async () => {
  const run = smartWorkflowRun.value;
  if (!run) return;
  const result = await retryWorkflowCodexHandoff(run, codexStore);
  workflowStore.setRun(result.run);
  result.status === "submitted"
    ? ElMessage.success(result.message)
    : ElMessage.warning(result.message);
};

const createManualCodexBatch = async () => {
  if (!smartWorkflowRun.value) {
    ElMessage.warning("请先启动一个关闭投递目标的智能采集任务");
    return;
  }
  const strategyId = recommendationStrategyId.value || smartRecommendationStrategyId.value;
  if (!strategyId) {
    ElMessage.warning("请先选择建议投递策略");
    return;
  }
  if (!selectedWorkflowJobIds.value.length) {
    ElMessage.warning("请先选择已完成详情的岗位");
    return;
  }
  try {
    const run = await api.createFineJobWorkflowManualAnalysisBatch(smartWorkflowRun.value.workflow_run_id, {
      recommendation_strategy_id: strategyId,
      job_ids: selectedWorkflowJobIds.value,
      analysis_batch_size: smartAnalysisBatchSize.value,
      codex_model: smartCodexModel.value || undefined,
      codex_reasoning_effort: smartCodexReasoningEffort.value
    });
    workflowStore.setRun(run);
    const result = await triggerWorkflowCodexHandoff(run, codexStore, "manual");
    workflowStore.setRun(result.run);
    if (result.status === "submitted") {
      ElMessage.success("已将选中岗位交给 Codex 批量生成建议");
    } else {
      ElMessage.warning(result.message);
    }
  } catch (errorValue) {
    ElMessage.error(errorValue instanceof Error ? errorValue.message : "创建 Codex 分析批次失败");
  }
};

const syncSmartCaptureTask = async (run: typeof workflowStore.currentRun) => {
  if (!run) return;
  if (currentSmartCapture.value?.workflow_run_id !== run.workflow_run_id) return;
  // Workflow 状态响应直接携带整个智能任务的岗位集合，确保原岗位列表使用同一份数据。
  if (Array.isArray(run.capture_jobs)) {
    smartWorkflowJobs.value = run.capture_jobs;
  }
  const captureRefs = [...(run.tasks ?? [])]
    .filter((task) => task.task_type === "deep_job_search" && task.operation_ref_id)
    .map((task) => task.operation_ref_id as string);
  const refsKey = captureRefs.join(",");
  smartCaptureTaskIds.value = captureRefs;
  if (refsKey !== smartCaptureTaskRefsKey.value) {
    try {
      // 每个智能采集任务首次出现时主动取一次岗位列表，避免首个状态响应还没有岗位时列表一直为空。
      const result = await api.listFineJobWorkflowCaptureJobs(run.workflow_run_id);
      smartWorkflowJobs.value = mergeDisplayJobs(smartWorkflowJobs.value, result.items);
      smartCaptureTaskRefsKey.value = refsKey;
    } catch {
      // 当前批次仍由采集任务快照展示，汇总接口暂不可用时不影响采集继续执行。
    }
  }
  const captureTask = [...(run?.tasks ?? [])]
    .reverse()
    .find((task) => task.task_type === "deep_job_search" && task.operation_ref_id);
  if (!captureTask?.operation_ref_id || captureTask.operation_ref_id === captureStore.task?.id) return;
  const refreshedTask = await captureStore.refreshTask(captureTask.operation_ref_id);
  if (refreshedTask?.jobs?.length) {
    smartWorkflowJobs.value = mergeDisplayJobs(smartWorkflowJobs.value, refreshedTask.jobs);
  }
};

watch(
  () => workflowStore.currentRun,
  (run) => {
    if (run?.workflow_run_id === smartWorkflowRunId.value) {
      void syncSmartCaptureTask(run);
      void loadSmartAnalysisItems();
      if (currentSmartCapture.value?.workflow_run_id === run.workflow_run_id) {
        void refreshCurrentSmartCapture();
      }
    }
  }
);

watch(
  () => captureStore.task?.updated_at,
  () => {
    if (currentSmartCapture.value) void refreshCurrentSmartCapture();
  }
);

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
    await captureStore.locate({
      keyword: form.keyword.trim(),
      city: form.city.trim(),
      filters: selectedBossFilters.value
    });
    ElMessage.success("已定位到 BOSS 搜索页，可在浏览器中继续调整筛选条件");
  } catch {
    ElMessage.error(captureStore.error ?? "定位 BOSS 搜索页失败");
  }
};

const captureJobs = async () => {
  if (!ensureSearchInput()) return;
  selectedJobIds.value = [];
  try {
    if (!await ensureCollectionStartAvailable("自定义采集")) return;
    await captureStore.capture({
      keyword: form.keyword.trim(),
      city: form.city.trim(),
      pages: form.pages,
      include_details: form.includeDetails,
      prefer_current_page: form.preferCurrentPage,
      filters: selectedBossFilters.value,
      filter_strategy_id: filterStrategyId.value
    });
    resetSmartWorkflowView();
    ElMessage.success("采集任务已启动，可在本页查看实时进度");
  } catch (errorValue) {
    await showCollectionStartBlocked(errorValue, "自定义采集");
  }
};

const handleSelectionChange = (rows: FineJobBossCapturedJob[]) => {
  selectedJobIds.value = rows.map((row) => jobDisplayKey(row)).filter(Boolean);
};

const applySuggestedSelection = async (
  mode: "strategy" | "ai",
  contextStaleAction?: "regenerate" | "use_current" | "cancel"
) => {
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
          recommendationStrategyId: recommendationStrategyId.value,
          contextStaleAction
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
  } catch (errorValue) {
    if (errorValue instanceof ApiError && errorValue.errorCategory === "CONTEXT_STALE_CONFIRMATION_REQUIRED") {
      try {
        await ElMessageBox.confirm(
          "当前岗位评估上下文已过期。请选择 AI 初筛使用的版本。",
          "上下文已过期",
          {
            type: "warning",
            confirmButtonText: "重新生成并继续",
            cancelButtonText: "使用当前版本",
            distinguishCancelAndClose: true
          }
        );
        await applySuggestedSelection(mode, "regenerate");
      } catch (action) {
        if (action === "cancel") await applySuggestedSelection(mode, "use_current");
      }
      return;
    }
    ElMessage.error(captureStore.error ?? "生成详情采集建议失败");
  }
};

const evaluateDeliveries = async (
  jobIds: string[],
  contextStaleAction?: "regenerate" | "use_current" | "cancel"
) => {
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
      jobIds,
      contextStaleAction
    );
    const recommended = evaluations.filter((item) => item.decision === "recommend").length;
    const review = evaluations.filter((item) => item.decision === "review").length;
    ElMessage.success(`投递评估完成：建议 ${recommended}，待判断 ${review}`);
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
        await evaluateDeliveries(jobIds, "regenerate");
      } catch (action) {
        if (action === "cancel") await evaluateDeliveries(jobIds, "use_current");
      }
      return;
    }
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
  selectedJobId.value = jobDisplayKey(job) || null;
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

const continueCapture = async () => {
  try {
    await captureStore.continueCapture(form.pages);
    ElMessage.success(`正在原搜索页继续下滑采集 ${form.pages} 页`);
  } catch {
    ElMessage.error(captureStore.error ?? "继续下滑采集失败");
  }
};

const stopCapture = async () => {
  try {
    // 点击停止前刷新后端状态，避免列表已结束或已进入详情阶段时继续使用旧快照。
    const currentTask = await captureStore.refreshTask();
    if (!currentTask) {
      ElMessage.error(captureStore.error ?? "无法读取当前采集任务状态");
      return;
    }
    if (currentTask.status !== "queued" && currentTask.status !== "running") {
      ElMessage.info("当前没有正在执行的自定义采集");
      return;
    }
    await captureStore.stopCaptureTask();
    ElMessage.success("正在停止采集；已获得岗位和原搜索页会保留");
  } catch {
    ElMessage.error(captureStore.error ?? "停止采集失败");
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

const filterStatusLabel = (status?: string) => ({ pass: "通过", reject: "排除", exclude: "冷却排除", review: "待判断" }[status || ""] || "未筛选");
const filterStatusType = (status?: string) => status === "pass" ? "success" : status === "reject" || status === "exclude" ? "danger" : status === "review" ? "warning" : "info";
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

    <section class="page-panel capture-overview-panel">
      <div class="panel-title-row">
        <div>
          <p class="panel-eyebrow">Capture Overview</p>
          <h2>采集概览</h2>
        </div>
        <el-tag :type="captureStore.task?.status === 'failed' ? 'danger' : captureStore.task?.status === 'running' ? 'warning' : 'info'">
          {{ captureOverview.status }}
        </el-tag>
      </div>
      <p>{{ captureOverview.message }}</p>
      <div class="capture-overview-grid">
        <div><span class="secondary-text">搜索条件</span><strong>{{ captureOverview.keyword }} / {{ captureOverview.city }}</strong></div>
        <div><span class="secondary-text">当前阶段</span><strong>{{ captureOverview.stage }}</strong></div>
        <div><span class="secondary-text">已采集页数</span><strong>{{ captureOverview.pages }}</strong></div>
        <div><span class="secondary-text">岗位总数</span><strong>{{ captureOverview.jobs }}</strong></div>
        <div><span class="secondary-text">新岗位 / 重复岗位</span><strong>{{ captureOverview.freshJobs }} / {{ captureOverview.duplicateJobs }}</strong></div>
        <div><span class="secondary-text">通过 / 待确认 / 排除</span><strong>{{ captureOverview.passed }} / {{ captureOverview.review }} / {{ captureOverview.rejected }}</strong></div>
        <div><span class="secondary-text">详情完成 / 失败</span><strong>{{ captureOverview.detailsCompleted }} / {{ captureOverview.detailsFailed }}</strong></div>
        <div><span class="secondary-text">后续采集</span><strong>{{ captureOverview.continuationAvailable && captureOverview.hasMore ? "可以继续" : "暂无更多" }}</strong></div>
      </div>
      <p v-if="captureOverview.currentJob" class="secondary-text">
        当前岗位：{{ captureOverview.currentJob.title }} / {{ captureOverview.currentJob.company }}
      </p>
    </section>

    <section v-if="smartWorkflowRun" class="page-panel smart-workflow-panel">
      <div class="panel-title-row">
        <div><p class="panel-eyebrow">Workflow Run</p><h2>智能任务进度</h2></div>
        <el-tag :type="smartWorkflowRun.waiting_for_user ? 'warning' : 'info'">
          {{ smartWorkflowRun.status }} / {{ smartWorkflowRun.current_step }}
        </el-tag>
      </div>
      <p>{{ smartWorkflowRun.next_action_reason }}</p>
      <div class="workflow-run-grid">
        <div><span class="secondary-text">当前搜索</span><strong>{{ smartWorkflowRun.progress.current_keyword || "等待开始" }} / {{ smartWorkflowRun.progress.current_city || "—" }}</strong></div>
        <div><span class="secondary-text">搜索深度 / 批次</span><strong>{{ smartWorkflowRun.progress.search_depth }} / {{ smartWorkflowRun.progress.search_batch_count }}</strong></div>
        <div><span class="secondary-text">岗位：已见 / Fresh / 重复</span><strong>{{ smartWorkflowRun.progress.jobs_seen }} / {{ smartWorkflowRun.progress.fresh_jobs }} / {{ smartWorkflowRun.progress.duplicate_jobs }}</strong></div>
        <div><span class="secondary-text">初筛候选</span><strong>{{ smartWorkflowRun.progress.candidates }}</strong></div>
        <div><span class="secondary-text">JD：完成 / 已建</span><strong>{{ smartWorkflowRun.progress.jd_completed }} / {{ smartWorkflowRun.progress.jd_total }}</strong></div>
        <div><span class="secondary-text">分析：推荐 / 复核 / 拒绝</span><strong>{{ smartWorkflowRun.progress.recommend_count }} / {{ smartWorkflowRun.progress.review_count }} / {{ smartWorkflowRun.progress.reject_count }}</strong></div>
        <div v-if="smartCompletionProgress"><span class="secondary-text">Recommend 完成</span><strong>{{ smartCompletionProgress.recommend.current }} / {{ smartCompletionProgress.recommend.target }}（剩余 {{ smartCompletionProgress.recommend.remaining }}）</strong></div>
        <div v-if="smartCompletionProgress"><span class="secondary-text">Review 完成</span><strong>{{ smartCompletionProgress.review.target === null ? `${smartCompletionProgress.review.current}（未计入目标）` : `${smartCompletionProgress.review.current} / ${smartCompletionProgress.review.target}（剩余 ${smartCompletionProgress.review.remaining}）` }}</strong></div>
        <div v-if="smartPrefetchProgress && smartPrefetchProgress.status !== 'none'"><span class="secondary-text">下一批 JD</span><strong>{{ smartPrefetchProgress.ready_count }} / {{ smartPrefetchProgress.target_count }} 已准备</strong></div>
      </div>
    </section>

    <section class="page-panel smart-workflow-operations">
      <div class="panel-title-row">
        <div><p class="panel-eyebrow">Workflow Run</p><h2>Workflow Run 操作</h2></div>
      </div>
      <div class="smart-run-restore">
        <el-input v-model="smartRunLookupId" clearable placeholder="输入 Workflow Run ID 查看本轮上下文" @keyup.enter="restoreSmartWorkflowRun" />
        <el-select v-model="smartContextChannel" class="smart-context-channel">
          <el-option label="搜索 Context" value="deep_job_search" />
          <el-option label="分析 Shared Base" value="candidate_analysis" />
        </el-select>
        <el-button :loading="workflowStore.loading" @click="restoreSmartWorkflowRun">查看本轮上下文</el-button>
        <el-button :loading="workflowStore.advancing" :disabled="!smartWorkflowRun" @click="advanceSmartWorkflowRun">立即推进</el-button>
        <el-button v-if="smartWorkflowRun && smartWorkflowRun.status !== 'paused' && !['cancelled', 'completed', 'completed_with_errors', 'failed'].includes(smartWorkflowRun.status)" :loading="smartControlLoading" @click="pauseSmartWorkflow">暂停自动推进</el-button>
        <el-button v-if="smartWorkflowRun?.status === 'paused' || (smartWorkflowRun?.status === 'waiting_for_user' && ['capture_interrupted', 'browser_not_running', 'collection_task_active', 'analysis_batch_completed_waiting_user', 'analysis_batch_waiting_user'].includes(smartWorkflowRun.stop_reason))" :loading="smartControlLoading" @click="resumeSmartWorkflow">继续任务</el-button>
        <el-button v-if="smartWorkflowRun && !['cancelled', 'completed', 'completed_with_errors', 'failed'].includes(smartWorkflowRun.status)" type="danger" plain :loading="smartControlLoading" @click="stopSmartWorkflow">停止任务</el-button>
        <el-button v-if="smartWorkflowCodexEntry" type="primary" @click="openSmartWorkflowCodex(smartWorkflowCodexEntry.action)">{{ smartWorkflowCodexEntry.label }}</el-button>
        <el-button v-if="canResubmitSmartWorkflowCodex" @click="resubmitSmartWorkflowCodex">再次提交</el-button>
        <el-button v-if="canRetrySmartWorkflowCodex" type="warning" @click="retrySmartWorkflowCodex">重新交接</el-button>
        <el-tag v-if="smartWorkflowHandoffStatus" type="info">{{ smartWorkflowHandoffStatus }}</el-tag>
      </div>
      <el-alert
        v-if="hasEndedSmartWorkflowCodexSession"
        title="原 Codex 会话不可恢复；下一批进入 waiting_codex 后可重新交给 Codex 分析。"
        type="info"
        :closable="false"
        show-icon
      />
    </section>

    <section v-if="smartWorkflowRun" class="page-panel smart-workflow-details">
      <div class="panel-title-row">
        <div><p class="panel-eyebrow">Workflow Run</p><h2>Workflow Run 状态与详情</h2></div>
      </div>
      <el-descriptions :column="3" border>
        <el-descriptions-item label="Workflow Run ID"><code>{{ smartWorkflowRun.workflow_run_id }}</code></el-descriptions-item>
        <el-descriptions-item label="Recommend 目标">{{ smartWorkflowRun.completion_contract?.recommend_target ?? smartWorkflowRun.completion_contract?.target_count ?? smartRecommendTarget }}</el-descriptions-item>
        <el-descriptions-item label="Review 目标">{{ smartWorkflowRun.completion_contract?.review_target ?? '未配置' }}</el-descriptions-item>
        <el-descriptions-item label="目标模式">{{ smartWorkflowRun.completion_contract?.target_mode ?? 'all' }}</el-descriptions-item>
        <el-descriptions-item label="业务完成状态">{{ smartCompletionProgress?.target_reached ? '已达到配置目标' : '尚未达到配置目标' }}</el-descriptions-item>
        <el-descriptions-item label="Recommend 完成">{{ smartCompletionProgress ? `${smartCompletionProgress.recommend.current} / ${smartCompletionProgress.recommend.target}（剩余 ${smartCompletionProgress.recommend.remaining}）` : '—' }}</el-descriptions-item>
        <el-descriptions-item label="Review 完成">{{ smartCompletionProgress?.review.target === null ? `${smartCompletionProgress?.review.current ?? 0}（未计入目标）` : `${smartCompletionProgress?.review.current ?? 0} / ${smartCompletionProgress?.review.target ?? 0}（剩余 ${smartCompletionProgress?.review.remaining ?? 0}）` }}</el-descriptions-item>
        <el-descriptions-item label="正式 Recommend（兼容计数）">{{ smartWorkflowRun.completed_count }}</el-descriptions-item>
        <el-descriptions-item label="Recommend 剩余（兼容计数）">{{ smartWorkflowRun.remaining_count }}</el-descriptions-item>
        <el-descriptions-item label="当前搜索">{{ smartWorkflowRun.progress.current_keyword || '等待开始' }} / {{ smartWorkflowRun.progress.current_city || '—' }}</el-descriptions-item>
        <el-descriptions-item label="搜索深度 / 批次">{{ smartWorkflowRun.progress.search_depth }} / {{ smartWorkflowRun.progress.search_batch_count }}</el-descriptions-item>
        <el-descriptions-item label="岗位：已见 / Fresh / 重复">{{ smartWorkflowRun.progress.jobs_seen }} / {{ smartWorkflowRun.progress.fresh_jobs }} / {{ smartWorkflowRun.progress.duplicate_jobs }}</el-descriptions-item>
        <el-descriptions-item label="初筛候选">{{ smartWorkflowRun.progress.candidates }}</el-descriptions-item>
        <el-descriptions-item label="JD：完成 / 已建">{{ smartWorkflowRun.progress.jd_completed }} / {{ smartWorkflowRun.progress.jd_total }}</el-descriptions-item>
        <el-descriptions-item v-if="smartPrefetchProgress && smartPrefetchProgress.status !== 'none'" label="下一批 JD">{{ smartPrefetchProgress.ready_count }} / {{ smartPrefetchProgress.target_count }} 已准备（{{ smartPrefetchProgress.status }}）</el-descriptions-item>
        <el-descriptions-item label="分析：推荐 / 复核 / 拒绝">{{ smartWorkflowRun.progress.recommend_count }} / {{ smartWorkflowRun.progress.review_count }} / {{ smartWorkflowRun.progress.reject_count }}</el-descriptions-item>
        <el-descriptions-item label="当前状态">{{ smartWorkflowRun.status }} / {{ smartWorkflowRun.current_step }}</el-descriptions-item>
        <el-descriptions-item label="下一步">{{ smartWorkflowRun.next_action }}</el-descriptions-item>
        <el-descriptions-item label="下一步原因">{{ smartWorkflowRun.next_action_reason }}</el-descriptions-item>
        <el-descriptions-item label="建议投递策略">{{ smartWorkflowRun.completion_contract?.selected_strategy_ids?.recommendation_strategy_id || '—' }}</el-descriptions-item>
        <el-descriptions-item label="本 Run 模型">{{ smartWorkflowRun.completion_contract?.codex_execution_config?.model || '—' }}</el-descriptions-item>
        <el-descriptions-item label="推理强度">{{ smartWorkflowRun.completion_contract?.codex_execution_config?.reasoning_effort || '—' }}</el-descriptions-item>
        <el-descriptions-item label="Analysis Batch">{{ smartWorkflowRun.completion_contract?.analysis_policy?.analysis_batch_size ?? '—' }}</el-descriptions-item>
        <el-descriptions-item label="批次衔接">{{ smartWorkflowRun.completion_contract?.execution_policy?.after_analysis_batch ?? 'auto_continue' }}</el-descriptions-item>
        <el-descriptions-item label="Codex 交接">{{ smartWorkflowRun.completion_contract?.execution_policy?.codex_handoff ?? 'auto' }}</el-descriptions-item>
      </el-descriptions>
    </section>

    <section v-if="smartSearchPlanner" class="page-panel smart-search-planner">
      <div class="panel-title-row"><div><p class="panel-eyebrow">Search Planner</p><h2>智能搜索策略</h2></div></div>
      <p>搜索范围：{{ smartSearchPlanner.current_scope.keyword || "—" }} / {{ smartSearchPlanner.current_scope.city || "—" }}</p>
      <p>当前组合：{{ smartPlannerFilterText }}</p>
      <p>本组合：Fresh {{ smartPlannerMetric("run_fresh_jobs") }}；历史重复 {{ smartPlannerMetric("historical_duplicates") }}；策略拒绝 {{ smartPlannerMetric("strategy_reject") }}；合格 Fresh {{ smartPlannerMetric("qualified_fresh_jobs") }}</p>
      <p>切换原因：{{ smartPlannerReasonText }}</p>
      <p>下一动作：{{ smartPlannerNextActionText }}</p>
      <p v-if="smartCaptureStatusText">实际采集状态：{{ smartCaptureStatusText }}</p>
    </section>

    <el-tabs v-model="activeCaptureConditionTab" class="capture-condition-tabs">
      <el-tab-pane label="智能采集" name="smart">
        <section class="page-panel smart-capture-panel">
          <div class="panel-title-row">
            <div>
              <p class="panel-eyebrow">Smart Capture</p>
              <h2>智能采集</h2>
            </div>
            <span class="secondary-text">按岗位筛选策略准备搜索范围，再交给现有采集流程执行。</span>
          </div>
          <el-form label-position="top" class="intent-form">
            <div class="form-grid">
              <el-form-item label="岗位筛选策略">
                <el-select v-model="smartFilterStrategyId" placeholder="选择岗位筛选策略" @change="syncSmartStrategyScope">
                  <el-option v-for="item in strategiesStore.filters" :key="item.id" :label="item.name" :value="item.id" />
                </el-select>
              </el-form-item>
              <el-form-item label="采集目标岗位数">
                <el-input-number v-model="smartCandidateTargetCount" :min="1" :max="500" />
                <p class="secondary-text">用于确定本轮希望形成的岗位候选范围。</p>
              </el-form-item>
            </div>
            <el-form-item label="本轮搜索词（从策略中选择）">
              <el-checkbox-group v-model="smartSelectedKeywords">
                <el-checkbox v-for="keyword in selectedSmartFilterStrategy?.search_keywords ?? []" :key="keyword" :label="keyword">
                  {{ keyword }}
                </el-checkbox>
              </el-checkbox-group>
              <p v-if="!selectedSmartFilterStrategy?.search_keywords?.length" class="secondary-text">当前策略没有可用搜索词。</p>
            </el-form-item>
            <el-form-item label="本轮城市（从策略中选择）">
              <el-checkbox-group v-model="smartSelectedCities">
                <el-checkbox v-for="city in selectedSmartFilterStrategy?.cities ?? []" :key="city" :label="city">
                  {{ city }}
                </el-checkbox>
              </el-checkbox-group>
              <p v-if="!selectedSmartFilterStrategy?.cities?.length" class="secondary-text">当前策略没有可用城市。</p>
            </el-form-item>
            <el-form-item label="本 Run 临时分析指导（可选）">
              <el-input v-model="smartAnalysisGuidance" type="textarea" :rows="3" placeholder="只影响本 Run 的后续分析，不修改长期正式策略" />
            </el-form-item>
            <el-form-item label="本轮 Context 软预算（字符）">
              <el-input-number v-model="smartContextSoftBudgetCharacters" :min="1000" :max="200000" :step="1000" />
              <p class="secondary-text">超出预算时，本 Run 会暂停并显示原因；该设置在建立新 Run 时生效。</p>
            </el-form-item>
            <el-form-item label="投递目标">
              <el-switch v-model="smartDeliveryTargetEnabled" active-text="采集后自动交给 Codex 分析" inactive-text="采集完成后等待手动选择" />
            </el-form-item>
            <template v-if="smartDeliveryTargetEnabled">
              <div class="form-grid">
                <el-form-item label="建议投递策略">
                  <el-select v-model="smartRecommendationStrategyId" placeholder="选择建议投递策略">
                    <el-option v-for="item in strategiesStore.recommendations" :key="item.id" :label="item.name" :value="item.id" />
                  </el-select>
                </el-form-item>
                <el-form-item label="Recommend 目标">
                  <el-input-number v-model="smartRecommendTarget" :min="1" :max="100" />
                </el-form-item>
                <el-form-item label="Review 目标（可选）">
                  <div class="inline-form-control">
                    <el-switch v-model="smartEnableReviewTarget" active-text="计入目标" inactive-text="不计入目标" />
                    <el-input-number v-if="smartEnableReviewTarget" v-model="smartReviewTarget" :min="1" :max="100" />
                  </div>
                </el-form-item>
                <el-form-item label="目标达成模式">
                  <el-select v-model="smartTargetMode">
                    <el-option label="全部目标达到" value="all" />
                    <el-option label="任一目标达到" value="any" />
                  </el-select>
                </el-form-item>
                <el-form-item label="Codex 模型">
                  <el-select v-model="smartCodexModel" filterable allow-create placeholder="选择或输入 Codex 模型">
                    <el-option v-for="item in smartCodexModels" :key="item.id" :label="item.label || item.id" :value="item.id" />
                  </el-select>
                  <div v-if="smartCodexModelLoadError" class="secondary-text">{{ smartCodexModelLoadError }}</div>
                </el-form-item>
                <el-form-item label="推理强度">
                  <el-select v-model="smartCodexReasoningEffort">
                    <el-option label="minimal" value="minimal" />
                    <el-option label="low" value="low" />
                    <el-option label="medium" value="medium" />
                    <el-option label="high" value="high" />
                    <el-option label="xhigh" value="xhigh" />
                  </el-select>
                </el-form-item>
                <el-form-item label="分析批次大小">
                  <el-input-number v-model="smartAnalysisBatchSize" :min="1" :max="20" />
                </el-form-item>
                <el-form-item label="批次完成后">
                  <el-select v-model="smartAfterAnalysisBatch">
                    <el-option label="自动继续下一批" value="auto_continue" />
                    <el-option label="等待我继续" value="wait_for_user" />
                  </el-select>
                </el-form-item>
                <el-form-item label="Codex 交接">
                  <el-select v-model="smartCodexHandoff">
                    <el-option label="自动交接" value="auto" />
                    <el-option label="等待手动交接" value="manual" />
                  </el-select>
                </el-form-item>
              </div>
              <div class="capture-options">
                <el-switch v-model="smartAnalyzeAllCandidates" />
                <span>达到投递目标后继续分析已形成的候选岗位</span>
              </div>
              <div class="capture-options">
                <el-switch v-model="smartStopAfterCurrentBatch" />
                <span>当前 Codex 批次完成后等待我继续</span>
              </div>
            </template>
          </el-form>
          <div class="capture-config-summary">
            <span>搜索词 {{ smartSelectedKeywords.length }} 个</span>
            <span>城市 {{ smartSelectedCities.length }} 个</span>
            <span>目标 {{ smartCandidateTargetCount }} 个岗位</span>
            <span v-if="smartWorkflowRunId">当前 Run <code>{{ smartWorkflowRunId }}</code></span>
          </div>
          <div class="platform-actions capture-submit">
            <el-button
              type="primary"
              :loading="smartCaptureControlLoading"
              :disabled="smartCaptureControlLoading || !smartCaptureCanStart"
              @click="startSmartCapture"
            >
              开始智能采集
            </el-button>
            <el-button v-if="smartCaptureRunning" :loading="smartCaptureControlLoading" @click="pauseCurrentSmartCapture">
              暂停采集
            </el-button>
            <el-button v-if="smartCaptureResumable" type="success" :loading="smartCaptureControlLoading" @click="resumeCurrentSmartCapture">
              继续采集
            </el-button>
            <el-button
              v-if="currentSmartCapture && !['completed', 'stopped', 'failed'].includes(currentSmartCapture.status)"
              type="danger"
              plain
              :loading="smartCaptureControlLoading"
              @click="stopCurrentSmartCapture"
            >
              停止采集
            </el-button>
          </div>
          <el-alert
            v-if="currentSmartCapture"
            type="info"
            :closable="false"
            show-icon
            :title="`岗位采集：${currentSmartCapture.status}`"
            :description="currentSmartCapture.message"
          />
          <el-alert v-if="smartWorkflowRun" type="info" :closable="false" show-icon :title="`智能采集：${smartWorkflowRun.status}`" :description="smartWorkflowRun.next_action_reason" />
          <div v-if="smartWorkflowRun" class="smart-guidance-actions">
            <h2>本 Run 分析指导 v{{ smartWorkflowRun.completion_contract?.analysis_guidance?.version || 1 }}</h2>
            <el-input v-model="smartAnalysisGuidance" type="textarea" :rows="2" placeholder="补充要求会保存并作用于后续批次" />
            <el-button @click="saveSmartAnalysisGuidance">保存分析指导</el-button>
          </div>
          <section v-if="smartWorkflowRun" class="smart-context-inspector">
            <div class="panel-title-row">
              <div><p class="panel-eyebrow">Context</p><h2>Context 快照与预算</h2></div>
            </div>
            <el-collapse>
              <el-collapse-item name="smart-context">
                <template #title>查看 Context 快照与预算</template>
                <div class="smart-context-controls">
                  <el-select v-model="smartContextChannel" @change="loadSmartContextSnapshot()">
                    <el-option label="搜索 Context" value="deep_job_search" />
                    <el-option label="分析 Shared Base" value="candidate_analysis" />
                  </el-select>
                  <el-button @click="loadSmartContextSnapshot(true)">查看 Context</el-button>
                </div>
                <template v-if="smartContextSnapshot">
                  <el-alert
                    :title="`任务通道：${smartContextSnapshot.channel}；后端快照 ${smartContextSnapshot.status === 'ready' ? '可用' : '被预算阻断'}`"
                    :description="`实际纳入 ${smartContextSnapshot.context_characters} 字，估算 ${smartContextSnapshot.estimated_tokens} token；软预算 ${smartContextSnapshot.soft_budget_characters} 字。`"
                    :type="smartContextSnapshot.status === 'ready' ? 'success' : 'warning'"
                    :closable="false"
                    show-icon
                  />
                  <el-table :data="smartContextSnapshot.sections" class="smart-context-table" max-height="300">
                    <el-table-column prop="section_id" label="Section" min-width="180" />
                    <el-table-column prop="section_type" label="类型" min-width="130" />
                    <el-table-column label="纳入" width="90"><template #default="scope"><el-tag :type="scope.row.included ? 'success' : 'info'">{{ scope.row.included ? '已纳入' : '未注入' }}</el-tag></template></el-table-column>
                    <el-table-column prop="character_count" label="字符量" width="100" />
                    <el-table-column prop="estimated_tokens" label="估算 token" width="120" />
                    <el-table-column prop="source" label="来源" min-width="140" />
                    <el-table-column prop="source_version" label="版本" width="90" />
                    <el-table-column prop="exclusion_reason" label="排除说明" min-width="240" />
                  </el-table>
                  <el-collapse class="smart-context-content">
                    <el-collapse-item v-for="section in smartContextSnapshot.sections" :key="section.section_id" :name="section.section_id">
                      <template #title>{{ section.section_id }}：{{ section.included ? "实际注入内容" : "未注入说明" }}</template>
                      <pre>{{ section.included ? JSON.stringify(section.content, null, 2) : section.exclusion_reason }}</pre>
                    </el-collapse-item>
                  </el-collapse>
                </template>
                <el-empty v-else description="当前通道尚未生成 Context 快照。" :image-size="64" />
              </el-collapse-item>
            </el-collapse>
          </section>
        </section>
      </el-tab-pane>
      <el-tab-pane label="自定义采集" name="custom">
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
        <el-button class="more-filter-button" text type="primary" @click="moreFiltersOpen = !moreFiltersOpen">
          {{ moreFiltersOpen ? "收起更多筛选条件" : "更多筛选条件" }}
        </el-button>
        <el-collapse-transition>
          <div v-show="moreFiltersOpen" class="more-filters">
            <el-form-item label="岗位筛选策略">
              <div class="strategy-filter-actions">
                <el-select v-model="filterStrategyId" clearable placeholder="选择岗位筛选策略">
                  <el-option
                    v-for="item in strategiesStore.filters"
                    :key="item.id"
                    :label="item.name"
                    :value="item.id"
                  />
                </el-select>
                <el-button type="primary" plain @click="selectBossFiltersFromStrategy">智能选择</el-button>
              </div>
              <p class="secondary-text strategy-filter-hint">
                自动填充策略中可映射到 BOSS 的工作类型、薪资、经验、学历、公司规模和融资阶段。
              </p>
            </el-form-item>
            <el-form-item label="求职类型">
              <el-radio-group v-model="bossFilters.jobType" @change="handleJobTypeChange">
                <el-radio value="">不限</el-radio>
                <el-radio value="1901">全职</el-radio>
                <el-radio value="1903">兼职</el-radio>
              </el-radio-group>
            </el-form-item>

            <el-form-item v-if="bossFilters.jobType === '1901'" label="薪资待遇">
              <el-radio-group v-model="bossFilters.salary">
                <el-radio value="">不限</el-radio>
                <el-radio v-for="option in fullTimeSalaryOptions" :key="option.value" :value="option.value">
                  {{ option.label }}
                </el-radio>
              </el-radio-group>
            </el-form-item>

            <template v-if="bossFilters.jobType === '1903'">
              <el-form-item label="结算方式">
                <el-checkbox-group v-model="bossFilters.payType" class="filter-checkboxes">
                  <el-checkbox v-for="option in payTypeOptions" :key="option.value" :value="option.value">
                    {{ option.label }}
                  </el-checkbox>
                </el-checkbox-group>
              </el-form-item>
              <el-form-item label="兼职时间">
                <el-checkbox-group v-model="bossFilters.partTime" class="filter-checkboxes">
                  <el-checkbox v-for="option in partTimeOptions" :key="option.value" :value="option.value">
                    {{ option.label }}
                  </el-checkbox>
                </el-checkbox-group>
              </el-form-item>
            </template>

            <el-form-item label="工作经验">
              <el-checkbox-group v-model="bossFilters.experience" class="filter-checkboxes">
                <el-checkbox v-for="option in experienceOptions" :key="option.value" :value="option.value">
                  {{ option.label }}
                </el-checkbox>
              </el-checkbox-group>
            </el-form-item>
            <el-form-item label="学历要求">
              <el-checkbox-group v-model="bossFilters.degree" class="filter-checkboxes">
                <el-checkbox v-for="option in degreeOptions" :key="option.value" :value="option.value">
                  {{ option.label }}
                </el-checkbox>
              </el-checkbox-group>
            </el-form-item>
            <el-form-item label="公司规模">
              <el-checkbox-group v-model="bossFilters.scale" class="filter-checkboxes">
                <el-checkbox v-for="option in scaleOptions" :key="option.value" :value="option.value">
                  {{ option.label }}
                </el-checkbox>
              </el-checkbox-group>
            </el-form-item>
            <el-form-item label="融资阶段">
              <el-checkbox-group v-model="bossFilters.stage" class="filter-checkboxes">
                <el-checkbox v-for="option in stageOptions" :key="option.value" :value="option.value">
                  {{ option.label }}
                </el-checkbox>
              </el-checkbox-group>
            </el-form-item>
          </div>
        </el-collapse-transition>
        <div class="capture-options">
          <el-switch v-model="form.preferCurrentPage" />
          <span>优先采集当前 BOSS 搜索页；当前页无效时自动定位</span>
        </div>
        <div class="capture-options">
          <el-switch v-model="form.includeDetails" />
          <span>采集列表后，自动获取全部岗位详情（不建议）</span>
        </div>
        <p class="secondary-text">当前会使用已选岗位筛选策略，在详情采集前统一排除黑名单、外包公司及冷却中的公司/岗位。</p>
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
        <el-button
          v-if="!customCaptureRunning"
          type="primary"
          size="large"
          :disabled="!captureStore.status?.running"
          :loading="captureStore.capturing"
          @click="captureJobs"
        >
          开始采集
        </el-button>
        <el-button
          v-else
          type="danger"
          size="large"
          :loading="captureStore.stoppingCapture"
          :disabled="Boolean(captureStore.task?.stop_requested)"
          @click="stopCapture"
        >
          {{ captureStore.task?.stop_requested ? "正在停止" : "停止采集" }}
        </el-button>
        <el-button
          v-if="currentTaskIsCustomCapture && captureStore.task?.status === 'completed'"
          type="success"
          size="large"
          :disabled="!canContinueCapture"
          :loading="captureStore.capturing"
          @click="continueCapture"
        >
          继续下滑采集 {{ form.pages }} 页
        </el-button>
          </div>
          <el-alert
        v-if="currentTaskIsCustomCapture && captureStore.task?.status === 'completed'"
        class="capture-result-alert"
        :type="['list_stopped', 'details_stopped'].includes(captureStore.task.stage) ? 'warning' : 'success'"
        :title="captureStore.task.message"
        :description="`累计下滑 ${captureStore.task.total_pages_loaded ?? 0} 页；最近一次新增 ${captureStore.task.last_added_jobs ?? 0} 个岗位。`"
        :closable="false"
        show-icon
          />
        </section>
      </el-tab-pane>
    </el-tabs>

    <section v-if="captureProgressVisible" class="page-panel capture-progress-panel">
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

    <section v-if="workflowDisplayJobs.length" class="page-panel">
      <div class="panel-title-row">
        <div><p class="panel-eyebrow">Jobs</p><h2>岗位列表</h2></div>
        <div class="capture-metrics">
          <span>累计 {{ workflowDisplayJobs.length }}</span>
          <span>新岗位 {{ captureOverview.freshJobs }}</span>
          <span>历史岗位 {{ captureOverview.duplicateJobs }}</span>
          <span>已选择 {{ detailStatusSummary.selected }}</span>
          <span>筛选通过 {{ detailStatusSummary.passed }}</span>
          <span>待判断 {{ detailStatusSummary.review }}</span>
          <span>已排除 {{ detailStatusSummary.rejected }}</span>
          <span>建议投递 {{ detailStatusSummary.recommended }}</span>
          <span>详情完成 {{ detailStatusSummary.completed }}</span>
          <span>失败 {{ detailStatusSummary.failed }}</span>
        </div>
      </div>

      <div v-if="!smartWorkflowRun" class="detail-actions">
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
      <div v-else class="detail-actions">
        <el-button
          type="primary"
          plain
          :disabled="taskRunning || smartWorkflowRun.status !== 'waiting_for_user' || !recommendationStrategyId || !selectedWorkflowJobIds.length"
          :loading="workflowStore.loading"
          @click="createManualCodexBatch"
        >
          交给 Codex 批量生成建议（{{ selectedWorkflowJobIds.length }}）
        </el-button>
      </div>

      <el-table
        ref="jobsTable"
        :data="sortedJobs"
        :row-key="jobDisplayKey"
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
        <el-table-column prop="boss_name" label="公司" min-width="190">
          <template #default="scope">
            <span>{{ scope.row.boss_name }}</span>
            <el-tag v-if="scope.row.is_outsourcing_company" type="warning" size="small">外包</el-tag>
            <el-tag v-if="scope.row.is_blacklisted" type="danger" size="small">黑名单</el-tag>
            <el-tag v-if="scope.row.application_status === 'communicating'" type="success" size="small">沟通中</el-tag>
          </template>
        </el-table-column>
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

        <el-table-column label="操作" width="100" fixed="right">
          <template #default="scope">
            <el-button link type="primary" @click.stop="openDetail(scope.row)">详情
            </el-button>
            <el-button
              link
              type="primary"
              :loading="executorStore.openingJobId === scope.row.job_id"
              @click.stop="openInDedicatedBrowser(scope.row)"
            >
              打开
            </el-button>
            <el-button
              v-if="!smartWorkflowRun && scope.row.detail_status === 'completed' && !scope.row.delivery_evaluation"
              link
              type="warning"
              :disabled="taskRunning || !scope.row.job_id || !recommendationStrategyId"
              :loading="captureStore.suggesting"
              @click.stop="evaluateSingleDelivery(scope.row)"
            >
              获取投递详情
            </el-button>
            <el-button
              v-else-if="!smartWorkflowRun && scope.row.detail_status !== 'completed'"
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

      <div v-if="captureStore.task?.jobs_path" class="capture-output-path">
        <span>列表文件</span><code>{{ captureStore.task?.jobs_path }}</code>
        <template v-if="captureStore.task?.details_path">
          <span>详情文件</span><code>{{ captureStore.task?.details_path }}</code>
        </template>
      </div>
    </section>

    <section v-if="smartWorkflowRun" class="page-panel smart-analysis-panel">
      <div class="panel-title-row">
        <div><p class="panel-eyebrow">Analysis Queue</p><h2>待分析岗位队列</h2></div>
        <el-button @click="loadSmartAnalysisItems(true)">刷新</el-button>
      </div>
      <p class="secondary-text">本批进入 Codex 前的岗位与筛选依据均来自当前智能 Run 的持久化记录。</p>
      <el-table :data="smartAnalysisItems" max-height="420">
        <el-table-column label="岗位" min-width="180"><template #default="scope">{{ scope.row.job.title }}</template></el-table-column>
        <el-table-column label="公司" min-width="140"><template #default="scope">{{ scope.row.job.company }}</template></el-table-column>
        <el-table-column label="薪资 / 城市" min-width="140"><template #default="scope">{{ scope.row.job.salary }} / {{ scope.row.job.city }}</template></el-table-column>
        <el-table-column label="来源" min-width="140"><template #default="scope">{{ scope.row.job.discovery_keyword }} · 深度 {{ scope.row.job.discovery_depth }}</template></el-table-column>
        <el-table-column label="初筛" min-width="160"><template #default="scope">{{ scope.row.job.filter_result }}：{{ scope.row.job.filter_reasons.join("；") }}</template></el-table-column>
        <el-table-column label="JD / Item" min-width="130"><template #default="scope">{{ scope.row.job.jd_status }} / {{ scope.row.status }}</template></el-table-column>
        <el-table-column label="操作" width="100"><template #default="scope"><el-button link @click="viewSmartAnalysisItem(scope.row)">查看详情</el-button></template></el-table-column>
      </el-table>
    </section>

    <section v-if="smartWorkflowRun" class="page-panel smart-analysis-panel">
      <div class="panel-title-row"><div><p class="panel-eyebrow">Analysis Result</p><h2>分析结果</h2></div></div>
      <el-alert v-if="smartActiveAnalysisItem" type="info" :closable="false" :title="`正在分析：${smartActiveAnalysisItem.job.title} · ${smartActiveAnalysisItem.job.company}`" />
      <el-empty v-if="!smartAnalysisResultItems.length" description="当前尚无已保存分析结果；进行中会在 Codex 分析工作台显示正在处理的岗位。" />
      <div v-for="item in smartAnalysisResultItems" :key="item.workflow_task_id" class="smart-analysis-result-card">
        <div class="panel-title-row"><strong>{{ item.job.title }} · {{ item.job.company }}</strong><el-tag>{{ item.analysis_result.decision }}</el-tag></div>
        <p>硬条件：{{ listSmartText(item.analysis_result.hard_requirements) || "未提供" }}</p>
        <p>匹配点：{{ listSmartText(item.analysis_result.strengths) || "未提供" }}</p>
        <p>差距：{{ listSmartText(item.analysis_result.gaps) || "无" }}</p>
        <p>风险：{{ listSmartText(item.analysis_result.risks) || "无" }}</p>
        <p>证据：JD {{ listSmartText(item.analysis_result.jd_evidence) || "未提供" }}；候选人 {{ listSmartText(item.analysis_result.candidate_evidence) || "未提供" }}</p>
        <div class="smart-analysis-actions">
          <el-button link @click="viewSmartAnalysisItem(item)">查看详情</el-button>
          <el-button size="small" @click="saveSmartAnalysisFeedback(item, 'expected')">符合预期</el-button>
          <el-select v-model="smartFeedbackReason" size="small" class="smart-feedback-reason">
            <el-option label="技术方向不符合" value="technical_direction" />
            <el-option label="薪资" value="salary" />
            <el-option label="公司" value="company" />
            <el-option label="年限/学历" value="experience_or_education" />
            <el-option label="工作制" value="work_schedule" />
            <el-option label="地点" value="location" />
            <el-option label="AI 对 JD 理解错误" value="jd_understanding" />
            <el-option label="AI 对我的经历理解错误" value="candidate_understanding" />
            <el-option label="其他" value="other" />
          </el-select>
          <el-button size="small" @click="saveSmartAnalysisFeedback(item, 'unexpected')">不符合预期</el-button>
        </div>
      </div>
    </section>

    <section v-if="smartWorkflowRun?.status === 'completed'" class="page-panel smart-analysis-panel">
      <div class="panel-title-row"><div><p class="panel-eyebrow">Completion</p><h2>本轮完成结果</h2></div></div>
      <p>本轮共分析 {{ smartWorkflowRun.progress.recommend_count + smartWorkflowRun.progress.review_count + smartWorkflowRun.progress.reject_count }}；Recommend {{ smartWorkflowRun.progress.recommend_count }}；Review {{ smartWorkflowRun.progress.review_count }}；Reject {{ smartWorkflowRun.progress.reject_count }}；进入待确认 {{ smartPendingReviewItems.length }}。</p>
      <div v-for="item in smartPendingReviewItems" :key="`completed-${item.workflow_task_id}`" class="smart-analysis-result-card">
        <strong>{{ item.job.title }} · {{ item.job.company }}</strong>
        <p>{{ listSmartText(item.analysis_result.reasons) }}</p>
        <p>风险：{{ listSmartText(item.analysis_result.risks) || "无" }}</p>
        <el-button link @click="router.push({ name: 'fine-job-review' })">去待确认</el-button>
      </div>
    </section>

    <el-dialog v-model="smartAnalysisDetailOpen" title="Workflow 分析 Item 详情" width="80%">
      <pre>{{ JSON.stringify({ item: smartSelectedAnalysisItem, context: smartSelectedAnalysisContext }, null, 2) }}</pre>
    </el-dialog>

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
            <el-tag v-if="currentDetailJob.is_outsourcing_company" type="warning">外包公司</el-tag>
            <el-tag v-if="currentDetailJob.is_blacklisted" type="danger">公司黑名单</el-tag>
            <el-tag v-if="currentDetailJob.application_status === 'communicating'" type="success">沟通中</el-tag>
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
.session-summary,
.capture-overview-panel,
.smart-capture-panel,
.smart-workflow-panel,
.smart-workflow-operations,
.smart-workflow-details {
  display: grid;
  gap: 16px;
}

.capture-condition-tabs {
  display: grid;
  gap: 8px;
}

.capture-config-summary {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  color: var(--el-text-color-secondary);
}

.capture-overview-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 12px;
}

.capture-overview-grid > div,
.workflow-run-grid > div {
  display: grid;
  gap: 4px;
  padding: 12px;
  border-radius: 8px;
  background: var(--el-fill-color-lighter);
}

.workflow-run-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
  gap: 12px;
}

.smart-search-planner {
  display: grid;
  gap: 8px;
}

.smart-search-planner p {
  margin: 0;
}

.smart-workflow-actions {
  margin-top: 12px;
}

.smart-run-restore,
.smart-guidance-actions {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.smart-run-restore .el-input {
  max-width: 420px;
}

.smart-context-channel {
  width: 180px;
}

.smart-guidance-actions {
  display: grid;
  padding-top: 12px;
  border-top: 1px solid var(--line);
}

.smart-guidance-actions h2 {
  margin: 0;
}

.smart-context-inspector {
  display: grid;
  gap: 12px;
  border-top: 1px solid var(--line);
}

.smart-context-controls {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
}

.smart-context-controls .el-select {
  width: 180px;
}

.smart-context-table {
  width: 100%;
  margin-top: 12px;
}

.smart-context-content {
  margin-top: 12px;
}

.smart-context-content pre {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
}

.smart-analysis-panel,
.smart-analysis-result-card {
  display: grid;
  gap: 12px;
}

.smart-analysis-result-card {
  padding: 14px;
  border: 1px solid var(--line);
  border-radius: 8px;
}

.smart-analysis-result-card p {
  margin: 0;
  white-space: pre-wrap;
}

.smart-analysis-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.smart-feedback-reason {
  width: 190px;
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

.more-filter-button {
  margin: 4px 0 12px;
}

.more-filters {
  display: grid;
  gap: 4px;
  margin-bottom: 12px;
  padding: 16px;
  border-radius: 8px;
  background: var(--el-fill-color-lighter);
}

.more-filters :deep(.el-form-item) {
  margin-bottom: 12px;
}

.more-filters :deep(.el-form-item:last-child) {
  margin-bottom: 0;
}

.filter-checkboxes {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 16px;
}

.filter-checkboxes :deep(.el-checkbox) {
  margin-right: 0;
}

.strategy-filter-actions {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.strategy-filter-actions .el-select {
  min-width: 260px;
}

.strategy-filter-hint {
  width: 100%;
  margin: 8px 0 0;
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
