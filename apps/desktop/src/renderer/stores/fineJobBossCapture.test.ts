import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";

import { api } from "@/services/api";
import type { FineJobBossCaptureTask } from "@/types";
import { useFineJobBossCaptureStore } from "./fineJobBossCapture";

const task = (updates: Partial<FineJobBossCaptureTask> = {}): FineJobBossCaptureTask => ({
  id: "task-1",
  status: "queued",
  stage: "queued",
  message: "等待执行",
  keyword: "Python",
  city: "上海",
  pages: 1,
  auto_details: true,
  used_current_page: false,
  progress_current: 0,
  progress_total: 1,
  jobs_collected: 0,
  details_completed: 0,
  details_failed: 0,
  duplicate_jobs_count: 0,
  estimated_seconds_min: 750,
  estimated_seconds_max: 1650,
  jobs: [],
  created_at: "2026-08-18T10:00:00Z",
  updated_at: "2026-08-18T10:00:00Z",
  ...updates
});

describe("fineJobBossCapture store", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("polls a long-running capture until progress is completed", async () => {
    vi.spyOn(api, "captureFineJobBossJobs").mockResolvedValue(task());
    vi.spyOn(api, "getFineJobBossCaptureTask")
      .mockResolvedValueOnce(
        task({
          status: "running",
          stage: "details_collecting",
          progress_current: 1,
          progress_total: 2,
          jobs_collected: 2,
          details_completed: 1
        })
      )
      .mockResolvedValueOnce(
        task({
          status: "completed",
          stage: "details_completed",
          progress_current: 2,
          progress_total: 2,
          jobs_collected: 2,
          details_completed: 2
        })
      );

    const store = useFineJobBossCaptureStore();
    await store.capture({
      keyword: "Python",
      city: "上海",
      pages: 1,
      include_details: true,
      prefer_current_page: true
    });
    await vi.advanceTimersByTimeAsync(500);
    expect(store.task?.status).toBe("running");
    expect(store.task?.details_completed).toBe(1);

    await vi.advanceTimersByTimeAsync(1000);
    expect(store.task?.status).toBe("completed");
    expect(store.task?.details_completed).toBe(2);
    expect(api.getFineJobBossCaptureTask).toHaveBeenCalledTimes(2);
  });

  it("passes force when recapturing a completed detail", async () => {
    const captureSpy = vi
      .spyOn(api, "captureSelectedFineJobBossDetails")
      .mockResolvedValue(task({ status: "queued", stage: "details_queued" }));
    const store = useFineJobBossCaptureStore();
    store.task = task({
      status: "completed",
      jobs: [{ job_id: "job-1", detail_status: "completed", detail: { jd: "旧详情" } }]
    });

    await store.captureDetails(["job-1"], true);

    expect(captureSpy).toHaveBeenCalledWith("task-1", ["job-1"], true);
    store.stopPolling();
  });

  it("continues and stops the same list capture task", async () => {
    const continueSpy = vi
      .spyOn(api, "continueFineJobBossCapture")
      .mockResolvedValue(task({ status: "queued", stage: "list_continue_queued", pages: 3 }));
    const stopSpy = vi
      .spyOn(api, "stopFineJobBossCaptureTask")
      .mockResolvedValue(task({
        status: "running",
        stage: "list_continuing",
        pages: 3,
        stop_requested: true
      }));
    const store = useFineJobBossCaptureStore();
    store.task = task({
      status: "completed",
      stage: "list_completed",
      continuation_available: true
    });

    await store.continueCapture(3);
    await store.stopCaptureTask();

    expect(continueSpy).toHaveBeenCalledWith("task-1", 3);
    expect(stopSpy).toHaveBeenCalledWith("task-1");
    expect(store.task?.stop_requested).toBe(true);
    store.stopPolling();
  });
});
