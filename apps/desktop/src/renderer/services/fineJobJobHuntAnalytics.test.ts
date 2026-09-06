// @vitest-environment jsdom

import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  formatAnalyticsRate,
  formatAnalyticsRateWithDenominator,
  getAnalyticsPresetRange
} from "./fineJobJobHuntAnalytics";

describe("fineJobJobHuntAnalytics helpers", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-09-06T04:00:00.000Z"));
  });

  it("按 Asia/Shanghai 自然日生成默认和预设日期范围", () => {
    expect(getAnalyticsPresetRange("last7")).toEqual({
      from: "2026-08-31",
      to: "2026-09-06"
    });
    expect(getAnalyticsPresetRange("thisWeek")).toEqual({
      from: "2026-08-31",
      to: "2026-09-06"
    });
    expect(getAnalyticsPresetRange("thisMonth")).toEqual({
      from: "2026-09-01",
      to: "2026-09-06"
    });
    vi.useRealTimers();
  });

  it("格式化比例并处理 null 与 0 分母", () => {
    expect(formatAnalyticsRate(0.345)).toBe("34.5%");
    expect(formatAnalyticsRate(0)).toBe("0.0%");
    expect(formatAnalyticsRate(null)).toBe("—");
    expect(formatAnalyticsRateWithDenominator(0.5, 0)).toBe("—");
    expect(formatAnalyticsRateWithDenominator(null, 2)).toBe("—");
  });
});
