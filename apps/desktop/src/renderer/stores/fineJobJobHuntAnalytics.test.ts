// @vitest-environment jsdom

import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";

const apiMocks = vi.hoisted(() => ({
  analytics: vi.fn(),
  details: vi.fn()
}));

vi.mock("@/services/api", () => ({
  ApiError: class extends Error {},
  NetworkError: class extends Error {},
  api: {
    getFineJobJobHuntAnalytics: apiMocks.analytics,
    getFineJobJobHuntAnalyticsJobs: apiMocks.details
  }
}));

import { useFineJobJobHuntAnalyticsStore } from "./fineJobJobHuntAnalytics";

describe("fineJobJobHuntAnalytics store", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-09-06T04:00:00.000Z"));
    apiMocks.analytics.mockReset();
    apiMocks.analytics.mockResolvedValue({});
    apiMocks.details.mockReset();
    apiMocks.details.mockResolvedValue({ metric: "rejected", total: 0, jobs: [] });
  });

  it("默认使用最近 7 天并请求 Asia/Shanghai 分析数据", async () => {
    const store = useFineJobJobHuntAnalyticsStore();

    expect(store.preset).toBe("last7");
    expect(store.fromDate).toBe("2026-08-31");
    expect(store.toDate).toBe("2026-09-06");

    await store.load();

    expect(apiMocks.analytics).toHaveBeenCalledWith({
      from: "2026-08-31",
      to: "2026-09-06",
      timezone: "Asia/Shanghai",
      granularity: "auto",
      contact_origin: null
    });
    await store.setGranularity("week");
    expect(apiMocks.analytics).toHaveBeenLastCalledWith(expect.objectContaining({
      granularity: "week"
    }));
    vi.useRealTimers();
  });

  it("时间范围和来源切换都会重新请求", async () => {
    const store = useFineJobJobHuntAnalyticsStore();

    await store.selectPreset("today");
    await store.setContactOrigin("recruiter_initiated");

    expect(apiMocks.analytics).toHaveBeenNthCalledWith(1, expect.objectContaining({
      from: "2026-09-06",
      to: "2026-09-06"
    }));
    expect(apiMocks.analytics).toHaveBeenNthCalledWith(2, expect.objectContaining({
      contact_origin: "recruiter_initiated"
    }));
    vi.useRealTimers();
  });

  it("请求失败时保存错误并结束 loading", async () => {
    apiMocks.analytics.mockRejectedValue(new Error("服务不可用"));
    const store = useFineJobJobHuntAnalyticsStore();

    await expect(store.load()).rejects.toThrow("服务不可用");
    expect(store.loading).toBe(false);
    expect(store.error).toBe("服务不可用");
    vi.useRealTimers();
  });

  it("岗位明细沿用当前日期和来源筛选", async () => {
    const store = useFineJobJobHuntAnalyticsStore();
    store.contactOrigin = "candidate_initiated";

    await store.loadDetails({
      metric: "rejected",
      rejection_reason_source: "ai_inferred",
      rejection_reason_category: "skills"
    });

    expect(apiMocks.details).toHaveBeenCalledWith({
      metric: "rejected",
      from: "2026-08-31",
      to: "2026-09-06",
      timezone: "Asia/Shanghai",
      contact_origin: "candidate_initiated",
      rejection_reason_source: "ai_inferred",
      rejection_reason_category: "skills"
    });
    expect(store.detailLoading).toBe(false);
    vi.useRealTimers();
  });

  it("岗位明细失败时保留独立错误状态", async () => {
    apiMocks.details.mockRejectedValue(new Error("明细服务不可用"));
    const store = useFineJobJobHuntAnalyticsStore();

    await expect(store.loadDetails({ metric: "offer_received" })).rejects.toThrow(
      "明细服务不可用"
    );

    expect(store.detailError).toBe("明细服务不可用");
    expect(store.detailLoading).toBe(false);
    vi.useRealTimers();
  });
});
