// @vitest-environment jsdom

import { beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { defineComponent } from "vue";

import type { FineJobJobHuntAnalyticsResponse } from "@/types";

const mocks = vi.hoisted(() => ({
  useStore: vi.fn(),
  push: vi.fn()
}));

vi.mock("@/stores/fineJobJobHuntAnalytics", () => ({
  useFineJobJobHuntAnalyticsStore: mocks.useStore
}));

vi.mock("vue-router", () => ({
  useRouter: () => ({ push: mocks.push })
}));

vi.mock("@/components/ChartSurface.vue", () => ({
  default: defineComponent({
    template: "<div data-testid='trend-chart'></div>"
  })
}));

import JobHuntAnalytics from "./JobHuntAnalytics.vue";

const ButtonStub = defineComponent({
  inheritAttrs: false,
  emits: ["click"],
  template: "<button v-bind=\"$attrs\" @click=\"$emit('click')\"><slot /></button>"
});

const ElementStub = defineComponent({
  inheritAttrs: false,
  props: {
    title: { type: String, default: "" },
    description: { type: String, default: "" }
  },
  template: "<div v-bind=\"$attrs\">{{ title }}{{ description }}<slot /></div>"
});

const TableColumnStub = defineComponent({
  template: "<div><slot :row=\"{ contact_origin: 'recruiter_initiated' }\" /></div>"
});

const baseStore = () => ({
  preset: "last7",
  fromDate: "2026-08-31",
  toDate: "2026-09-06",
  customRange: null,
  contactOrigin: "",
  granularity: "auto",
  data: null as FineJobJobHuntAnalyticsResponse | null,
  loading: false,
  error: null as string | null,
  detailData: null as import("@/types").FineJobJobHuntAnalyticsJobsResponse | null,
  detailLoading: false,
  detailError: null as string | null,
  refresh: vi.fn().mockResolvedValue(undefined),
  selectPreset: vi.fn().mockResolvedValue(undefined),
  applyCustomRange: vi.fn().mockResolvedValue(undefined),
  setContactOrigin: vi.fn().mockResolvedValue(undefined),
  setGranularity: vi.fn().mockResolvedValue(undefined),
  loadDetails: vi.fn().mockResolvedValue(undefined),
  clearDetails: vi.fn()
});

const analyticsData: FineJobJobHuntAnalyticsResponse = {
  range: {
    from: "2026-08-31",
    to: "2026-09-06",
    timezone: "Asia/Shanghai",
    granularity: "day",
    contact_origin: null
  },
  overview: {
    candidate_contacts: 2,
    candidate_contact_replies: 1,
    candidate_reply_rate: 0.5,
    recruiter_contacts: 1,
    resume_submitted: 1,
    resume_viewed: null,
    under_review: 0,
    interview_scheduled: 0,
    rejected: 0,
    job_closed: 0,
    offer_received: 0
  },
  trend: [{
    period_start: "2026-09-06",
    candidate_contacts: 2,
    resume_submitted: 1,
    interview_scheduled: 0,
    rejected: 0
  }],
  funnel: {
    available: true,
    stages: [
      { key: "candidate_contacts", count: 2, previous_rate: null, total_rate: 1 },
      { key: "candidate_contact_replies", count: 1, previous_rate: 0.5, total_rate: 0.5 }
    ]
  },
  current_state: {
    waiting_recruiter: 1,
    waiting_candidate: 0,
    followup_recommended: 0,
    under_review: 0,
    interview_scheduling: 0
  },
  rejection_analysis: {
    recruiter_explicit: [],
    ai_inferred: [],
    unknown: []
  },
  source_performance: [{
    contact_origin: "recruiter_initiated",
    job_count: 1,
    candidate_reply_rate: null,
    resume_rate: 0,
    interview_rate: 0,
    offer_rate: 0,
    rejection_rate: 0
  }]
};

const mountPage = async (store = baseStore()) => {
  mocks.useStore.mockReturnValue(store);
  const wrapper = mount(JobHuntAnalytics, {
    global: {
      directives: { loading: () => undefined },
      stubs: {
        ElAlert: ElementStub,
        ElButton: ButtonStub,
        ElDatePicker: ElementStub,
        ElDialog: ElementStub,
        ElOption: ElementStub,
        ElRadioButton: ElementStub,
        ElRadioGroup: ElementStub,
        ElSelect: ElementStub,
        ElTag: ElementStub,
        ElTable: ElementStub,
        ElTableColumn: TableColumnStub
      }
    }
  });
  await flushPromises();
  return wrapper;
};

describe("JobHuntAnalytics", () => {
  beforeEach(() => {
    mocks.useStore.mockReset();
    mocks.push.mockReset();
  });

  it("展示当前待处理说明、来源回复率破折号并容忍 null 字段", async () => {
    const store = baseStore();
    store.data = analyticsData;
    const wrapper = await mountPage(store);

    expect(wrapper.text()).toContain("当前待处理");
    expect(wrapper.text()).toContain("不受上方历史日期范围影响");
    expect(wrapper.text()).toContain("招聘方主动联系");
    expect(wrapper.text()).toContain("—");
    expect(wrapper.find("[data-testid='trend-chart']").exists()).toBe(true);
  });

  it("空数据展示空状态", async () => {
    const store = baseStore();
    store.data = {
      overview: {},
      trend: [],
      funnel: { available: true, stages: [] },
      current_state: {},
      rejection_analysis: {},
      source_performance: []
    };
    const wrapper = await mountPage(store);

    expect(wrapper.text()).toContain("当前日期范围内暂无历史求职动作");
    expect(wrapper.text()).toContain("暂无趋势数据");
  });

  it("加载和错误状态可见", async () => {
    const loadingStore = baseStore();
    loadingStore.loading = true;
    const loadingWrapper = await mountPage(loadingStore);
    expect(loadingWrapper.attributes("aria-busy")).toBe("true");

    const errorStore = baseStore();
    errorStore.error = "分析接口暂时不可用";
    const errorWrapper = await mountPage(errorStore);
    expect(errorWrapper.text()).toContain("分析接口暂时不可用");
    expect(errorWrapper.text()).toContain("暂无求职分析数据");
  });

  it("点击 KPI 使用当前统计条件加载同口径岗位明细", async () => {
    const store = baseStore();
    store.data = analyticsData;
    store.detailData = {
      metric: "candidate_contacts",
      total: 2,
      jobs: [
        { job_id: "job-1", title: "后端工程师", company_name: "示例科技", progress: "communicating", matched_at: "2026-09-01T01:00:00Z", metric: "candidate_contacts" },
        { job_id: "job-2", title: "平台工程师", company_name: "示例网络", progress: "resume_submitted", matched_at: "2026-09-02T01:00:00Z", metric: "candidate_contacts" }
      ]
    };
    const wrapper = await mountPage(store);
    const candidateCard = wrapper.findAll("button.metric-card--action")
      .find((button) => button.text().includes("主动联系"));

    await candidateCard?.trigger("click");

    expect(store.loadDetails).toHaveBeenCalledWith({ metric: "candidate_contacts" });
    expect(wrapper.text()).toContain("指标显示 2 个岗位，明细共 2 个岗位");
    expect(wrapper.findAll(".analytics-detail-item")).toHaveLength(2);
  });

  it("拒绝原因明细保留来源标识和原因摘要", async () => {
    const store = baseStore();
    store.data = {
      ...analyticsData,
      overview: { ...analyticsData.overview, rejected: 1 },
      rejection_analysis: {
        recruiter_explicit: [],
        ai_inferred: [{ category: "skills", job_count: 1 }],
        unknown: []
      }
    };
    store.detailData = {
      metric: "rejected",
      total: 1,
      jobs: [{
        job_id: "job-ai",
        title: "AI 工程师",
        company_name: "示例智能",
        progress: "rejected",
        matched_at: "2026-09-03T01:00:00Z",
        metric: "rejected",
        rejection_reason_source: "ai_inferred",
        rejection_reason_category: "skills",
        rejection_reason_summary: "技能栈暂不匹配"
      }]
    };
    const wrapper = await mountPage(store);
    const reasonButton = wrapper.find("button.rejection-item--action");

    await reasonButton.trigger("click");

    expect(store.loadDetails).toHaveBeenCalledWith({
      metric: "rejected",
      rejection_reason_source: "ai_inferred",
      rejection_reason_category: "skills"
    });
    expect(wrapper.text()).toContain("AI 推测");
    expect(wrapper.text()).toContain("技能栈暂不匹配");
  });

  it("当前待处理携带最小筛选条件进入自动代聊", async () => {
    const store = baseStore();
    store.data = analyticsData;
    const wrapper = await mountPage(store);
    const currentButtons = wrapper.findAll(".current-state-grid button");

    await currentButtons[0].trigger("click");
    expect(mocks.push).toHaveBeenLastCalledWith({
      name: "fine-job-chat",
      query: { waiting_on: "recruiter" }
    });
    await currentButtons[1].trigger("click");
    expect(mocks.push).toHaveBeenLastCalledWith({
      name: "fine-job-chat",
      query: { waiting_on: "candidate" }
    });
    await currentButtons[2].trigger("click");
    expect(mocks.push).toHaveBeenLastCalledWith({
      name: "fine-job-chat",
      query: { attention: "needs_followup" }
    });
  });

  it("明细支持错误、空状态和大量岗位列表", async () => {
    const loadingStore = baseStore();
    loadingStore.data = analyticsData;
    loadingStore.detailLoading = true;
    const loadingWrapper = await mountPage(loadingStore);
    await loadingWrapper.find("button.metric-card--action").trigger("click");
    expect(loadingWrapper.find(".analytics-detail").attributes("aria-busy")).toBe("true");

    const errorStore = baseStore();
    errorStore.data = analyticsData;
    errorStore.detailError = "明细接口暂时不可用";
    const errorWrapper = await mountPage(errorStore);
    const errorCard = errorWrapper.find("button.metric-card--action");
    await errorCard.trigger("click");
    expect(errorWrapper.text()).toContain("岗位明细加载失败");
    expect(errorWrapper.text()).toContain("明细接口暂时不可用");

    const emptyStore = baseStore();
    emptyStore.data = analyticsData;
    emptyStore.detailData = { metric: "candidate_contacts", total: 0, jobs: [] };
    const emptyWrapper = await mountPage(emptyStore);
    await emptyWrapper.find("button.metric-card--action").trigger("click");
    expect(emptyWrapper.text()).toContain("暂无岗位明细");

    const manyStore = baseStore();
    manyStore.data = {
      ...analyticsData,
      overview: { ...analyticsData.overview, candidate_contacts: 120 }
    };
    manyStore.detailData = {
      metric: "candidate_contacts",
      total: 120,
      jobs: Array.from({ length: 120 }, (_, index) => ({
        job_id: `job-${index}`,
        title: `岗位 ${index}`,
        company_name: "示例公司",
        progress: "communicating",
        matched_at: "2026-09-01T01:00:00Z",
        metric: "candidate_contacts" as const
      }))
    };
    const manyWrapper = await mountPage(manyStore);
    await manyWrapper.find("button.metric-card--action").trigger("click");
    expect(manyWrapper.findAll(".analytics-detail-item")).toHaveLength(120);
    expect(manyWrapper.text()).toContain("指标显示 120 个岗位，明细共 120 个岗位");
  });
});
