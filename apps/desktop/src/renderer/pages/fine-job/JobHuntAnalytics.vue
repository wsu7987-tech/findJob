<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import type { EChartsOption } from "echarts";
import { useRouter } from "vue-router";

import ChartSurface from "@/components/ChartSurface.vue";
import EmptyState from "@/components/EmptyState.vue";
import { formatAnalyticsRate, formatAnalyticsRateWithDenominator, displayAnalyticsMetric } from "@/services/fineJobJobHuntAnalytics";
import { useFineJobJobHuntAnalyticsStore } from "@/stores/fineJobJobHuntAnalytics";
import type {
  FineJobAnalyticsFunnelStageKey,
  FineJobAnalyticsGranularity,
  FineJobAnalyticsMetric,
  FineJobAnalyticsPreset,
  FineJobContactOrigin,
  FineJobJobHuntAnalyticsJobItem,
  FineJobJobHuntAnalyticsRejectionReasonBucket,
  FineJobRejectionReasonSource,
  FineJobJobHuntAnalyticsSourcePerformanceItem
} from "@/types";

const store = useFineJobJobHuntAnalyticsStore();
const router = useRouter();
const detailVisible = ref(false);
const detailTitle = ref("");
const detailExpectedCount = ref(0);
const detailShowsRejection = ref(false);

const presetOptions: Array<{ label: string; value: Exclude<FineJobAnalyticsPreset, "custom"> }> = [
  { label: "今天", value: "today" },
  { label: "最近 7 天", value: "last7" },
  { label: "本周", value: "thisWeek" },
  { label: "最近 30 天", value: "last30" },
  { label: "本月", value: "thisMonth" }
];

const originOptions: Array<{ label: string; value: FineJobContactOrigin | "" }> = [
  { label: "全部", value: "" },
  { label: "FineJob 自动联系", value: "finejob_auto" },
  { label: "FineJob 内我主动联系", value: "candidate_initiated" },
  { label: "其他渠道我主动联系", value: "external_candidate_initiated" },
  { label: "招聘方主动联系", value: "recruiter_initiated" },
  { label: "未知", value: "unknown" }
];

const metricCards = [
  { label: "主动联系", key: "candidate_contacts" },
  { label: "主动联系获回复", key: "candidate_contact_replies" },
  { label: "发送简历", key: "resume_submitted" },
  { label: "简历查看", key: "resume_viewed" },
  { label: "已约面", key: "interview_scheduled" },
  { label: "被拒绝", key: "rejected" },
  { label: "Offer", key: "offer_received" },
  { label: "招聘方主动联系", key: "recruiter_contacts" }
] as const;

const funnelLabels: Record<FineJobAnalyticsFunnelStageKey, string> = {
  candidate_contacts: "主动联系",
  candidate_contact_replies: "获得 HR 回复",
  resume_submitted: "发送简历",
  resume_viewed: "简历查看",
  interview_scheduled: "已约面",
  offer_received: "Offer"
};

const currentStateItems = [
  { label: "等招聘方回复", key: "waiting_recruiter", query: { waiting_on: "recruiter" } },
  { label: "等我回复", key: "waiting_candidate", query: { waiting_on: "candidate" } },
  { label: "建议跟进", key: "followup_recommended", query: { attention: "needs_followup" } },
  { label: "用人部门评估中", key: "under_review", query: null },
  { label: "面试安排中", key: "interview_scheduling", query: null }
] as const;

const sourceLabels: Record<FineJobContactOrigin, string> = {
  finejob_auto: "FineJob 自动联系",
  candidate_initiated: "FineJob 内我主动联系",
  external_candidate_initiated: "其他渠道我主动联系",
  recruiter_initiated: "招聘方主动联系",
  unknown: "未知"
};

const rejectionLabels: Record<string, string> = {
  experience: "经验",
  education: "学历",
  skills: "技能",
  industry_background: "行业背景",
  salary: "薪资",
  location: "地点",
  availability: "到岗时间",
  position_filled: "岗位已招满",
  headcount_closed: "编制关闭",
  fit: "匹配度",
  other: "其他",
  unknown: "原因未知"
};

const overview = computed(() => store.data?.overview ?? null);
const trend = computed(() => store.data?.trend ?? []);
const funnel = computed(() => store.data?.funnel ?? null);
const currentState = computed(() => store.data?.current_state ?? null);
const rejectionAnalysis = computed(() => store.data?.rejection_analysis ?? null);

