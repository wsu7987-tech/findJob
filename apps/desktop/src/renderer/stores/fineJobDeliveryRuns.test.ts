import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";

import { api } from "@/services/api";
import { useFineJobDeliveryRunsStore } from "./fineJobDeliveryRuns";

describe("fineJobDeliveryRuns store", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.restoreAllMocks();
  });

  it("按筛选条件加载动作日志及分页信息", async () => {
    vi.spyOn(api, "listFineJobRecentActionLogs").mockResolvedValue({
      logs: [{
        id: "log-1",
        run_id: null,
        level: "warning",
        action_type: "boss_executor_risk",
        message: "执行器暂停",
        detail: {},
        created_at: "2026-08-31T10:00:00Z",
        source: "main_workflow",
        category: "execution",
        outcome: "warning"
      }],
      total: 12,
      page: 2,
      page_size: 25,
      action_types: ["boss_executor_risk"]
    });
    const store = useFineJobDeliveryRunsStore();

    await store.loadRecentLogs({ category: "execution", page: 2, page_size: 25 });

    expect(store.logTotal).toBe(12);
    expect(store.logPage).toBe(2);
    expect(store.logActionTypes).toEqual(["boss_executor_risk"]);
  });

  it("加载统一运行状态并删除旧任务", async () => {
    const dashboard = {
      generated_at: "2026-08-31T10:00:00Z",
      metrics: { jobs: 10 },
      review_counts: {},
      action_counts: {},
      execution_counts: {},
      capture_counts: {},
      executor: null,
      queue: { actions: [], total: 0 },
      current_action: null,
      recent_issues: [],
      legacy_runs: []
    };
    vi.spyOn(api, "getFineJobOperationsDashboard").mockResolvedValue(dashboard);
    vi.spyOn(api, "deleteFineJobDeliveryRun").mockResolvedValue({
      deleted: true,
      id: "run-1",
      candidates_deleted: 2,
      logs_deleted: 4
    });
    const store = useFineJobDeliveryRunsStore();

    const result = await store.deleteLegacyRun("run-1");

    expect(result.logs_deleted).toBe(4);
    expect(api.getFineJobOperationsDashboard).toHaveBeenCalled();
  });
});
