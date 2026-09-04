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

  it("从运行状态页同步插件暂停状态", async () => {
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

  it("保存执行器等待配置并在连接后隐藏配对码", async () => {
    vi.spyOn(api, "createFineJobBossPairingCode").mockResolvedValue({
      code: "123456",
      expires_at: "2026-08-23T19:00:00Z"
    });
    const settingsSpy = vi.spyOn(api, "updateFineJobBossExecutorSettings").mockResolvedValue({
      executor: {
        id: "executor-1",
        label: "FineJob BOSS 执行器",
        protocol_version: "1.1",
        plugin_version: "0.1.0",
        capabilities: [],
        queue_state: "running",
        risk_state: "none",
        browser_connected: true,
        last_heartbeat_at: "2026-09-04T00:00:00Z",
        task_cooldown_max_seconds: 8,
        page_load_wait_max_seconds: 5,
        runtime_phase: "idle",
        runtime_detail: "",
        runtime_until_at: null,
        updated_at: "2026-09-04T00:00:00Z"
      },
      queue: { actions: [], total: 0 },
      protocol_version: "1.1"
    });
    const store = useFineJobBossExecutorStore();

    await store.createPairingCode();
    await store.updateSettings({
      task_cooldown_max_seconds: 8,
      page_load_wait_max_seconds: 5
    });

    expect(settingsSpy).toHaveBeenCalledWith({
      task_cooldown_max_seconds: 8,
      page_load_wait_max_seconds: 5
    });
    expect(store.pairingCode).toBeNull();
    expect(store.dashboard?.executor?.task_cooldown_max_seconds).toBe(8);
  });

  it("加载测试岗位并创建可选择关闭页面的测试任务", async () => {
    vi.spyOn(api, "listFineJobBossExecutorTestJobs").mockResolvedValue({
      jobs: [{
        id: "system-test-job-1", encrypt_job_id: "test-id", title: "测试岗位 1",
        company_name: "FineJob 系统测试", job_link: "https://www.zhipin.com/",
        created_at: "2026-09-04T00:00:00Z", updated_at: "2026-09-04T00:00:00Z"
      }]
    });
    vi.spyOn(api, "createFineJobBossExecutorTestTask").mockResolvedValue({
      task: {
        id: "test-task-1", job_id: "system-test-job-1", review_item_id: "review-1",
        action_type: "start_conversation", task_type: "TEST_DELAY", status: "queued",
        execution_state: "queued", execution_epoch: 0, job_title: "测试岗位 1",
        company_name: "FineJob 系统测试", encrypt_job_id: "test-id",
        close_page_after_completion: true, delay_seconds: 8
      }
    });
    vi.spyOn(api, "getFineJobBossExecutorStatus").mockResolvedValue({
      executor: null, queue: { actions: [], total: 0 }, protocol_version: "1.1"
    });
    const store = useFineJobBossExecutorStore();

    await store.loadTestJobs();
    await store.createTestTask({
      job_id: "system-test-job-1",
      close_page_after_completion: true,
      delay_seconds: 8
    });

    expect(store.testJobs).toHaveLength(1);
    expect(api.createFineJobBossExecutorTestTask).toHaveBeenCalledWith({
      job_id: "system-test-job-1", close_page_after_completion: true, delay_seconds: 8
    });
  });
});