const historicalValueKeys = [
  "candidate_contacts",
  "recruiter_contacts",
  "candidate_contact_replies",
  "resume_submitted",
  "resume_viewed",
  "under_review",
  "interview_scheduled",
  "rejected",
  "job_closed",
  "offer_received"
] as const;

const hasHistoricalData = computed(() => {
  const overviewHasData = historicalValueKeys.some((key) => (overview.value?.[key] ?? 0) > 0);
  const trendHasData = trend.value.some((point) =>
    [point.candidate_contacts, point.resume_submitted, point.interview_scheduled, point.rejected]
      .some((value) => (value ?? 0) > 0)
  );
  const rejectionHasData = [
    ...(rejectionAnalysis.value?.recruiter_explicit ?? []),
    ...(rejectionAnalysis.value?.ai_inferred ?? []),
    ...(rejectionAnalysis.value?.unknown ?? [])
  ].some((item) => (item.job_count ?? 0) > 0);
  const sourceHasData = (store.data?.source_performance ?? [])
    .some((item) => (item.job_count ?? 0) > 0);
  return overviewHasData || trendHasData || rejectionHasData || sourceHasData;
});

const trendHasData = computed(() => trend.value.some((point) =>
  [point.candidate_contacts, point.resume_submitted, point.interview_scheduled, point.rejected]
    .some((value) => (value ?? 0) > 0)
));

const trendOption = computed<EChartsOption>(() => ({
  tooltip: { trigger: "axis" },
  legend: { data: ["主动联系", "发送简历", "已约面", "被拒绝"] },
  grid: { left: 12, right: 20, top: 36, bottom: 24, containLabel: true },
  xAxis: {
    type: "category",
    boundaryGap: false,
    data: trend.value.map((point) => point.period_start?.slice(5) ?? "")
  },
  yAxis: { type: "value", minInterval: 1 },
  series: [
    { name: "主动联系", type: "line", smooth: true, data: trend.value.map((point) => point.candidate_contacts ?? 0) },
    { name: "发送简历", type: "line", smooth: true, data: trend.value.map((point) => point.resume_submitted ?? 0) },
    { name: "已约面", type: "line", smooth: true, data: trend.value.map((point) => point.interview_scheduled ?? 0) },
    { name: "被拒绝", type: "line", smooth: true, data: trend.value.map((point) => point.rejected ?? 0) }
  ]
}));

const rejectionGroups = computed(() => [
  { label: "HR 明确原因", source: "recruiter_explicit" as const, items: rejectionAnalysis.value?.recruiter_explicit ?? [] },
  { label: "AI 推测", source: "ai_inferred" as const, items: rejectionAnalysis.value?.ai_inferred ?? [] },
  { label: "原因未知", source: "unknown" as const, items: rejectionAnalysis.value?.unknown ?? [] }
]);

const sourceRows = computed(() => {
  const sourcePerformance = store.data?.source_performance ?? [];
  return (Object.keys(sourceLabels) as FineJobContactOrigin[]).map((contactOrigin) =>
    sourcePerformance.find((item) => item.contact_origin === contactOrigin)
      ?? ({ contact_origin: contactOrigin } as FineJobJobHuntAnalyticsSourcePerformanceItem)
  );
});

const sourceLabel = (value: unknown) =>
  sourceLabels[value as FineJobContactOrigin] ?? "未知";

const metricValue = (key: (typeof metricCards)[number]["key"]) =>
  displayAnalyticsMetric(overview.value?.[key]);

const currentValue = (key: (typeof currentStateItems)[number]["key"]) =>
  displayAnalyticsMetric(currentState.value?.[key]);

const rejectionLabel = (item: FineJobJobHuntAnalyticsRejectionReasonBucket) =>
  rejectionLabels[item.category ?? "unknown"] ?? item.category ?? "原因未知";

const rejectionMax = (items: FineJobJobHuntAnalyticsRejectionReasonBucket[]) =>
  Math.max(...items.map((item) => item.job_count ?? 0), 1);

const progressLabels: Record<string, string> = {
  discovered: "已发现",
  shortlisted: "已筛选",
  greeted: "已联系",
  communicating: "沟通中",
  resume_requested: "待发简历",
  resume_submitted: "已发简历",
  resume_viewed: "简历已查看",
  under_review: "评估中",
  interview_scheduling: "面试安排中",
  interviewing: "面试中",
  offer: "已获 Offer",
  rejected: "已被拒绝",
  closed: "岗位已关闭"
};

