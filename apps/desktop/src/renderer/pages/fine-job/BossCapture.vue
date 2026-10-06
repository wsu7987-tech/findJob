<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { useRouter } from "vue-router";

import { useFineJobSmartCaptureStore } from "@/stores/fineJobSmartCapture";
import { useFineJobBossCaptureStore } from "@/stores/fineJobBossCapture";
import { useFineJobBossExecutorStore } from "@/stores/fineJobBossExecutor";
import { useFineJobCodexStore } from "@/stores/fineJobCodex";
import { useFineJobPlatformSessionsStore } from "@/stores/fineJobPlatformSessions";
import { useFineJobStrategiesStore } from "@/stores/fineJobStrategies";
import { useFineJobWorkflowRunStore } from "@/stores/fineJobWorkflowRun";
import SmartCaptureConfigForm from "@/components/fine-job/SmartCaptureConfigForm.vue";
import {
  resubmitSmartCaptureCodexSubmit,
  retrySmartCaptureCodexHandoff,
  triggerSmartCaptureCodexHandoff
} from "@/services/workflowCodexHandoff";
import type {
  FineJobCollectionProgressScope,
  FineJobBossCapturedJob,
  FineJobBossCaptureTask,
  FineJobWorkflowAnalysisItem,
  FineJobWorkflowContextSnapshot,
  FineJobSmartCapture,
  FineJobSmartCaptureHandoff,
  FineJobSearchPlannerSummary,
  FineJobWorkflowRun
} from "@/types";
import { ApiError, api, collectionStarts } from "@/services/api";
import {
  toSmartCaptureRequest,
  validateSmartCaptureExecutionConfig,
  type SmartCaptureExecutionConfig
} from "@/services/smartCaptureExecutionConfig";

