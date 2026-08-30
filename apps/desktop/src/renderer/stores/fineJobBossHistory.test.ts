import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";

import { api } from "@/services/api";
import type { FineJobBossCaptureTask } from "@/types";
import { useFineJobBossHistoryStore } from "./fineJobBossHistory";

const detailTask = (
  updates: Partial<FineJobBossCaptureTask> = {}
): FineJobBossCaptureTask => ({
  id: "detail-task-1",
  status: "queued",
  stage: "details_queued",
  message: "等待执行",
  keyword: "Python 开发",
  city: "上海",
  pages: 1,
  auto_details: false,
  used_current_page: false,
  progress_current: 0,
  progress_total: 1,
  jobs_collected: 1,
  details_completed: 0,
  details_failed: 0,
  duplicate_jobs_count: 1,
  estimated_seconds_min: 25,
  estimated_seconds_max: 55,
  jobs: [],
  created_at: "2026-08-19T10:00:00Z",
  updated_at: "2026-08-19T10:00:00Z",
  ...updates
});

describe("fineJobBossHistory store", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.useFakeTimers();
    vi.restoreAllMocks();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("loads filtered and paginated history", async () => {
    const listSpy = vi.spyOn(api, "listFineJobBossCaptureHistory").mockResolvedValue({
      items: [
        {
          id: "history-1",
          job_id: "job-1",
          title: "Python 开发",
          boss_name: "示例科技",
          search_keyword: "Python 后端",
          first_collected_at: "2026-08-18T10:00:00Z",
          last_collected_at: "2026-08-19T10:00:00Z",
          collect_count: 2,
          latest_capture_id: "capture-2"
        }
      ],
      total: 1,
      page: 2,
      page_size: 10
    });
    const store = useFineJobBossHistoryStore();
    store.page = 2;
    store.pageSize = 10;

    await store.load({
      query: "Python",
      search_keyword: "Python 后端",
      sort_by: "collect_count",
      sort_order: "desc"
    });

    expect(listSpy).toHaveBeenCalledWith({
      page: 2,
      page_size: 10,
      query: "Python",
      search_keyword: "Python 后端",
      sort_by: "collect_count",
      sort_order: "desc"
    });
    expect(store.total).toBe(1);
    expect(store.items[0].collect_count).toBe(2);
  });

  it("polls a standalone history detail task", async () => {
    vi.spyOn(api, "captureFineJobBossHistoryDetails").mockResolvedValue(detailTask());
    vi.spyOn(api, "getFineJobBossCaptureTask").mockResolvedValue(
      detailTask({
        status: "completed",
        stage: "details_completed",
        progress_current: 1,
        details_completed: 1
      })
    );
    const store = useFineJobBossHistoryStore();

    await store.captureDetails("history-1");
    expect(store.detailJobId).toBe("history-1");

    await vi.advanceTimersByTimeAsync(500);
    expect(store.detailTask?.status).toBe("completed");
    expect(store.detailJobId).toBeNull();
  });
});