const detailCountMatches = computed(() =>
  store.detailData === null || store.detailData.total === detailExpectedCount.value
);

const progressLabel = (item: FineJobJobHuntAnalyticsJobItem) =>
  progressLabels[item.progress] ?? "进展待确认";

const rejectionSourceLabel = (source: FineJobRejectionReasonSource | null | undefined) => ({
  recruiter_explicit: "HR 明确",
  ai_inferred: "AI 推测",
  unknown: "原因未知"
}[source ?? "unknown"]);

const openMetricDetails = async (card: (typeof metricCards)[number]) => {
  detailVisible.value = true;
  detailTitle.value = `${card.label}岗位`;
  detailExpectedCount.value = Number(overview.value?.[card.key] ?? 0);
  detailShowsRejection.value = card.key === "rejected";
  try {
    await store.loadDetails({ metric: card.key as FineJobAnalyticsMetric });
  } catch {
    // 明细错误由 Store 保存并在 Dialog 内展示。
  }
};

const openRejectionDetails = async (
  groupLabel: string,
  source: FineJobRejectionReasonSource,
  item: FineJobJobHuntAnalyticsRejectionReasonBucket
) => {
  detailVisible.value = true;
  detailTitle.value = `${groupLabel} · ${rejectionLabel(item)}`;
  detailExpectedCount.value = Number(item.job_count ?? 0);
  detailShowsRejection.value = true;
  try {
    await store.loadDetails({
      metric: "rejected",
      rejection_reason_source: source,
      rejection_reason_category: item.category ?? "unknown"
    });
  } catch {
    // 明细错误由 Store 保存并在 Dialog 内展示。
  }
};

const closeDetails = () => {
  detailVisible.value = false;
  store.clearDetails();
};

const goToChat = (query: { waiting_on?: string; attention?: string } | null) => {
  if (!query) return;
  void router.push({ name: "fine-job-chat", query });
};

const handlePresetChange = (value: unknown) => {
  if (typeof value === "string") void store.selectPreset(value as FineJobAnalyticsPreset).catch(() => undefined);
};

const handleCustomRangeChange = (value: unknown) => {
  if (Array.isArray(value)) {
    void store.applyCustomRange(value.map(String)).catch(() => undefined);
  }
};

const handleOriginChange = (value: unknown) => {
  if (typeof value === "string") {
    void store.setContactOrigin(value as FineJobContactOrigin | "").catch(() => undefined);
  }
};

const handleGranularityChange = (value: unknown) => {
  if (value === "auto" || value === "day" || value === "week") {
    void store.setGranularity(value as FineJobAnalyticsGranularity).catch(() => undefined);
  }
};

const reload = async () => {
  try {
    await store.refresh();
  } catch {
    // 错误信息由 Store 保存并由页面统一展示。
  }
};

onMounted(() => {
  void reload();
});
</script>