const sharedSmartCapture = useFineJobSmartCaptureStore();
const startState = collectionStarts.state;
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
const formOptionsLoading = ref(false);
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
const smartMinDepth = ref(1);
const smartScrollBatchSize = ref(3);
const smartMaxDepth = ref(20);
const smartLowYieldStreakLimit = ref(3);
const smartContextChannel = ref("deep_job_search");
const smartContextSnapshot = ref<FineJobWorkflowContextSnapshot | null>(null);
const inspectedContextSnapshot = ref<FineJobWorkflowContextSnapshot | null>(null);
const smartWorkflowRunId = ref("");
const smartRunLookupId = ref("");
const inspectedHistoricalRun = ref<FineJobWorkflowRun | null>(null);
const smartWorkflowJobs = ref<FineJobBossCapturedJob[]>([]);
const smartCaptureTaskRefsKey = ref("");
const smartCaptureTaskIds = ref<string[]>([]);
// 关联父批次继续随 Workflow SSE 更新，Smart Capture 主状态由 Smart Capture SSE 提供。
const smartCaptureTask = ref<FineJobBossCaptureTask | null>(null);
const smartAnalysisItems = ref<FineJobWorkflowAnalysisItem[]>([]);
const smartSelectedAnalysisItem = ref<FineJobWorkflowAnalysisItem | null>(null);
const smartSelectedAnalysisContext = ref<Record<string, unknown> | null>(null);
const smartAnalysisDetailOpen = ref(false);
const smartFeedbackReason = ref("technical_direction");
const smartControlLoading = ref(false);
const smartCaptureControlLoading = ref(false);
// 当前采集身份只接受 Smart Capture current API，历史 Workflow 查询不会改写这里。
const currentSmartCapture = ref<FineJobSmartCapture | null>(null);
const smartAnalysisHandoff = ref<FineJobSmartCaptureHandoff | null>(null);
let smartCaptureRefreshGeneration = 0;
let unsubscribeSmartCapture: (() => void) | null = null;
let bossCapturePageActive = false;
let smartContextRequestGeneration = 0;
let smartAnalysisRequestGeneration = 0;
let inspectedContextRequestGeneration = 0;
const smartContextLoading = ref(false);
const smartContextError = ref("");
const smartAnalysisLoading = ref(false);
const smartAnalysisError = ref("");
const inspectedContextLoading = ref(false);
const inspectedContextError = ref("");
const analysisDetailLoading = ref(false);
const analysisDetailError = ref("");
let analysisDetailGeneration = 0;
const customSubmitting = ref(false);
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
// linked 父任务只由当前 Smart Capture 的关联 ID 确定，历史查询不会写入此镜像。
const linkedParentWorkflowRun = computed(() => smartWorkflowRun.value);
const linkedParentStartedAt = computed(() => {
  const run = linkedParentWorkflowRun.value;
  const smartCaptureId = currentSmartCapture.value?.smart_capture_id;
  if (!run || !smartCaptureId) return "—";
  const relation = run.children?.find((child) => child.smart_capture_id === smartCaptureId);
  return relation?.started_at || relation?.created_at || run.updated_at || "—";
});
const linkedParentTerminal = computed(() => {
  const status = linkedParentWorkflowRun.value?.status;
  return !status || ["cancelled", "completed", "completed_with_errors", "failed"].includes(status);
});
// 父镜像控制依据父层 control_state，避免用 legacy status 覆盖子任务卡点语义。
const linkedParentCanPause = computed(() => Boolean(
  linkedParentWorkflowRun.value
    && !linkedParentTerminal.value
    && linkedParentWorkflowRun.value.control_state === "active"
));
const linkedParentCanResume = computed(() => Boolean(
  linkedParentWorkflowRun.value
    && linkedParentWorkflowRun.value.control_state === "paused"
    && linkedParentWorkflowRun.value.control_cause === "parent_pause"
));
const linkedParentCanCancel = computed(() => Boolean(
  linkedParentWorkflowRun.value
    && !linkedParentTerminal.value
    && linkedParentWorkflowRun.value.control_state
));
// Workflow 进入这些终态后，采集槽位已释放，页面允许重新开始或切换采集模式。
const smartWorkflowTerminalStatuses = [
  "completed",
  "completed_with_errors",
  "cancelled",
  "failed",
  "stopped"
];
const smartCaptureTerminal = computed(() => Boolean(
  currentSmartCapture.value
  && smartWorkflowTerminalStatuses.includes(currentSmartCapture.value.status)
));
const smartCaptureRunning = computed(() =>
  Boolean(currentSmartCapture.value?.capabilities?.pause)
);
const smartCaptureStartable = computed(() =>
  Boolean(
    currentSmartCapture.value?.capabilities?.start
      && !currentSmartCapture.value.workflow_run_id
  )
);
const smartCaptureResumable = computed(() =>
  Boolean(currentSmartCapture.value?.capabilities?.resume)
);
const smartCaptureRetryable = computed(() =>
  Boolean(currentSmartCapture.value?.capabilities?.retry)
);
const smartCaptureCanStart = computed(() =>
  !currentSmartCapture.value || smartWorkflowTerminalStatuses.includes(currentSmartCapture.value.status)
);
const smartManualAnalysisAvailable = computed(() =>
  Boolean(
    currentSmartCapture.value
      && ["waiting_for_user", "completed"].includes(currentSmartCapture.value.status)
  )
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
const parseJsonRecord = (value: unknown): Record<string, unknown> => {
  if (typeof value !== "string" || !value) return {};
  try {
    const parsed = JSON.parse(value) as unknown;
    return parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? parsed as Record<string, unknown>
      : {};
  } catch {
    return {};
  }
};
// Planner 与 Prefetch 直接读取 Smart Capture snapshot，确保 independent 不依赖父 Workflow。
const smartSearchPlanner = computed<FineJobSearchPlannerSummary | null>(() => {
  const combinations = currentSmartCapture.value?.search_combinations ?? [];
  if (!combinations.length) return null;
  const current = combinations.find((item) => item.status === "running") ?? combinations[combinations.length - 1];
  const filters = parseJsonRecord(current.platform_filters_json);
  return {
    current_scope: { keyword: current.keyword, city: current.city },
    current_combination: {
      id: current.id,
      sequence: current.sequence,
      status: current.status,
      platform_filters: Object.fromEntries(Object.entries(filters).map(([key, value]) => [key, String(value)])),
      platform_filter_labels: Object.entries(filters).map(([key, value]) => `${key}: ${String(value)}`),
      metrics: {
        jobs_seen: Number(current.jobs_seen ?? 0),
        run_fresh_jobs: Number(current.run_fresh_jobs ?? 0),
        historical_duplicates: Number(current.historical_duplicates ?? 0),
        strategy_reject: Number(current.strategy_reject ?? 0),
        qualified_fresh_jobs: Number(current.qualified_fresh_jobs ?? 0)
      }
    },
    last_transition: {
      action: current.transition_action,
      reason: current.transition_reason,
      selected_axis: current.selected_axis,
      evidence: parseJsonRecord(current.evidence_json),
      stop_reason: current.stop_reason
    },
    pending_combination_count: combinations.filter((item) => item.status === "pending").length
  };
});
const smartAnalysisDecisionCounts = computed(() => {
  const counts = { recommend_count: 0, review_count: 0, reject_count: 0 };
  smartAnalysisResultItems.value.forEach((item) => {
    const decision = String(item.analysis_result.decision ?? "");
    const key = `${decision}_count` as keyof typeof counts;
    if (key in counts) counts[key] += 1;
  });
  return counts;
});
const smartCompletionProgress = computed(() => {
  const deliveryTarget = currentSmartCapture.value?.execution_config?.delivery_target;
  if (!deliveryTarget || typeof deliveryTarget !== "object") return null;
  const config = deliveryTarget as Record<string, unknown>;
  if (!config.enabled) return null;
  const recommendTarget = Number(config.recommend_target ?? 0);
  const reviewTarget = config.review_target === null || config.review_target === undefined
    ? null
    : Number(config.review_target);
  const recommendCurrent = smartAnalysisDecisionCounts.value.recommend_count;
  const reviewCurrent = smartAnalysisDecisionCounts.value.review_count;
  const targetMode = config.target_mode === "any" ? "any" : "all";
  const recommendReached = recommendCurrent >= recommendTarget;
  const reviewReached = reviewTarget === null ? null : reviewCurrent >= reviewTarget;
  return {
    recommend: {
      current: recommendCurrent,
      target: recommendTarget,
      remaining: Math.max(0, recommendTarget - recommendCurrent),
      reached: recommendReached
    },
    review: {
      current: reviewCurrent,
      target: reviewTarget,
      remaining: reviewTarget === null ? null : Math.max(0, reviewTarget - reviewCurrent),
      reached: reviewReached
    },
    target_mode: targetMode,
    target_reached: targetMode === "any"
      ? recommendReached || reviewReached === true
      : recommendReached && reviewReached !== false
  };
});
const smartDetailProgress = computed(() => {
  const capture = currentSmartCapture.value;
  const progress = capture?.progress ?? {};
  const combinations = capture?.search_combinations ?? [];
  const currentCombination = combinations.find((item) => item.status === "running") ?? combinations[combinations.length - 1];
  const totals = combinations.reduce((result, item) => ({
    jobsSeen: result.jobsSeen + Number(item.jobs_seen ?? 0),
    freshJobs: result.freshJobs + Number(item.run_fresh_jobs ?? 0),
    duplicateJobs: result.duplicateJobs + Number(item.historical_duplicates ?? 0)
  }), { jobsSeen: 0, freshJobs: 0, duplicateJobs: 0 });
  return {
    keyword: currentCombination?.keyword ?? "等待开始",
    city: currentCombination?.city ?? "—",
    depth: Number(progress.current ?? progress.progress_current ?? 0),
    batchCount: capture?.batches.length ?? 0,
    jobsSeen: totals.jobsSeen || capture?.jobs.length || Number(progress.jobs_collected ?? 0),
    freshJobs: totals.freshJobs,
    duplicateJobs: totals.duplicateJobs,
    candidates: capture?.candidate_pool?.length ?? 0,
    jdCompleted: Number(progress.details_completed ?? 0),
    jdTotal: capture?.candidate_pool?.length ?? 0,
    ...smartAnalysisDecisionCounts.value
  };
});
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
const smartCaptureStatusText = computed(() => {
  const capture = currentSmartCapture.value;
  if (!capture) return "";
  const progressData = capture.progress;
  const current = Number(progressData.current ?? progressData.progress_current ?? 0);
  const total = Number(progressData.total ?? progressData.progress_total ?? 0);
  const progress = total > 0 ? `${current}/${total}` : "";
  return [
    `采集任务：${capture.status}`,
    capture.stage ? `阶段：${capture.stage}` : "",
    progress ? `进度：${progress}` : "",
    capture.waiting_reason || capture.message || ""
  ].filter(Boolean).join("；");
});
const hasCurrentSmartWorkflowCodexSession = computed(() => Boolean(
  smartAnalysisHandoff.value?.codex_session_ref
    && codexStore.status === "running"
    && smartAnalysisHandoff.value.codex_session_ref === codexStore.sessionRef
));
const hasEndedSmartWorkflowCodexSession = computed(() => Boolean(
  smartAnalysisHandoff.value?.codex_session_ref?.startsWith("runtime:")
    && !hasCurrentSmartWorkflowCodexSession.value
));
const canResubmitSmartWorkflowCodex = computed(() => {
  const handoff = smartAnalysisHandoff.value;
  return Boolean(
    handoff?.attempt_status === "prompt_written"
      && handoff.codex_session_ref === codexStore.sessionRef
      && codexStore.status === "running"
  );
});
const canRetrySmartWorkflowCodex = computed(() => Boolean(
  smartAnalysisHandoff.value?.attempt_status === "prompt_written"
    && smartAnalysisHandoff.value.retry_available
));
const smartWorkflowCodexEntry = computed(() => {
  const capture = currentSmartCapture.value;
  const handoff = smartAnalysisHandoff.value;
  if (!capture || !handoff) return null;
  const handoffMode = smartCodexHandoff.value;
  if (handoffMode === "auto") {
    return ["prompt_written", "started"].includes(handoff.attempt_status) || handoff.codex_session_ref
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
  const handoff = smartAnalysisHandoff.value;
  if (!currentSmartCapture.value || !handoff || smartCodexHandoff.value !== "auto") return "";
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
const displayingCurrentSmartCapture = computed(() =>
  activeCaptureConditionTab.value === "smart" && Boolean(currentSmartCapture.value)
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
  // 公共岗位区跟随当前页签选择 owner，Smart 与 custom 数据不交叉合并。
  if (displayingCurrentSmartCapture.value && currentSmartCapture.value) {
    return mergeDisplayJobs(
      smartWorkflowJobs.value,
      smartCaptureTask.value?.jobs ?? [],
      currentSmartCapture.value.jobs ?? []
    );
  }
  return activeCaptureConditionTab.value === "custom" && currentTaskIsCustomCapture.value ? captureStore.task?.jobs ?? [] : [];
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

const smartExecutionConfig = computed<SmartCaptureExecutionConfig>({
  get: () => ({
    filter_strategy_id: smartFilterStrategyId.value || "",
    allowed_search_keywords: [...smartSelectedKeywords.value],
    allowed_cities: [...smartSelectedCities.value],
    filters: { ...selectedBossFilters.value },
    candidate_target_count: smartCandidateTargetCount.value,
    prefer_current_page: form.preferCurrentPage,
    delivery_target_enabled: smartDeliveryTargetEnabled.value,
    recommendation_strategy_id: smartRecommendationStrategyId.value || "",
    recommend_target: smartRecommendTarget.value,
    review_target_enabled: smartEnableReviewTarget.value,
    review_target: smartReviewTarget.value,
    target_mode: smartTargetMode.value,
    analyze_all_candidates: smartAnalyzeAllCandidates.value,
    stop_after_current_batch: smartStopAfterCurrentBatch.value,
    analysis_batch_size: smartAnalysisBatchSize.value,
    execution_policy_after_analysis_batch: smartAfterAnalysisBatch.value,
    execution_policy_codex_handoff: smartCodexHandoff.value,
    analysis_guidance: smartAnalysisGuidance.value,
    codex_model: smartCodexModel.value,
    codex_reasoning_effort: smartCodexReasoningEffort.value,
    context_soft_budget_characters: smartContextSoftBudgetCharacters.value,
    min_depth: smartMinDepth.value,
    scroll_batch_size: smartScrollBatchSize.value,
    max_depth: smartMaxDepth.value,
    low_yield_streak_limit: smartLowYieldStreakLimit.value
  }),
  set: (value) => {
    smartFilterStrategyId.value = value.filter_strategy_id || null;
    smartSelectedKeywords.value = [...value.allowed_search_keywords];
    smartSelectedCities.value = [...value.allowed_cities];
    smartCandidateTargetCount.value = value.candidate_target_count;
    form.preferCurrentPage = value.prefer_current_page;
    smartDeliveryTargetEnabled.value = value.delivery_target_enabled;
    smartRecommendationStrategyId.value = value.recommendation_strategy_id || null;
    smartRecommendTarget.value = value.recommend_target;
    smartEnableReviewTarget.value = value.review_target_enabled;
    smartReviewTarget.value = value.review_target;
    smartTargetMode.value = value.target_mode;
    smartAnalyzeAllCandidates.value = value.analyze_all_candidates;
    smartStopAfterCurrentBatch.value = value.stop_after_current_batch;
    smartAnalysisBatchSize.value = value.analysis_batch_size;
    smartAfterAnalysisBatch.value = value.execution_policy_after_analysis_batch;
    smartCodexHandoff.value = value.execution_policy_codex_handoff;
    smartAnalysisGuidance.value = value.analysis_guidance;
    smartCodexModel.value = value.codex_model;
    smartCodexReasoningEffort.value = value.codex_reasoning_effort;
    smartContextSoftBudgetCharacters.value = value.context_soft_budget_characters;
    smartMinDepth.value = value.min_depth;
    smartScrollBatchSize.value = value.scroll_batch_size;
    smartMaxDepth.value = value.max_depth;
    smartLowYieldStreakLimit.value = value.low_yield_streak_limit;
  }
});
const smartConfigValidation = computed(() => validateSmartCaptureExecutionConfig(smartExecutionConfig.value));

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
const displayedTask = computed(() => activeCaptureConditionTab.value === "smart"
  ? (currentSmartCapture.value?.current_batch as FineJobBossCaptureTask | null) ?? null
  : currentTaskIsCustomCapture.value ? captureStore.task : null);
const hasDisplayedTask = computed(() => activeCaptureConditionTab.value === "smart" ? Boolean(currentSmartCapture.value) : currentTaskIsCustomCapture.value);
const displayedProgress = computed<FineJobCollectionProgressScope | null>(() => {
  if (activeCaptureConditionTab.value === "smart") {
    const capture = currentSmartCapture.value;
    if (!capture?.collection_progress) return null;
    if (capture.stage === "collecting_jd" || (capture.collection_progress.formal_jd && !["capturing", "resume_requested"].includes(capture.stage) && ["paused", "pausing", "stopped", "failed", "completed", "interrupted"].includes(capture.status))) return capture.collection_progress.formal_jd;
    if (["waiting_codex", "analyzing", "analyzing_candidates"].includes(capture.stage) || capture.waiting_reason === "codex") return null;
    return capture.collection_progress.list;
  }
  const task = currentTaskIsCustomCapture.value ? captureStore.task : null;
  if (!task) return null;
  const details = task.stage.startsWith("details");
  if (details && task.detail_phase_job_ids) {
    const jobs = task.jobs.filter((job) => task.detail_phase_job_ids?.includes(job.job_id || ""));
    const succeeded = jobs.filter((job) => job.detail_status === "completed").length;
    const failed = jobs.filter((job) => job.detail_status === "failed").length;
    return { scope_id: task.detail_phase_id || task.id, unit: "job", stage: task.stage, total: task.detail_phase_job_ids.length, processed: succeeded + failed, succeeded, failed, pending: null, running: null, cancelled: null, active_job: null };
  }
  return { scope_id: task.list_phase_id || task.id, unit: details ? "job" : "page", stage: task.stage,
    total: details ? task.progress_total : task.list_phase_id ? task.pages : null,
    processed: details ? task.progress_current : task.list_phase_processed ?? Math.max(0, (task.total_pages_loaded ?? 0) - (task.list_phase_baseline ?? 0)),
    succeeded: details ? task.details_completed : null, failed: details ? task.details_failed : null,
    pending: null, running: null, cancelled: null, active_job: null };
});
const captureProgressVisible = hasDisplayedTask;
const canContinueCapture = computed(() => Boolean(
  captureStore.task?.status === "completed" && currentTaskIsCustomCapture.value && captureStore.task.continuation_available
));
const progressPercentage = computed(() => {
  const progress = displayedProgress.value;
  return progress?.total && progress.total > 0 ? Math.min(100, Math.round(progress.processed / progress.total * 100)) : null;
});
const displayStatus = computed(() => {
  if (activeCaptureConditionTab.value === "smart") return currentSmartCapture.value?.status ?? "";
  const task = captureStore.task;
  if (!task) return "";
  if (task.stage.endsWith("paused")) return "已暂停";
  if (task.stage.endsWith("stopped")) return "已停止";
  if (task.stop_requested && ["queued", "running"].includes(task.status)) return "正在停止";
  return captureStatusLabel(task.status);
});
const startPhaseLabel = computed(() => (({ validating: "正在校验参数", checking_browser: "检查浏览器", opening_browser: "打开浏览器", checking_login: "检查登录", dispatching: "准备采集", finished: "启动结果待确认" } as Record<string, string>)[startState.receipt?.phase || ""] || "正在准备启动"));
const checkStartResult = async () => {
  const receipt = await collectionStarts.check();
  if (receipt?.status !== "started") return;
  if (receipt.result_kind === "custom") {
    try { captureStore.setTask(await collectionStarts.loadResult<FineJobBossCaptureTask>(receipt)); }
    catch { /* 查询错误由启动区域展示，保留已知结果。 */ }
  } else await refreshCurrentSmartCapture();
};
watch(() => startState.receipt, (receipt) => {
  if (receipt?.status === "started") void checkStartResult();
});
watch(() => [activeCaptureConditionTab.value, activeCaptureConditionTab.value === "smart" ? currentSmartCapture.value?.smart_capture_id : captureStore.task?.id], () => {
  selectedJobIds.value = []; selectedJobId.value = null; detailDrawerOpen.value = false;
  smartAnalysisDetailOpen.value = false; analysisDetailGeneration += 1; analysisDetailLoading.value = false;
  jobsTable.value?.clearSelection();
});
const preCaptureEstimate = computed(() => {
  if (!form.includeDetails) return "";
  const expectedJobs = form.pages * 30;
  return `${formatDuration(expectedJobs * 25)}～${formatDuration(expectedJobs * 55)}`;
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
  const task = displayedTask.value;
  const smart = activeCaptureConditionTab.value === "smart" ? currentSmartCapture.value : null;
  const jobs = workflowDisplayJobs.value;
  return {
    status: displayStatus.value,
    stage: smart?.stage || captureStageLabel(task?.stage),
    message: smart?.message || task?.message || "还没有开始岗位采集。",
    keyword: smart ? smartDetailProgress.value.keyword : task?.keyword || form.keyword || "—",
    city: smart ? smartDetailProgress.value.city : task?.city || form.city || "—",
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
  // 页面重新进入时重建父镜像订阅，继续展示已经取得的任务结果。
  bossCapturePageActive = true;
  workflowStore.stopPolling();
  workflowStore.setRun(null);
  const currentCapturePromise = refreshCurrentSmartCapture();
  unsubscribeSmartCapture = sharedSmartCapture.subscribe((capture) => { void applyCurrentSmartCapture(capture); });
  void sharedSmartCapture.startRealtime();
  void collectionStarts.restore().then(checkStartResult).catch(() => undefined);
  formOptionsLoading.value = true;
  const configPromise = (async () => {
    try {
      const config = await api.getConfig({ timeoutMs: 15000 });
      if (!bossCapturePageActive) return;
      smartCodexModel.value = config.codex_model || "";
      smartCodexReasoningEffort.value = (config.codex_reasoning_effort as typeof smartCodexReasoningEffort.value) || "medium";
      try {
        const result = await api.listCodexModels(config.codex_cli_path || "codex", { timeoutMs: 45000 });
        if (!bossCapturePageActive) return;
        smartCodexModels.value = mergeSmartCodexModels(result.models, config.codex_model);
      } catch (value) {
        smartCodexModels.value = mergeSmartCodexModels(loadCachedSmartCodexModels(), config.codex_model);
        smartCodexModelLoadError.value = value instanceof Error ? value.message : "Codex 模型目录加载失败，可直接输入模型 ID。";
      }
    } catch (value) {
      smartCodexModels.value = mergeSmartCodexModels(loadCachedSmartCodexModels(), "");
      smartCodexModelLoadError.value = value instanceof Error ? value.message : "Codex 配置加载失败，可直接输入模型 ID。";
    }
  })();
  try {
  await Promise.all([
    captureStore.loadStatus(),
    captureStore.loadCities(),
    strategiesStore.load(),
    platformStore.load(),
    currentCapturePromise
  ]);
  if (!bossCapturePageActive) return;
  const initialFilter = strategiesStore.filters.find((item) => item.enabled) ?? strategiesStore.filters[0];
  const initialRecommendation = strategiesStore.recommendations.find((item) => item.enabled) ?? strategiesStore.recommendations[0];
  filterStrategyId.value = initialFilter?.id ?? null;
  smartFilterStrategyId.value = initialFilter?.id ?? null;
  smartSelectedKeywords.value = [...(initialFilter?.search_keywords ?? [])];
  smartSelectedCities.value = [...(initialFilter?.cities ?? [])];
  recommendationStrategyId.value = initialRecommendation?.id ?? null;
  smartRecommendationStrategyId.value = initialRecommendation?.id ?? null;
  // 进入页面时优先展示正在执行的任务；普通采集任务没有智能 Run，因此落到自定义采集。
  if (taskRunning.value && !currentTaskBelongsToSmartWorkflow.value) {
    activeCaptureConditionTab.value = "custom";
  }
  form.keyword = initialFilter?.search_keywords[0] || initialFilter?.title_include_any[0] || "";
  form.city = initialFilter?.cities[0] || "";
  // 自定义任务使用既有轮询；智能采集使用 current pointer 和 Smart Capture SSE。
  if (!currentTaskIsSmartCapture.value) captureStore.resumePolling();
  await configPromise;
  } finally { formOptionsLoading.value = false; }
});

onBeforeUnmount(() => {
  bossCapturePageActive = false;
  captureStore.stopPolling();
  workflowStore.stopPolling();
  workflowStore.setRun(null);
  smartCaptureRefreshGeneration += 1;
  smartContextRequestGeneration += 1;
  smartAnalysisRequestGeneration += 1;
  inspectedContextRequestGeneration += 1;
  unsubscribeSmartCapture?.();
  unsubscribeSmartCapture = null;
  analysisDetailGeneration += 1;
});

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

const smartCaptureRefreshKey = (capture: FineJobSmartCapture | null) => capture
  ? [capture.status, capture.stage, capture.current_batch_id ?? "", capture.waiting_reason].join(":")
  : "";

const applyCurrentSmartCapture = async (capture: FineJobSmartCapture | null) => {
  if (!bossCapturePageActive) return;
  if (!capture) {
    smartContextRequestGeneration += 1;
    smartAnalysisRequestGeneration += 1;
    smartContextLoading.value = false; smartAnalysisLoading.value = false;
    smartContextError.value = ""; smartAnalysisError.value = "";
    smartControlGeneration += 1; smartCaptureControlLoading.value = false;
    currentSmartCapture.value = null;
    smartWorkflowJobs.value = [];
    smartWorkflowRunId.value = "";
    smartCaptureTaskRefsKey.value = "";
    smartCaptureTaskIds.value = [];
    smartCaptureTask.value = null;
    smartContextSnapshot.value = null;
    smartAnalysisHandoff.value = null;
    smartAnalysisItems.value = [];
    smartSelectedAnalysisItem.value = null;
    smartSelectedAnalysisContext.value = null;
    workflowStore.setRun(null);
    if (captureStore.task?.capture_source === "smart") captureStore.clearTask();
    return;
  }
  const previous = currentSmartCapture.value;
  if (
    previous?.smart_capture_id === capture.smart_capture_id
    && capture.state_version < previous.state_version
  ) {
    return;
  }
  const currentIdentityChanged = previous?.smart_capture_id !== capture.smart_capture_id;
  const linkedParentChanged = previous?.workflow_run_id !== capture.workflow_run_id;
  const semanticStateChanged = smartCaptureRefreshKey(previous) !== smartCaptureRefreshKey(capture);
  if (linkedParentChanged) {
    // 切换 current 的 linked parent 前先关闭旧 SSE，避免旧父任务迟到快照污染新父镜像。
    workflowStore.stopPolling();
  }
  currentSmartCapture.value = capture;
  if (smartWorkflowTerminalStatuses.includes(capture.status)) {
    // 终态快照保留结果展示，同时立即释放页面级详情订阅。
  }
  smartWorkflowRunId.value = capture.workflow_run_id ?? "";
  smartWorkflowJobs.value = [...(capture.jobs ?? [])];
  if (captureStore.task?.capture_source === "smart") {
    captureStore.clearTask();
  }
  if (currentIdentityChanged) {
    smartControlGeneration += 1; smartCaptureControlLoading.value = false;
    smartContextRequestGeneration += 1; smartAnalysisRequestGeneration += 1;
    smartContextSnapshot.value = null; smartAnalysisItems.value = []; smartAnalysisHandoff.value = null;
    smartCaptureTask.value = null; smartSelectedAnalysisContext.value = null;
    smartContextError.value = ""; smartAnalysisError.value = "";
  }
  if (currentIdentityChanged || semanticStateChanged) {
    // 进度版本会高频增长；Context 与 Analysis 只在业务阶段变化时刷新。
    void Promise.all([loadSmartContextSnapshot(), loadSmartAnalysisItems()]);
  }
  // 上述异步读取期间 current 可能已经切换；旧快照不能继续操作或清空新 current 的父镜像。
  if (!bossCapturePageActive || currentSmartCapture.value?.smart_capture_id !== capture.smart_capture_id) return;
  if (!capture.workflow_run_id) {
    workflowStore.setRun(null);
    return;
  }
  if (!linkedParentChanged && smartWorkflowRun.value) return;
  const linkedRun = capture.workflow_run ?? await api.getFineJobWorkflowRun(capture.workflow_run_id);
  // 关联父镜像只由当前 Smart Capture 的 workflow_run_id 建立。
  if (!bossCapturePageActive || currentSmartCapture.value?.smart_capture_id !== capture.smart_capture_id) return;
  workflowStore.setRun(linkedRun);
  if (
    !["cancelled", "completed", "completed_with_errors", "failed"].includes(linkedRun.status)
    && !workflowStore.pollingActive
  ) {
    workflowStore.startPolling();
  }
};

const refreshCurrentSmartCapture = async () => {
  try {
    const capture = await sharedSmartCapture.refreshCurrent();
    await applyCurrentSmartCapture(capture);
    return capture;
  } catch { return null; }
};

const showCollectionStartBlocked = async (value: unknown, label: string) => {
  if (value instanceof ApiError && value.errorCategory === "COLLECTION_TASK_ACTIVE") {
    await ElMessageBox.alert(value.message, `无法开始${label}`, { type: "warning", confirmButtonText: "知道了" });
  } else ElMessage.error(value instanceof Error ? value.message : `启动${label}失败`);
};

const ensureCollectionStartAvailable = async (label: string) => {
  const { active_task: task } = await api.getFineJobActiveCollectionTask();
  if (!task) return true;
  await ElMessageBox.alert(task.message || "当前采集尚未结束，请先处理当前任务。", `无法开始${label}`, { type: "warning", confirmButtonText: "知道了" });
  return false;
};

const startSmartCapture = async () => {
  const config = smartExecutionConfig.value;
  const validation = smartConfigValidation.value;
  if (!validation.isValid) {
    ElMessage.warning(Object.values(validation.errors).join("；"));
    return;
  }
  try {
    if (smartCaptureControlLoading.value || startState.intent) return;
    smartCaptureControlLoading.value = true;
    if (!await ensureCollectionStartAvailable("智能采集")) return;
    await api.createFineJobSmartCapture(
      toSmartCaptureRequest(config)
    );
    // 创建响应不直接决定页面 current；创建完成后重新读取服务端 current pointer。
    const capture = await refreshCurrentSmartCapture();
    if (!capture) throw new Error("启动已受理，状态同步失败，请刷新当前任务。");
    ElMessage.success("智能采集任务已启动");
  } catch (errorValue) {
    await showCollectionStartBlocked(errorValue, "智能采集");
  } finally {
    smartCaptureControlLoading.value = false;
  }
};

let smartControlGeneration = 0;
const applySmartControlResult = async (capture: FineJobSmartCapture) => {
  if (!bossCapturePageActive || currentSmartCapture.value?.smart_capture_id !== capture.smart_capture_id) return;
  sharedSmartCapture.apply(capture);
  await applyCurrentSmartCapture(capture);
};
const startCurrentSmartCapture = async () => {
  const capture = currentSmartCapture.value;
  if (!capture?.capabilities.start || capture.workflow_run_id) return;
  const generation = ++smartControlGeneration;
  const ownsControl = () => bossCapturePageActive && generation === smartControlGeneration && currentSmartCapture.value?.smart_capture_id === capture.smart_capture_id;
  try {
    smartCaptureControlLoading.value = true;
    await applySmartControlResult(await api.startFineJobSmartCapture(capture.smart_capture_id));
    if (ownsControl()) ElMessage.success("岗位采集任务已启动");
  } catch (errorValue) {
    if (!ownsControl()) return;
    ElMessage.error(errorValue instanceof Error ? errorValue.message : "启动岗位采集失败");
  } finally {
    if (ownsControl()) smartCaptureControlLoading.value = false;
  }
};

const pauseCurrentSmartCapture = async () => {
  const capture = currentSmartCapture.value;
  if (!capture) return;
  const generation = ++smartControlGeneration;
  const ownsControl = () => bossCapturePageActive && generation === smartControlGeneration && currentSmartCapture.value?.smart_capture_id === capture.smart_capture_id;
  try {
    smartCaptureControlLoading.value = true;
    await applySmartControlResult(await api.pauseFineJobSmartCapture(capture.smart_capture_id));
    if (ownsControl()) ElMessage.success("正在安全暂停岗位采集任务");
  } catch (errorValue) {
    if (!ownsControl()) return;
    await refreshCurrentSmartCapture();
    ElMessage.error(errorValue instanceof Error ? errorValue.message : "暂停岗位采集失败");
  } finally {
    if (ownsControl()) smartCaptureControlLoading.value = false;
  }
};

const resumeCurrentSmartCapture = async () => {
  const capture = currentSmartCapture.value;
  if (!capture) return;
  const generation = ++smartControlGeneration;
  const ownsControl = () => bossCapturePageActive && generation === smartControlGeneration && currentSmartCapture.value?.smart_capture_id === capture.smart_capture_id;
  try {
    smartCaptureControlLoading.value = true;
    await applySmartControlResult(await api.resumeFineJobSmartCapture(capture.smart_capture_id));
    if (ownsControl()) ElMessage.success("岗位采集任务已继续");
  } catch (errorValue) {
    if (!ownsControl()) return;
    ElMessage.error(errorValue instanceof Error ? errorValue.message : "继续岗位采集失败");
  } finally {
    if (ownsControl()) smartCaptureControlLoading.value = false;
  }
};

const retryCurrentSmartCapture = async () => {
  const capture = currentSmartCapture.value;
  if (!capture?.capabilities.retry) return;
  const generation = ++smartControlGeneration;
  const ownsControl = () => bossCapturePageActive && generation === smartControlGeneration && currentSmartCapture.value?.smart_capture_id === capture.smart_capture_id;
  try {
    smartCaptureControlLoading.value = true;
    await applySmartControlResult(await api.retryFineJobSmartCapture(capture.smart_capture_id));
    if (ownsControl()) ElMessage.success("岗位采集任务已重试");
  } catch (errorValue) {
    if (!ownsControl()) return;
    ElMessage.error(errorValue instanceof Error ? errorValue.message : "重试岗位采集失败");
  } finally {
    if (ownsControl()) smartCaptureControlLoading.value = false;
  }
};

const stopCurrentSmartCapture = async () => {
  const capture = currentSmartCapture.value;
  if (!capture) return;
  const generation = ++smartControlGeneration;
  const ownsControl = () => bossCapturePageActive && generation === smartControlGeneration && currentSmartCapture.value?.smart_capture_id === capture.smart_capture_id;
  try {
    smartCaptureControlLoading.value = true;
    await applySmartControlResult(await api.stopFineJobSmartCapture(capture.smart_capture_id));
    if (ownsControl()) ElMessage.success("岗位采集任务已停止");
  } catch (errorValue) {
    if (!ownsControl()) return;
    await refreshCurrentSmartCapture();
    ElMessage.error(errorValue instanceof Error ? errorValue.message : "停止岗位采集失败");
  } finally {
    if (ownsControl()) smartCaptureControlLoading.value = false;
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
  const capture = currentSmartCapture.value;
  if (!capture) return;
  try {
    await applyCurrentSmartCapture(await api.updateFineJobSmartCaptureAnalysisGuidance(
      capture.smart_capture_id,
      smartAnalysisGuidance.value
    ));
    ElMessage.success("本次智能采集分析指导已保存");
  } catch (errorValue) {
    ElMessage.error(errorValue instanceof Error ? errorValue.message : "保存本 Run 分析指导失败");
  }
};

let historicalRunGeneration = 0;
const historicalRunLoading = ref(false);
const restoreSmartWorkflowRun = async () => {
  const workflowRunId = smartRunLookupId.value.trim();
  if (!workflowRunId) return;
  const generation = ++historicalRunGeneration;
  historicalRunLoading.value = true;
  try {
    // 历史 Run 只读；它不会替换当前 Smart Capture 或关联父镜像。
    const run = await api.getFineJobWorkflowRun(workflowRunId);
    if (!bossCapturePageActive || generation !== historicalRunGeneration || workflowRunId !== smartRunLookupId.value.trim()) return;
    if (!run || run.workflow_type !== "deep_job_search") {
      ElMessage.warning("该 Run 不是智能岗位采集任务");
      return;
    }
    inspectedHistoricalRun.value = run;
    await loadInspectedContextSnapshot();
    ElMessage.success("已加载历史 Workflow Run");
  } catch (errorValue) {
    if (bossCapturePageActive && generation === historicalRunGeneration) ElMessage.error(errorValue instanceof Error ? errorValue.message : "恢复智能采集 Run 失败");
  } finally { if (generation === historicalRunGeneration) historicalRunLoading.value = false; }
};

const listSmartText = (value: unknown) => Array.isArray(value)
  ? value.map((item) => String(item)).join("；")
  : "";

const loadSmartContextSnapshot = async (showError = false) => {
  const capture = currentSmartCapture.value;
  if (!capture) return;
  const requestGeneration = ++smartContextRequestGeneration;
  const smartCaptureId = capture.smart_capture_id;
  const channel = smartContextChannel.value;
  smartContextLoading.value = true;
  smartContextError.value = "";
  try {
    const snapshot = await api.getFineJobSmartCaptureContextSnapshot(smartCaptureId, channel);
    if (
      requestGeneration !== smartContextRequestGeneration
      || currentSmartCapture.value?.smart_capture_id !== smartCaptureId
      || smartContextChannel.value !== channel
    ) return;
    smartContextSnapshot.value = snapshot;
  } catch (errorValue) {
    if (
      requestGeneration !== smartContextRequestGeneration
      || currentSmartCapture.value?.smart_capture_id !== smartCaptureId
    ) return;
    smartContextError.value = errorValue instanceof Error ? errorValue.message : "区域读取失败。";
    if (showError) {
      ElMessage.error(errorValue instanceof Error ? errorValue.message : "加载 Context 快照失败");
    }
  } finally { if (requestGeneration === smartContextRequestGeneration) smartContextLoading.value = false; }
};

const loadSmartAnalysisItems = async (showError = false) => {
  const capture = currentSmartCapture.value;
  if (!capture) return;
  const requestGeneration = ++smartAnalysisRequestGeneration;
  const smartCaptureId = capture.smart_capture_id;
  smartAnalysisLoading.value = true;
  smartAnalysisError.value = "";
  try {
    const snapshot = await api.listFineJobSmartCaptureAnalysisItems(smartCaptureId);
    if (
      requestGeneration !== smartAnalysisRequestGeneration
      || currentSmartCapture.value?.smart_capture_id !== smartCaptureId
    ) return;
    smartAnalysisItems.value = snapshot.items;
    smartAnalysisHandoff.value = snapshot.handoff ?? snapshot.analysis_handoff ?? null;
  } catch (errorValue) {
    if (
      requestGeneration !== smartAnalysisRequestGeneration
      || currentSmartCapture.value?.smart_capture_id !== smartCaptureId
    ) return;
    smartAnalysisError.value = errorValue instanceof Error ? errorValue.message : "分析队列读取失败。";
    if (showError) {
      ElMessage.error(errorValue instanceof Error ? errorValue.message : "加载分析队列失败");
    }
  } finally { if (requestGeneration === smartAnalysisRequestGeneration) smartAnalysisLoading.value = false; }
};

const viewSmartAnalysisItem = async (item: FineJobWorkflowAnalysisItem) => {
  const capture = currentSmartCapture.value;
  if (!capture) return;
  const generation = ++analysisDetailGeneration;
  smartSelectedAnalysisItem.value = item;
  smartSelectedAnalysisContext.value = null;
  smartAnalysisDetailOpen.value = true;
  analysisDetailLoading.value = true;
  analysisDetailError.value = "";
  try {
    const context = await api.getFineJobSmartCaptureAnalysisItemContext(capture.smart_capture_id, item.workflow_task_id);
    if (generation === analysisDetailGeneration && currentSmartCapture.value?.smart_capture_id === capture.smart_capture_id) smartSelectedAnalysisContext.value = context;
  } catch (error) {
    if (generation === analysisDetailGeneration) analysisDetailError.value = (error as Error).message;
  } finally { if (generation === analysisDetailGeneration) analysisDetailLoading.value = false; }
};

const saveSmartAnalysisFeedback = async (
  item: FineJobWorkflowAnalysisItem,
  sentiment: "expected" | "unexpected"
) => {
  const capture = currentSmartCapture.value;
  if (!capture) return;
  try {
    await api.saveFineJobSmartCaptureAnalysisFeedback(capture.smart_capture_id, item.workflow_task_id, {
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
  const capture = currentSmartCapture.value;
  if (!capture) return;
  if (action !== "view") {
    const snapshot = await api.getFineJobSmartCaptureAnalysisSnapshot(capture.smart_capture_id);
    const result = await triggerSmartCaptureCodexHandoff(snapshot, codexStore, "manual");
    result.status === "submitted"
      ? ElMessage.success(result.message)
      : ElMessage.warning(result.message);
    return;
  }
  await router.push({
    name: "fine-job-codex",
    query: {
      task: "smart-capture-analysis",
      smart_capture_id: capture.smart_capture_id,
      ...(capture.workflow_run_id ? { parent_workflow_run_id: capture.workflow_run_id } : {}),
      workflow_action: action
    }
  });
};

const resubmitSmartWorkflowCodex = async () => {
  const capture = currentSmartCapture.value;
  if (!capture) return;
  const snapshot = await api.getFineJobSmartCaptureAnalysisSnapshot(capture.smart_capture_id);
  const result = await resubmitSmartCaptureCodexSubmit(snapshot, codexStore);
  result.status === "enter_submitted"
    ? ElMessage.success(result.message)
    : ElMessage.warning(result.message);
};

const retrySmartWorkflowCodex = async () => {
  const capture = currentSmartCapture.value;
  if (!capture) return;
  const snapshot = await api.getFineJobSmartCaptureAnalysisSnapshot(capture.smart_capture_id);
  const result = await retrySmartCaptureCodexHandoff(snapshot, codexStore);
  result.status === "submitted"
    ? ElMessage.success(result.message)
    : ElMessage.warning(result.message);
};

const createManualCodexBatch = async () => {
  const capture = currentSmartCapture.value;
  if (!capture) {
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
    const snapshot = await api.createFineJobSmartCaptureManualAnalysisBatch(capture.smart_capture_id, {
      recommendation_strategy_id: strategyId,
      job_ids: selectedWorkflowJobIds.value,
      analysis_batch_size: smartAnalysisBatchSize.value,
      codex_model: smartCodexModel.value || undefined,
      codex_reasoning_effort: smartCodexReasoningEffort.value
    });
    const result = await triggerSmartCaptureCodexHandoff(snapshot, codexStore, "manual");
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
      if (!bossCapturePageActive || currentSmartCapture.value?.workflow_run_id !== run.workflow_run_id) return;
      smartWorkflowJobs.value = mergeDisplayJobs(smartWorkflowJobs.value, result.items);
      smartCaptureTaskRefsKey.value = refsKey;
    } catch {
      // 当前批次仍由采集任务快照展示，汇总接口暂不可用时不影响采集继续执行。
    }
  }
  const captureTask = [...(run?.tasks ?? [])]
    .reverse()
    .find((task) => task.task_type === "deep_job_search" && task.operation_ref_id);
  if (!captureTask?.operation_ref_id) {
    smartCaptureTask.value = null;
    return;
  }
  // child 已暂停、中断或终态时，旧进程内 BOSS task 不再是状态来源，避免重启后的 stale task ID 触发 404。
  if (!["running", "pausing"].includes(currentSmartCapture.value?.status ?? "")) {
    smartCaptureTask.value = null;
    return;
  }
  if (captureTask.operation_ref_id === smartCaptureTask.value?.id) return;
  try {
    // 新批次首次出现时只读取一次，用于补足 SSE 首帧尚未包含的岗位列表。
    const refreshedTask = await api.getFineJobBossCaptureTask(captureTask.operation_ref_id);
    if (!bossCapturePageActive || currentSmartCapture.value?.workflow_run_id !== run.workflow_run_id) return;
    smartCaptureTask.value = refreshedTask;
    if (refreshedTask.jobs?.length) {
      smartWorkflowJobs.value = mergeDisplayJobs(smartWorkflowJobs.value, refreshedTask.jobs);
    }
  } catch {
    // 迟到失败只清理原任务的补读结果。
    if (bossCapturePageActive && currentSmartCapture.value?.workflow_run_id === run.workflow_run_id) smartCaptureTask.value = null;
  }
};

const loadInspectedContextSnapshot = async (showError = false) => {
  const run = inspectedHistoricalRun.value;
  if (!run) return;
  const requestGeneration = ++inspectedContextRequestGeneration;
  const workflowRunId = run.workflow_run_id;
  const channel = smartContextChannel.value;
  inspectedContextLoading.value = true;
  inspectedContextError.value = "";
  try {
    const snapshot = await api.getFineJobWorkflowContextSnapshot(workflowRunId, channel);
    if (
      requestGeneration !== inspectedContextRequestGeneration
      || inspectedHistoricalRun.value?.workflow_run_id !== workflowRunId
      || smartContextChannel.value !== channel
    ) return;
    inspectedContextSnapshot.value = snapshot;
  } catch (errorValue) {
    if (
      requestGeneration !== inspectedContextRequestGeneration
      || inspectedHistoricalRun.value?.workflow_run_id !== workflowRunId
    ) return;
    inspectedContextError.value = errorValue instanceof Error ? errorValue.message : "区域读取失败。";
    if (showError) {
      ElMessage.error(errorValue instanceof Error ? errorValue.message : "加载历史 Context 快照失败");
    }
  } finally { if (requestGeneration === inspectedContextRequestGeneration) inspectedContextLoading.value = false; }
};

const loadSelectedContextSnapshots = async () => {
  await Promise.all([
    loadSmartContextSnapshot(),
    loadInspectedContextSnapshot()
  ]);
};

watch(smartRunLookupId, (workflowRunId) => {
  if (!workflowRunId.trim()) {
    inspectedContextRequestGeneration += 1;
    inspectedHistoricalRun.value = null;
    inspectedContextSnapshot.value = null;
  }
});

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
  if (!ensureSearchInput() || customSubmitting.value || startState.intent) return;
  customSubmitting.value = true;
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

    <el-tabs v-model="activeCaptureConditionTab">
      <el-tab-pane label="智能采集" name="smart" />
      <el-tab-pane label="自定义采集" name="custom" />
    </el-tabs>
    <!-- 智能采集配置置于对应 Tab 首位，用户先完成目标与策略配置，再查看任务结果。 -->
    <section
      v-if="activeCaptureConditionTab === 'smart'"
      v-loading="formOptionsLoading || strategiesStore.loading"
      class="capture-condition-tabs smart-capture-first"
    >
      <el-alert v-if="strategiesStore.error" :title="strategiesStore.error" type="error" :closable="false" />
      <el-button v-if="strategiesStore.error" @click="strategiesStore.load()">刷新策略选项</el-button>
      <section class="page-panel smart-capture-panel">
        <div class="panel-title-row">
          <div>
            <p class="panel-eyebrow">Smart Capture</p>
            <h2>智能采集</h2>
          </div>
          <span class="secondary-text">按岗位筛选策略准备搜索范围，再交给现有采集流程执行。</span>
        </div>
        <SmartCaptureConfigForm
          v-model="smartExecutionConfig"
          :filter-strategies="strategiesStore.filters"
          :recommendation-strategies="strategiesStore.recommendations"
          :codex-models="smartCodexModels"
          :codex-model-load-error="smartCodexModelLoadError"
          :show-analysis-guidance="false"
          :show-context-budget="false"
        />
        <div class="capture-config-summary">
          <span>搜索词 {{ smartSelectedKeywords.length }} 个</span>
          <span>城市 {{ smartSelectedCities.length }} 个</span>
          <span>目标 {{ smartCandidateTargetCount }} 个岗位</span>
          <span v-if="smartWorkflowRunId">当前 Run <code>{{ smartWorkflowRunId }}</code></span>
        </div>
        <div class="platform-actions capture-submit">
          <el-button
            v-if="smartCaptureStartable"
            type="primary"
            :loading="smartCaptureControlLoading"
            @click="startCurrentSmartCapture"
          >
            启动当前采集
          </el-button>
          <el-button
            v-else
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
          <el-button v-if="smartCaptureResumable" :disabled="Boolean(startState.intent)" type="success" :loading="smartCaptureControlLoading" @click="resumeCurrentSmartCapture">
            继续采集
          </el-button>
          <el-button v-if="smartCaptureRetryable" :disabled="Boolean(startState.intent)" type="warning" :loading="smartCaptureControlLoading" @click="retryCurrentSmartCapture">
            重试采集
          </el-button>
          <el-button
            v-if="currentSmartCapture && !smartWorkflowTerminalStatuses.includes(currentSmartCapture.status)"
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
        <el-alert
          v-if="hasEndedSmartWorkflowCodexSession"
          title="原 Codex 会话不可恢复；下一批进入 waiting_codex 后可重新交给 Codex 分析。"
          type="info"
          :closable="false"
          show-icon
        />
        <div v-if="currentSmartCapture" class="platform-actions">
          <el-button v-if="smartWorkflowCodexEntry" type="primary" @click="openSmartWorkflowCodex(smartWorkflowCodexEntry.action)">{{ smartWorkflowCodexEntry.label }}</el-button>
          <el-button v-if="canResubmitSmartWorkflowCodex" @click="resubmitSmartWorkflowCodex">再次提交</el-button>
          <el-button v-if="canRetrySmartWorkflowCodex" type="warning" @click="retrySmartWorkflowCodex">重新交接</el-button>
          <el-tag v-if="smartWorkflowHandoffStatus" type="info">{{ smartWorkflowHandoffStatus }}</el-tag>
        </div>
      </section>
    </section>
    <section v-if="startState.intent || startState.submitting || startState.error" class="page-panel">
      <p>{{ startState.submitting ? startPhaseLabel : startState.intent ? "启动结果待确认" : startState.error }}</p>
      <p v-if="startState.intent && startState.error">{{ startState.error }}</p>
      <el-button v-if="startState.intent" :loading="startState.checking" @click="checkStartResult">检查启动结果</el-button>
      <el-button v-if="startState.intent" :disabled="startState.submitting || startState.checking" @click="collectionStarts.resolve">确认结束未开始的请求</el-button>
    </section>
    <section v-if="activeCaptureConditionTab === 'smart' && (sharedSmartCapture.loading || sharedSmartCapture.error)" class="page-panel">
      <p v-if="sharedSmartCapture.loading">正在同步智能采集状态…</p>
      <el-alert v-if="sharedSmartCapture.error" :title="sharedSmartCapture.error" type="warning" :closable="false" />
      <el-button v-if="sharedSmartCapture.error" @click="refreshCurrentSmartCapture">重新同步</el-button>
    </section>
    <el-alert v-if="activeCaptureConditionTab === 'custom' && captureStore.syncError" :title="captureStore.syncError" type="warning" :closable="false" />

    <section v-if="hasDisplayedTask" class="page-panel capture-overview-panel">
      <div class="panel-title-row">
        <div>
          <p class="panel-eyebrow">Capture Overview</p>
          <h2>采集概览</h2>
        </div>
        <el-tag :type="(activeCaptureConditionTab === 'smart' ? currentSmartCapture?.status : displayedTask?.status) === 'failed' ? 'danger' : 'info'">
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

    <section v-if="captureProgressVisible" class="page-panel capture-progress-panel">
      <div class="panel-title-row"><h2>采集进度</h2><el-tag>{{ captureOverview.status }} · {{ captureOverview.stage }}</el-tag></div>
      <el-progress v-if="progressPercentage !== null" :percentage="progressPercentage" />
      <p>{{ captureOverview.message }}</p>
      <p v-if="displayedProgress">本批已处理 {{ displayedProgress.processed }} / {{ displayedProgress.total ?? '总量待确认' }} {{ displayedProgress.unit === 'page' ? '页' : '项' }}<span v-if="displayedProgress.unit === 'job'">；成功  {{ displayedProgress.succeeded }}，失败 {{ displayedProgress.failed }}</span></p>
      <p v-else>当前阶段进度暂不可用。</p>
      <p v-if="displayedProgress?.active_job">正在处理：{{ displayedProgress.active_job.title || displayedProgress.active_job.id }}</p>
      <p v-if="activeCaptureConditionTab === 'smart' && currentSmartCapture?.collection_progress?.prefetch">下一批详情已处理 {{ currentSmartCapture.collection_progress.prefetch.processed }} / {{ currentSmartCapture.collection_progress.prefetch.total }}；成功 {{ currentSmartCapture.collection_progress.prefetch.succeeded }}，失败 {{ currentSmartCapture.collection_progress.prefetch.failed }}</p>
    </section>

    <section v-if="activeCaptureConditionTab === 'smart' && currentSmartCapture" class="page-panel smart-workflow-panel">
      <div class="panel-title-row">
        <div><p class="panel-eyebrow">Smart Capture</p><h2>{{ smartCaptureTerminal ? "最近任务结果" : "智能采集执行详情" }}</h2></div>
        <el-tag :type="currentSmartCapture.status === 'waiting_for_user' ? 'warning' : 'info'">
          {{ currentSmartCapture.status }} / {{ currentSmartCapture.stage }}
        </el-tag>
      </div>
      <p v-if="smartCaptureTerminal" class="secondary-text">该任务已经结束，当前区域展示最终状态；采集控制已关闭，仍可处理结果分析。</p>
      <p>{{ currentSmartCapture.message || smartWorkflowRun?.next_action_reason }}</p>
      <div class="workflow-run-grid">
        <div><span class="secondary-text">当前搜索</span><strong>{{ smartDetailProgress.keyword }} / {{ smartDetailProgress.city }}</strong></div>
        <div><span class="secondary-text">搜索深度 / 批次</span><strong>{{ smartDetailProgress.depth }} / {{ smartDetailProgress.batchCount }}</strong></div>
        <div><span class="secondary-text">岗位：已见 / Fresh / 重复</span><strong>{{ smartDetailProgress.jobsSeen }} / {{ smartDetailProgress.freshJobs }} / {{ smartDetailProgress.duplicateJobs }}</strong></div>
        <div><span class="secondary-text">初筛候选</span><strong>{{ smartDetailProgress.candidates }}</strong></div>
        <div><span class="secondary-text">JD：完成 / 已建</span><strong>{{ smartDetailProgress.jdCompleted }} / {{ smartDetailProgress.jdTotal }}</strong></div>
        <div><span class="secondary-text">分析：推荐 / 复核 / 拒绝</span><strong>{{ smartDetailProgress.recommend_count }} / {{ smartDetailProgress.review_count }} / {{ smartDetailProgress.reject_count }}</strong></div>
        <div v-if="smartCompletionProgress"><span class="secondary-text">Recommend 完成</span><strong>{{ smartCompletionProgress.recommend.current }} / {{ smartCompletionProgress.recommend.target }}（剩余 {{ smartCompletionProgress.recommend.remaining }}）</strong></div>
        <div v-if="smartCompletionProgress"><span class="secondary-text">Review 完成</span><strong>{{ smartCompletionProgress.review.target === null ? `${smartCompletionProgress.review.current}（未计入目标）` : `${smartCompletionProgress.review.current} / ${smartCompletionProgress.review.target}（剩余 ${smartCompletionProgress.review.remaining}）` }}</strong></div>
        <div v-if="currentSmartCapture.collection_progress?.prefetch"><span class="secondary-text">下一批 JD</span><strong>{{ currentSmartCapture.collection_progress.prefetch.succeeded }} / {{ currentSmartCapture.collection_progress.prefetch.total }} 已准备</strong></div>
      </div>
    </section>


    <section v-if="activeCaptureConditionTab === 'smart' && linkedParentWorkflowRun" class="page-panel smart-workflow-details">
      <div class="panel-title-row">
        <div><p class="panel-eyebrow">Linked Parent</p><h2>关联父任务镜像</h2></div>
      </div>
      <el-descriptions :column="2" border>
        <el-descriptions-item label="父任务 ID"><code>{{ linkedParentWorkflowRun.workflow_run_id }}</code></el-descriptions-item>
        <el-descriptions-item label="父任务状态">{{ linkedParentWorkflowRun.status }} / {{ linkedParentWorkflowRun.control_state }}</el-descriptions-item>
        <el-descriptions-item label="当前编排步骤">{{ linkedParentWorkflowRun.current_step || linkedParentWorkflowRun.next_action }}</el-descriptions-item>
        <el-descriptions-item label="关联开始时间">{{ linkedParentStartedAt }}</el-descriptions-item>
      </el-descriptions>
      <div class="platform-actions">
        <el-button v-if="linkedParentCanPause" :loading="smartControlLoading" @click="pauseSmartWorkflow">暂停父任务</el-button>
        <el-button v-if="linkedParentCanResume" :loading="smartControlLoading" @click="resumeSmartWorkflow">继续父任务</el-button>
        <el-button v-if="linkedParentCanCancel" type="danger" plain :loading="smartControlLoading" @click="stopSmartWorkflow">停止父任务</el-button>
        <el-button @click="router.push({ name: 'fine-job-cockpit' })">返回驾驶舱</el-button>
      </div>
    </section>

    <section v-if="activeCaptureConditionTab === 'smart' && smartSearchPlanner" class="page-panel smart-search-planner">
      <div class="panel-title-row"><div><p class="panel-eyebrow">Search Planner</p><h2>智能搜索策略</h2></div></div>
      <p>搜索范围：{{ smartSearchPlanner.current_scope.keyword || "—" }} / {{ smartSearchPlanner.current_scope.city || "—" }}</p>
      <p>当前组合：{{ smartPlannerFilterText }}</p>
      <p>本组合：Fresh {{ smartPlannerMetric("run_fresh_jobs") }}；历史重复 {{ smartPlannerMetric("historical_duplicates") }}；策略拒绝 {{ smartPlannerMetric("strategy_reject") }}；合格 Fresh {{ smartPlannerMetric("qualified_fresh_jobs") }}</p>
      <p>切换原因：{{ smartPlannerReasonText }}</p>
      <p>下一动作：{{ smartPlannerNextActionText }}</p>
      <p v-if="smartCaptureStatusText">实际采集状态：{{ smartCaptureStatusText }}</p>
    </section>

    <section v-if="hasDisplayedTask" class="page-panel">
      <p v-if="activeCaptureConditionTab === 'custom' && captureStore.syncState === 'syncing'">正在同步岗位…</p>
      <el-empty v-if="!workflowDisplayJobs.length && !(activeCaptureConditionTab === 'smart' ? sharedSmartCapture.loading || sharedSmartCapture.error : captureStore.syncState === 'syncing' || captureStore.syncError)" :description="['running', 'queued', 'pausing'].includes(activeCaptureConditionTab === 'smart' ? currentSmartCapture?.status || '' : captureStore.task?.status || '') ? '正在采集，等待岗位结果' : '本次未采集到岗位'" />
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

      <div v-if="!displayingCurrentSmartCapture" class="detail-actions">
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
          :disabled="taskRunning || !smartManualAnalysisAvailable || !recommendationStrategyId || !selectedWorkflowJobIds.length"
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
              v-if="!displayingCurrentSmartCapture && scope.row.detail_status === 'completed' && !scope.row.delivery_evaluation"
              link
              type="warning"
              :disabled="taskRunning || !scope.row.job_id || !recommendationStrategyId"
              :loading="captureStore.suggesting"
              @click.stop="evaluateSingleDelivery(scope.row)"
            >
              获取投递详情
            </el-button>
            <el-button
              v-else-if="!displayingCurrentSmartCapture && scope.row.detail_status !== 'completed'"
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

      <div v-if="displayedTask?.jobs_path" class="capture-output-path">
        <span>列表文件</span><code>{{ displayedTask?.jobs_path }}</code>
        <template v-if="displayedTask?.details_path">
          <span>详情文件</span><code>{{ displayedTask?.details_path }}</code>
        </template>
      </div>
    </section>

    <section v-if="activeCaptureConditionTab === 'smart' && currentSmartCapture" class="page-panel smart-analysis-panel">
      <div class="panel-title-row">
        <div><p class="panel-eyebrow">Analysis Queue</p><h2>待分析岗位队列</h2></div>
        <el-button @click="loadSmartAnalysisItems(true)">刷新</el-button>
      </div>
      <p class="secondary-text">本批进入 Codex 前的岗位与筛选依据均来自当前 Smart Capture 的持久化记录。</p>
      <el-alert v-if="smartAnalysisError" :title="smartAnalysisError" type="error" :closable="false" />
      <el-table v-loading="smartAnalysisLoading" :data="smartAnalysisItems" max-height="420">
        <el-table-column label="岗位" min-width="180"><template #default="scope">{{ scope.row.job.title }}</template></el-table-column>
        <el-table-column label="公司" min-width="140"><template #default="scope">{{ scope.row.job.company }}</template></el-table-column>
        <el-table-column label="薪资 / 城市" min-width="140"><template #default="scope">{{ scope.row.job.salary }} / {{ scope.row.job.city }}</template></el-table-column>
        <el-table-column label="来源" min-width="140"><template #default="scope">{{ scope.row.job.discovery_keyword }} · 深度 {{ scope.row.job.discovery_depth }}</template></el-table-column>
        <el-table-column label="初筛" min-width="160"><template #default="scope">{{ scope.row.job.filter_result }}：{{ scope.row.job.filter_reasons.join("；") }}</template></el-table-column>
        <el-table-column label="JD / Item" min-width="130"><template #default="scope">{{ scope.row.job.jd_status }} / {{ scope.row.status }}</template></el-table-column>
        <el-table-column label="操作" width="100"><template #default="scope"><el-button link @click="viewSmartAnalysisItem(scope.row)">查看详情</el-button></template></el-table-column>
      </el-table>
    </section>

    <section v-if="activeCaptureConditionTab === 'smart' && currentSmartCapture" class="page-panel smart-analysis-panel">
      <div class="panel-title-row"><div><p class="panel-eyebrow">Analysis Result</p><h2>分析结果</h2></div></div>
      <el-alert v-if="smartActiveAnalysisItem" type="info" :closable="false" :title="`正在分析：${smartActiveAnalysisItem.job.title} · ${smartActiveAnalysisItem.job.company}`" />
      <el-empty v-if="!smartAnalysisLoading && !smartAnalysisError && !smartAnalysisResultItems.length" description="当前尚无已保存分析结果；进行中会在 Codex 分析工作台显示正在处理的岗位。" />
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

    <section v-if="activeCaptureConditionTab === 'smart' && smartWorkflowRun?.status === 'completed'" class="page-panel smart-analysis-panel">
      <div class="panel-title-row"><div><p class="panel-eyebrow">Completion</p><h2>本轮完成结果</h2></div></div>
      <p>本轮共分析 {{ smartWorkflowRun.progress.recommend_count + smartWorkflowRun.progress.review_count + smartWorkflowRun.progress.reject_count }}；Recommend {{ smartWorkflowRun.progress.recommend_count }}；Review {{ smartWorkflowRun.progress.review_count }}；Reject {{ smartWorkflowRun.progress.reject_count }}；进入待确认 {{ smartPendingReviewItems.length }}。</p>
      <div v-for="item in smartPendingReviewItems" :key="`completed-${item.workflow_task_id}`" class="smart-analysis-result-card">
        <strong>{{ item.job.title }} · {{ item.job.company }}</strong>
        <p>{{ listSmartText(item.analysis_result.reasons) }}</p>
        <p>风险：{{ listSmartText(item.analysis_result.risks) || "无" }}</p>
        <el-button link @click="router.push({ name: 'fine-job-review' })">去待确认</el-button>
      </div>
    </section>

    <div class="capture-condition-tabs" v-loading="formOptionsLoading || strategiesStore.loading">
      <el-alert v-if="activeCaptureConditionTab === 'custom' && strategiesStore.error" :title="strategiesStore.error" type="error" :closable="false" />
      <el-button v-if="activeCaptureConditionTab === 'custom' && strategiesStore.error" @click="strategiesStore.load()">刷新策略选项</el-button>
      <section v-if="activeCaptureConditionTab === 'custom'">
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
          :disabled="Boolean(startState.intent)"
          :loading="captureStore.capturing || customSubmitting"
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
      </section>
    </div>

    <el-dialog v-model="smartAnalysisDetailOpen" title="Workflow 分析 Item 详情" width="80%">
      <p v-if="analysisDetailLoading">正在读取分析详情…</p>
      <el-alert v-if="analysisDetailError" :title="analysisDetailError" type="error" />
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
          v-if="!displayingCurrentSmartCapture && currentDetailJob.detail_status !== 'queued' && currentDetailJob.detail_status !== 'collecting'"
          type="primary"
          :disabled="taskRunning"
          @click="captureSingleDetail(currentDetailJob)"
        >
          {{ detailActionLabel(currentDetailJob) }}
        </el-button>
        <el-button
          v-if="!displayingCurrentSmartCapture && currentDetailJob.detail_status === 'completed'"
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
