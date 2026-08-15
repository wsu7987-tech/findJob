import { describe, expect, it } from "vitest";
import {
  deriveSourceLabel,
  mapApiError,
  mapErrorCategoryLabel,
  mapPoolStatus,
  mapRunStageLabel,
  mapRunTaskTypeLabel,
  mapSourceTypeLabel,
  normalizeRunSnapshot,
  shouldFallbackToPolling
} from "./contract";

describe("mapPoolStatus", () => {
  it("maps contract statuses to UI statuses", () => {
    expect(mapPoolStatus("pending")).toBe("待总结");
    expect(mapPoolStatus("running")).toBe("总结中");
    expect(mapPoolStatus("succeeded")).toBe("已完成");
    expect(mapPoolStatus("failed")).toBe("失败");
  });
});

describe("display label helpers", () => {
  it("maps source types, task types and stages to Chinese labels", () => {
    expect(mapSourceTypeLabel("url")).toBe("网页链接");
    expect(mapSourceTypeLabel("text")).toBe("纯文本");
    expect(mapRunTaskTypeLabel("summary")).toBe("总结");
    expect(mapRunTaskTypeLabel("report")).toBe("周报");
    expect(mapRunStageLabel("summarizing")).toBe("总结中");
    expect(mapRunStageLabel("queued")).toBe("排队中");
  });

  it("maps error categories to readable labels", () => {
    expect(mapErrorCategoryLabel("CONFIG_INVALID")).toBe("配置无效");
    expect(mapErrorCategoryLabel("LLM_FAILED")).toBe("LLM 调用失败");
    expect(mapErrorCategoryLabel(null)).toBe("无");
  });
});

describe("normalizeRunSnapshot", () => {
  it("keeps required run fields and derives progress safely", () => {
    const normalized = normalizeRunSnapshot({
      run_id: "run-1",
      task_type: "summary",
      status: "running",
      stage: "summarizing",
      total_items: 10,
      succeeded_items: 3,
      failed_items: 1,
      skipped_items: 2,
      current_item_id: "pool-2",
      current_item_label: "Entry 2",
      error_category: null,
      error_message: null,
      updated_at: "2026-04-14T12:00:00Z"
    });

    expect(normalized.runId).toBe("run-1");
    expect(normalized.totalProcessed).toBe(6);
    expect(normalized.progressPercent).toBe(60);
  });
});

describe("shouldFallbackToPolling", () => {
  it("falls back after prolonged SSE silence", () => {
    expect(
      shouldFallbackToPolling({
        lastEventAt: 0,
        now: 11_000,
        thresholdMs: 10_000,
        currentMode: "sse"
      })
    ).toBe(true);
  });

  it("does not fall back before threshold", () => {
    expect(
      shouldFallbackToPolling({
        lastEventAt: 5_000,
        now: 12_000,
        thresholdMs: 10_000,
        currentMode: "sse"
      })
    ).toBe(false);
  });
});

describe("mapApiError", () => {
  it("maps contract error categories to understandable messages", () => {
    expect(mapApiError({ error_category: "CONFIG_INVALID" })).toContain("配置");
    expect(mapApiError({ error_category: "FETCH_FAILED" })).toContain("抓取");
    expect(mapApiError({ error_category: "UNKNOWN" })).toContain("未知");
  });
});

describe("deriveSourceLabel", () => {
  it("prefers explicit source_name and falls back to readable file names", () => {
    expect(
      deriveSourceLabel({
        source_type: "url",
        source_name: "official-docs",
        source_value: "https://example.com/post",
        title: null
      })
    ).toBe("official-docs");

    expect(
      deriveSourceLabel({
        source_type: "pdf",
        source_name: null,
        source_value: "D:/notes/weekly-review.pdf",
        title: null
      })
    ).toBe("weekly-review.pdf");
  });
});

describe("additional source type labels", () => {
  it("keeps the pdf source type label readable", () => {
    expect(mapSourceTypeLabel("pdf")).toBe("PDF 文件");
  });
});