<template>
  <section
    class="page-stack fine-job-page analytics-page"
    v-loading="store.loading"
    :aria-busy="store.loading"
  >
    <header class="page-heading">
      <div>
        <p class="app-shell__eyebrow">求职进展复盘</p>
        <h3>求职分析</h3>
        <p class="secondary-text">同一岗位只计一次，展示求职动作、转化和当前待处理状态。</p>
      </div>
      <el-button type="primary" plain @click="reload">刷新数据</el-button>
    </header>

    <section class="page-panel analytics-filters">
      <div class="filter-row">
        <span class="filter-label">时间范围</span>
        <el-radio-group :model-value="store.preset" @change="handlePresetChange">
          <el-radio-button
            v-for="option in presetOptions"
            :key="option.value"
            :label="option.value"
          >{{ option.label }}</el-radio-button>
          <el-radio-button label="custom">自定义</el-radio-button>
        </el-radio-group>
        <el-date-picker
          v-if="store.preset === 'custom'"
          v-model="store.customRange"
          type="daterange"
          value-format="YYYY-MM-DD"
          range-separator="至"
          start-placeholder="开始日期"
          end-placeholder="结束日期"
          @change="handleCustomRangeChange"
        />
      </div>
      <div class="filter-row">
        <span class="filter-label">沟通来源</span>
        <el-select
          :model-value="store.contactOrigin"
          class="origin-select"
          @change="handleOriginChange"
        >
          <el-option
            v-for="option in originOptions"
            :key="option.value || 'all'"
            :label="option.label"
            :value="option.value"
          />
        </el-select>
        <span class="secondary-text">统计时区：{{ store.data?.range?.timezone ?? "Asia/Shanghai" }}</span>
      </div>
      <div class="filter-row">
        <span class="filter-label">趋势粒度</span>
        <el-radio-group :model-value="store.granularity" @change="handleGranularityChange">
          <el-radio-button label="auto">自动</el-radio-button>
          <el-radio-button label="day">日</el-radio-button>
          <el-radio-button label="week">周</el-radio-button>
        </el-radio-group>
      </div>
    </section>

    <el-alert
      v-if="store.error"
      :title="store.error"
      type="error"
      :closable="false"
      show-icon
    />

    <EmptyState
      v-if="!store.data && !store.loading"
      title="暂无求职分析数据"
      description="当前还没有可用于分析的求职活动，请先更新求职数据。"
    />

    <template v-if="store.data">
      <el-alert
        v-if="!hasHistoricalData"
        title="当前日期范围内暂无历史求职动作"
        description="当前待处理仍按岗位最新状态展示。"
        type="info"
        :closable="false"
      />

      <section class="page-panel">
        <div class="section-heading">
          <div>
            <h3>核心指标</h3>
            <p class="secondary-text">历史指标按所选日期范围内发生的求职进展统计。</p>
          </div>
        </div>
        <div class="analytics-metric-grid">
          <button
            v-for="card in metricCards"
            :key="card.key"
            class="metric-card metric-card--action"
            type="button"
            @click="openMetricDetails(card)"
          >
            <p>{{ card.label }}</p>
            <strong>{{ metricValue(card.key) }}</strong>
            <span v-if="card.key === 'candidate_contact_replies'">
              回复率 {{ formatAnalyticsRateWithDenominator(overview?.candidate_reply_rate, overview?.candidate_contacts) }}
            </span>
            <span v-else-if="card.key === 'recruiter_contacts'">当前来源单独统计</span>
            <small>查看岗位</small>
          </button>
        </div>
      </section>

      <section class="page-panel analytics-two-columns">
        <div>
          <div class="section-heading">
            <div>
              <h3>求职趋势</h3>
              <p class="secondary-text">按{{ store.data.range?.granularity === 'week' ? '周' : '日' }}展示求职进展数量。</p>
            </div>
          </div>
          <ChartSurface
            v-if="trendHasData"
            :option="trendOption"
            :height="300"
            aria-label="求职动作趋势图"
          />
          <EmptyState
            v-else
            title="暂无趋势数据"
            description="所选日期范围内还没有主动联系、发简历、约面或拒绝记录。"
          />
        </div>

        <div>
          <div class="section-heading">
            <div>
              <h3>主动求职漏斗</h3>
              <p class="secondary-text">按所选日期内首次主动联系的岗位，观察截至结束日期的后续进展。</p>
            </div>
          </div>
          <div v-if="funnel?.available !== false && funnel?.stages?.length" class="funnel-list">
            <div v-for="(stage, index) in funnel.stages" :key="stage.key" class="funnel-row">
              <div class="funnel-row__title">
                <span>{{ funnelLabels[stage.key] }}</span>
                <strong>{{ displayAnalyticsMetric(stage.count) }}</strong>
              </div>
              <div class="funnel-track">
                <span
                  class="funnel-track__fill"
                  :style="{ width: `${(stage.count ?? 0) > 0 ? Math.max(8, ((stage.count ?? 0) / Math.max(funnel.stages[0]?.count ?? 0, 1)) * 100) : 0}%` }"
                ></span>
              </div>
              <div class="funnel-row__rates">
                <span v-if="index > 0">上一步 {{ formatAnalyticsRate(stage.previous_rate) }}</span>
                <span>首层 {{ formatAnalyticsRate(stage.total_rate) }}</span>
              </div>
            </div>
          </div>
          <EmptyState
            v-else
            title="暂无主动求职漏斗"
            :description="funnel?.unavailable_reason === 'candidate_contact_cohort_not_applicable' ? '招聘方主动联系不适用主动求职漏斗。' : '当前范围内暂无主动联系岗位。'"
          />
        </div>
      </section>

      <section class="page-panel">
        <div class="section-heading">
          <div>
            <h3>当前待处理</h3>
            <p class="secondary-text">基于当前岗位状态，不受上方历史日期范围影响。</p>
          </div>
        </div>
        <div class="analytics-metric-grid current-state-grid">
          <button
            v-for="item in currentStateItems"
            :key="item.key"
            class="metric-card"
            :class="{ 'metric-card--action': item.query }"
            type="button"
            :disabled="!item.query"
            @click="goToChat(item.query)"
          >
            <p>{{ item.label }}</p>
            <strong>{{ currentValue(item.key) }}</strong>
            <small v-if="item.query">进入自动代聊</small>
          </button>
        </div>
      </section>

      <section class="page-panel">
        <div class="section-heading">
          <div>
            <h3>拒绝原因分析</h3>
            <p class="secondary-text">HR 明确原因与 AI 推测分开呈现，岗位关闭不计入拒绝。</p>
          </div>
        </div>
        <div class="rejection-grid">
          <article v-for="group in rejectionGroups" :key="group.label" class="rejection-group">
            <h4>{{ group.label }}</h4>
            <div v-if="group.items.length" class="rejection-list">
              <button
                v-for="item in group.items"
                :key="`${group.label}-${item.category}`"
                class="rejection-item rejection-item--action"
                type="button"
                @click="openRejectionDetails(group.label, group.source, item)"
              >
                <div class="rejection-item__label">
                  <span>{{ rejectionLabel(item) }}</span>
                  <strong>{{ displayAnalyticsMetric(item.job_count) }}</strong>
                </div>
                <div class="rejection-track">
                  <span
                    class="rejection-track__fill"
                    :style="{ width: `${((item.job_count ?? 0) / rejectionMax(group.items)) * 100}%` }"
                  ></span>
                </div>
                <small>查看岗位</small>
              </button>
            </div>
            <p v-else class="secondary-text">暂无记录</p>
          </article>
        </div>
      </section>

      <section class="page-panel">
        <div class="section-heading">
          <div>
            <h3>来源表现</h3>
            <p class="secondary-text">各来源以该来源下的岗位数为分母；招聘方主动联系的主动回复率显示为 —。</p>
          </div>
        </div>
        <el-table :data="sourceRows" stripe>
          <el-table-column label="来源" min-width="180">
            <template #default="{ row }">{{ sourceLabel(row.contact_origin) }}</template>
          </el-table-column>
          <el-table-column label="岗位数" width="100">
            <template #default="{ row }">{{ displayAnalyticsMetric(row.job_count) }}</template>
          </el-table-column>
          <el-table-column label="主动回复率" width="120">
            <template #default="{ row }">
              {{ row.contact_origin === 'recruiter_initiated' ? '—' : formatAnalyticsRate(row.candidate_reply_rate) }}
            </template>
          </el-table-column>
          <el-table-column label="发简历率" width="110">
            <template #default="{ row }">{{ formatAnalyticsRate(row.resume_rate) }}</template>
          </el-table-column>
          <el-table-column label="约面率" width="100">
            <template #default="{ row }">{{ formatAnalyticsRate(row.interview_rate) }}</template>
          </el-table-column>
          <el-table-column label="Offer率" width="100">
            <template #default="{ row }">{{ formatAnalyticsRate(row.offer_rate) }}</template>
          </el-table-column>
          <el-table-column label="拒绝率" width="100">
            <template #default="{ row }">{{ formatAnalyticsRate(row.rejection_rate) }}</template>
          </el-table-column>
        </el-table>
      </section>
    </template>

    <el-dialog
      :model-value="detailVisible"
      :title="detailTitle"
      width="min(760px, 92vw)"
      destroy-on-close
      @close="closeDetails"
    >
      <div
        class="analytics-detail"
        v-loading="store.detailLoading"
        :aria-busy="store.detailLoading"
      >
        <p class="secondary-text detail-summary">
          指标显示 {{ detailExpectedCount }} 个岗位，明细共 {{ store.detailData?.total ?? 0 }} 个岗位。
        </p>
        <el-alert
          v-if="store.detailError"
          title="岗位明细加载失败"
          :description="store.detailError"
          type="error"
          :closable="false"
          show-icon
        />
        <el-alert
          v-else-if="!detailCountMatches"
          title="岗位数量暂未对齐，请关闭后刷新数据再试。"
          type="warning"
          :closable="false"
          show-icon
        />
        <EmptyState
          v-if="!store.detailLoading && !store.detailError && !(store.detailData?.jobs?.length)"
          title="暂无岗位明细"
          description="当前筛选条件下没有对应岗位。"
        />
        <div v-else-if="store.detailData?.jobs?.length" class="analytics-detail-list">
          <article v-for="job in store.detailData.jobs" :key="job.job_id" class="analytics-detail-item">
            <div>
              <strong>{{ job.title || "岗位名称待补充" }}</strong>
              <p>{{ job.company_name || "公司名称待补充" }} · {{ progressLabel(job) }}</p>
            </div>
            <div v-if="detailShowsRejection" class="analytics-detail-rejection">
              <el-tag :type="job.rejection_reason_source === 'ai_inferred' ? 'warning' : 'info'">
                {{ rejectionSourceLabel(job.rejection_reason_source) }}
              </el-tag>
              <span>{{ job.rejection_reason_summary || rejectionLabels[job.rejection_reason_category ?? "unknown"] || "未记录具体原因" }}</span>
            </div>
          </article>
        </div>
      </div>
    </el-dialog>
  </section>
