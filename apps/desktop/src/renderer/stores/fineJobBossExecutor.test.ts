import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";

import { api } from "@/services/api";
import { useFineJobBossExecutorStore } from "./fineJobBossExecutor";

describe("fineJobBossExecutor store", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.restoreAllMocks();
  });

  it("生成配对码并读取执行器队列", async () => {
    vi.spyOn(api, "createFineJobBossPairingCode").mockResolvedValue({
      code: "123456",
      expires_at: "2026-08-23T19:00:00Z"
    });
    vi.spyOn(api, "getFineJobBossExecutorStatus").mockResolvedValue({
      executor: null,
      queue: { actions: [], total: 0 },
      protocol_version: "1.1"
    });
    const store = useFineJobBossExecutorStore();

    await store.createPairingCode();
    await store.load();

    expect(store.pairingCode).toBe("123456");
    expect(store.dashboard?.protocol_version).toBe("1.1");
  });

  it("从历史页请求专用Chrome打开岗位", async () => {
    const openSpy = vi.spyOn(api, "openFineJobBossJob").mockResolvedValue({
      navigation: {
        id: "navigation-1",
        job_id: "history-1",
        source_context: "history",
        target_url: "https://www.zhipin.com/job_detail/job-1.html",
        target_encrypt_job_id: "job-1",
        status: "opened",
        created_at: "2026-08-23T19:00:00Z",
        updated_at: "2026-08-23T19:00:00Z"
      }
    });
    const store = useFineJobBossExecutorStore();

    const task = await store.openJob("history-1", "history");

    expect(openSpy).toHaveBeenCalledWith("history-1", "history");
    expect(task.status).toBe("opened");
    expect(store.openingJobId).toBeNull();
  });

  it("保存未知错误人工核验结果并刷新队列", async () => {
    const verifySpy = vi.spyOn(api, "manualVerifyFineJobBossUnknownAction").mockResolvedValue({
      action: { id: "action-1", execution_state: "succeeded" }
    } as never);
    vi.spyOn(api, "getFineJobBossExecutorStatus").mockResolvedValue({
      executor: null,
      queue: { actions: [], total: 0 },
      protocol_version: "1.1"
    });
    const store = useFineJobBossExecutorStore();

    await store.manualVerifyUnknown("action-1", true);

    expect(verifySpy).toHaveBeenCalledWith("action-1", true);
    expect(store.dashboard?.queue.total).toBe(0);
  });

  it("从运行状态页暂停执行器", async () => {
    const controlSpy = vi.spyOn(api, "controlFineJobBossExecutor").mockResolvedValue({
      executor: null,
      queue: { actions: [], total: 0 },
      protocol_version: "1.1"
    });
    const store = useFineJobBossExecutorStore();

    await store.control("pause");

    expect(controlSpy).toHaveBeenCalledWith("pause");
    expect(store.dashboard?.queue.total).toBe(0);
  });
});