</template>

<style scoped>
.analytics-page { display: grid; gap: 18px; }
.analytics-filters { display: grid; gap: 16px; }
.filter-row { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; }
.filter-label { min-width: 72px; font-weight: 600; color: var(--el-text-color-primary); }
.origin-select { width: 220px; }
.section-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-bottom: 16px; }
.section-heading h3, .section-heading h4, .section-heading p { margin: 0; }
.analytics-metric-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; }
.analytics-metric-grid .metric-card { min-height: 112px; }
.metric-card { width: 100%; border: 1px solid transparent; text-align: left; }
.metric-card:disabled { color: inherit; cursor: default; }
.metric-card--action { cursor: pointer; transition: border-color 0.2s, transform 0.2s; }
.metric-card--action:hover { border-color: var(--el-color-primary-light-5); transform: translateY(-1px); }
.analytics-metric-grid .metric-card p { margin: 0; }
.analytics-metric-grid .metric-card strong { display: block; margin: 8px 0 4px; }
.analytics-metric-grid .metric-card span { color: var(--el-text-color-secondary); font-size: 13px; }
.analytics-metric-grid .metric-card small, .rejection-item small { color: var(--el-color-primary); }
.analytics-two-columns { display: grid; grid-template-columns: minmax(0, 1.25fr) minmax(320px, 1fr); gap: 28px; }
.funnel-list { display: grid; gap: 14px; }
.funnel-row { display: grid; gap: 6px; }
.funnel-row__title, .funnel-row__rates, .rejection-item__label { display: flex; justify-content: space-between; gap: 12px; }
.funnel-row__rates { color: var(--el-text-color-secondary); font-size: 12px; }
.funnel-track, .rejection-track { height: 8px; overflow: hidden; border-radius: 999px; background: var(--el-fill-color-light); }
.funnel-track__fill, .rejection-track__fill { display: block; height: 100%; border-radius: inherit; background: var(--el-color-primary); }
.current-state-grid { grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); }
.rejection-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 18px; }
.rejection-group { padding: 16px; border-radius: 12px; background: var(--el-fill-color-light); }
.rejection-group h4 { margin: 0 0 16px; }
.rejection-list { display: grid; gap: 12px; }
.rejection-item { display: grid; gap: 6px; }
.rejection-item--action { width: 100%; padding: 0; border: 0; background: transparent; text-align: left; cursor: pointer; }
.rejection-item__label { font-size: 13px; }
.rejection-track__fill { background: var(--el-color-warning); }
.analytics-detail { min-height: 120px; }
.detail-summary { margin: 0 0 14px; }
.analytics-detail-list { display: grid; gap: 10px; max-height: 520px; overflow-y: auto; padding-right: 4px; }
.analytics-detail-item { display: grid; gap: 8px; padding: 14px; border: 1px solid var(--el-border-color-lighter); border-radius: 10px; }
.analytics-detail-item p { margin: 4px 0 0; color: var(--el-text-color-secondary); }
.analytics-detail-rejection { display: flex; align-items: center; gap: 8px; color: var(--el-text-color-regular); }
@media (max-width: 900px) {
  .analytics-two-columns, .rejection-grid { grid-template-columns: 1fr; }
}
@media (max-width: 720px) {
  .filter-label { width: 100%; }
  .origin-select { width: 100%; }
}
</style>
