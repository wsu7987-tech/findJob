import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { storageData, browser } = vi.hoisted(() => {
  const data: Record<string, unknown> = {};
  const browserMock = {
    storage: {
      local: {
        get: vi.fn(async (keys: string | string[]) => {
          const selected = Array.isArray(keys) ? keys : [keys];
          return Object.fromEntries(selected.filter((key) => key in data).map((key) => [key, data[key]]));
        }),
        set: vi.fn(async (values: Record<string, unknown>) => { Object.assign(data, values); })
      }
    },
    tabs: { query: vi.fn().mockResolvedValue([]), sendMessage: vi.fn() }
  };
  (globalThis as Record<string, unknown>).__fineJobTestBrowser = browserMock;
  return { storageData: data, browser: browserMock };
});

import { FineJobExecutorClient } from "../src/finejob/client";
import type { FineJobQueueAction, MainWorldExecutionResult } from "../src/finejob/types";

const response = (body: unknown) => ({
  ok: true,
  status: 200,
  json: vi.fn().mockResolvedValue(body)
}) as unknown as Response;

class TestWebSocket {
  static readonly OPEN = 1;
  static readonly CONNECTING = 0;
  readonly url: string;
  readyState = TestWebSocket.CONNECTING;
  readonly sent: string[] = [];
  private readonly listeners = new Map<string, Array<(event: unknown) => void>>();

  constructor(url: string) {
    this.url = url;
  }

  addEventListener(type: string, listener: (event: unknown) => void): void {
    const current = this.listeners.get(type) ?? [];
    current.push(listener);
    this.listeners.set(type, current);
  }

  close(): void {
    this.readyState = 3;
  }

  send(data: string): void {
    this.sent.push(data);
  }

  open(): void {
    this.readyState = TestWebSocket.OPEN;
    for (const listener of this.listeners.get("open") ?? []) listener({});
  }

  message(data: unknown): void {
    const event = { data: JSON.stringify(data) };
    for (const listener of this.listeners.get("message") ?? []) listener(event);
  }
}

describe("FineJob执行结果可靠回写", () => {
  let sockets: TestWebSocket[];

  beforeEach(() => {
    vi.useFakeTimers();
    vi.restoreAllMocks();
    sockets = [];
    vi.stubGlobal("WebSocket", class extends TestWebSocket {
      constructor(url: string) {
        super(url);
        sockets.push(this);
      }
    });
    for (const key of Object.keys(storageData)) delete storageData[key];
    storageData.finejobBossExecutorCredentialsV1 = { executorId: "executor-1", token: "token-1" };
  });

  afterEach(() => {
    vi.clearAllTimers();
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("输入配对码后等待主动心跳完成", async () => {
    delete storageData.finejobBossExecutorCredentialsV1;
    const fetchSpy = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(response({ executor_id: "executor-1", token: "token-1" }))
      .mockResolvedValueOnce(response({
        executor: {
          id: "executor-1", plugin_version: "0.1.0", protocol_version: "1.1",
          permission_state: "paused", queue_state: "paused", risk_state: "none",
          browser_connected: true, task_cooldown_max_seconds: 4, page_load_wait_max_seconds: 3,
          runtime_phase: "idle"
        },
        queue: { actions: [] }
      }));
    const client = new FineJobExecutorClient();

    await client.pair("123456");

    expect(fetchSpy.mock.calls.map(([url]) => String(url))).toEqual([
      expect.stringContaining("/pair"),
      expect.stringContaining("/heartbeat")
    ]);
    expect(client.getState().connected).toBe(true);
  });

  it("配对等待后台初始化，避免初始化覆盖新连接", async () => {
    delete storageData.finejobBossExecutorCredentialsV1;
    let releaseStorage!: () => void;
    const storageReady = new Promise<void>((resolve) => { releaseStorage = resolve; });
    browser.storage.local.get.mockImplementationOnce(async (keys: string | string[]) => {
      await storageReady;
      const selected = Array.isArray(keys) ? keys : [keys];
      return Object.fromEntries(selected.filter((key) => key in storageData).map((key) => [key, storageData[key]]));
    });
    const fetchSpy = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(response({ executor_id: "executor-1", token: "token-1" }))
      .mockResolvedValueOnce(response({
        executor: {
          id: "executor-1", plugin_version: "0.1.0", protocol_version: "1.1",
          permission_state: "paused", queue_state: "paused", risk_state: "none",
          browser_connected: true, task_cooldown_max_seconds: 4, page_load_wait_max_seconds: 3,
          runtime_phase: "idle"
        },
        queue: { actions: [] }
      }));
    const client = new FineJobExecutorClient();
    const startPromise = client.start();
    const pairPromise = client.pair("123456");

    await Promise.resolve();
    expect(fetchSpy).not.toHaveBeenCalled();
    releaseStorage();
    await Promise.all([startPromise, pairPromise]);

    expect(fetchSpy.mock.calls.map(([url]) => String(url))).toEqual([
      expect.stringContaining("/pair"),
      expect.stringContaining("/heartbeat")
    ]);
    expect(client.getState().connected).toBe(true);
  });

  it("控制通道建立后主动发起一次FineJob心跳", async () => {
    const heartbeatBody = {
      executor: {
        id: "executor-1", plugin_version: "0.1.0", protocol_version: "1.1",
        permission_state: "paused", queue_state: "paused", risk_state: "none",
        browser_connected: true, task_cooldown_max_seconds: 4, page_load_wait_max_seconds: 3,
        runtime_phase: "idle"
      },
      queue: { actions: [] }
    };
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(response(heartbeatBody));
    const client = new FineJobExecutorClient();

    await client.start();
    expect(sockets).toHaveLength(1);
    expect(fetchSpy.mock.calls.filter(([url]) => String(url).endsWith("/heartbeat"))).toHaveLength(1);

    const socket = sockets[0];
    if (!socket) throw new Error("测试 WebSocket 未建立");
    socket.open();
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();

    expect(fetchSpy.mock.calls.filter(([url]) => String(url).endsWith("/heartbeat"))).toHaveLength(2);
  });

  it("FineJob暂时不可用时保留accepted结果，恢复后只重试状态回写", async () => {
    const heartbeatBody = {
      executor: {
        id: "executor-1", plugin_version: "0.1.0", protocol_version: "1.1",
        permission_state: "paused", queue_state: "paused", risk_state: "none",
        browser_connected: true, task_cooldown_max_seconds: 4, page_load_wait_max_seconds: 3,
        runtime_phase: "idle"
      },
      queue: { actions: [] }
    };
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(response(heartbeatBody));
    const client = new FineJobExecutorClient();
    await client.start();

    const result: MainWorldExecutionResult = {
      taskId: "task-1",
      executionEpoch: 1,
      outcome: "accepted",
      contacted: null,
      statusCode: "BOSS_REQUEST_ACCEPTED",
      message: "平台已受理",
      evidence: { responseCode: 0 }
    };
    fetchSpy.mockRejectedValueOnce(new Error("offline"));
    await client.reportExecutionResult(result);

    expect(storageData.finejobBossExecutorPendingResultsV1).toEqual({
      "task-1:1": result
    });

    fetchSpy.mockResolvedValueOnce(response({
      task: {
        id: "task-1", job_id: "job-1", review_item_id: "review-1",
        action_type: "BOSS_DEFAULT_GREETING", status: "succeeded",
        execution_state: "succeeded", execution_epoch: 1,
        job_title: "测试岗位", company_name: "测试公司",
        encrypt_job_id: "encrypt-1"
      },
      queue: { actions: [] }
    }));
    await client.reportExecutionResult(result);

    expect(storageData.finejobBossExecutorPendingResultsV1).toEqual({});
    const completeCalls = fetchSpy.mock.calls.filter(([url]) => String(url).includes("/complete"));
    expect(completeCalls).toHaveLength(2);
  });

  it("运行通道确认结果后清理本地待同步结果", async () => {
    const heartbeatBody = {
      executor: {
        id: "executor-1", plugin_version: "0.1.0", protocol_version: "1.1",
        permission_state: "paused", queue_state: "running", risk_state: "none",
        browser_connected: true, task_cooldown_max_seconds: 4, page_load_wait_max_seconds: 3,
        runtime_phase: "idle"
      },
      queue: { actions: [] }
    };
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(response(heartbeatBody));
    const client = new FineJobExecutorClient();
    await client.start();
    const socket = sockets[0];
    if (!socket) throw new Error("测试 WebSocket 未建立");
    socket.readyState = TestWebSocket.OPEN;

    const result: MainWorldExecutionResult = {
      taskId: "task-1",
      executionEpoch: 1,
      outcome: "succeeded",
      contacted: null,
      statusCode: "TEST_DELAY_COMPLETED",
      message: "测试完成",
      evidence: { delaySeconds: 3 }
    };
    await client.reportExecutionResult(result);

    expect(storageData.finejobBossExecutorPendingResultsV1).toEqual({ "task-1:1": result });
    expect(JSON.parse(socket.sent.at(-1) ?? "{}")).toMatchObject({
      type: "task_succeeded",
      task_id: "task-1",
      execution_epoch: 1
    });

    socket.message({
      type: "task_result_synced",
      task_id: "task-1",
      execution_epoch: 1,
      queue: { actions: [] }
    });
    await Promise.resolve();

    expect(storageData.finejobBossExecutorPendingResultsV1).toEqual({});
    await vi.advanceTimersByTimeAsync(5_000);
    const completeCalls = fetchSpy.mock.calls.filter(([url]) => String(url).includes("/complete"));
    expect(completeCalls).toHaveLength(0);
  });

  it("运行通道未回执时使用补偿接口同步结果", async () => {
    const heartbeatBody = {
      executor: {
        id: "executor-1", plugin_version: "0.1.0", protocol_version: "1.1",
        permission_state: "paused", queue_state: "running", risk_state: "none",
        browser_connected: true, task_cooldown_max_seconds: 4, page_load_wait_max_seconds: 3,
        runtime_phase: "idle"
      },
      queue: { actions: [] }
    };
    const fetchSpy = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(response(heartbeatBody))
      .mockResolvedValueOnce(response(heartbeatBody))
      .mockResolvedValueOnce(response({
        task: {
          id: "task-1", job_id: "job-1", review_item_id: "review-1",
          action_type: "BOSS_DEFAULT_GREETING", status: "succeeded",
          execution_state: "succeeded", execution_epoch: 1,
          job_title: "测试岗位", company_name: "测试公司",
          encrypt_job_id: "encrypt-1"
        },
        queue: { actions: [] }
      }));
    const client = new FineJobExecutorClient();
    await client.start();
    const socket = sockets[0];
    if (!socket) throw new Error("测试 WebSocket 未建立");
    socket.readyState = TestWebSocket.OPEN;

    const result: MainWorldExecutionResult = {
      taskId: "task-1",
      executionEpoch: 1,
      outcome: "succeeded",
      contacted: null,
      statusCode: "TEST_DELAY_COMPLETED",
      message: "测试完成",
      evidence: { delaySeconds: 3 }
    };
    await client.reportExecutionResult(result);
    await vi.advanceTimersByTimeAsync(5_000);

    expect(storageData.finejobBossExecutorPendingResultsV1).toEqual({});
    const completeCalls = fetchSpy.mock.calls.filter(([url]) => String(url).includes("/complete"));
    expect(completeCalls).toHaveLength(1);
  });

  it("收到运行中队列且没有可用BOSS标签页时请求打开任务页", async () => {
    const task: FineJobQueueAction = {
      id: "task-queue-1",
      job_id: "job-1",
      review_item_id: "review-1",
      action_type: "start_conversation",
      task_type: "TEST_DELAY",
      status: "queued",
      execution_state: "queued",
      execution_epoch: 0,
      job_title: "测试岗位",
      company_name: "测试公司",
      encrypt_job_id: "encrypt-1",
      close_page_after_completion: false,
      delay_seconds: 5
    };
    vi.spyOn(globalThis, "fetch").mockResolvedValue(response({
      executor: {
        id: "executor-1", plugin_version: "0.1.0", protocol_version: "1.1",
        permission_state: "paused", queue_state: "running", risk_state: "none",
        browser_connected: true, task_cooldown_max_seconds: 4, page_load_wait_max_seconds: 3,
        runtime_phase: "idle"
      },
      queue: { actions: [] }
    }));
    browser.tabs.query.mockResolvedValueOnce([]);
    const client = new FineJobExecutorClient();
    await client.start();
    const socket = sockets[0];
    if (!socket) throw new Error("测试 WebSocket 未建立");
    socket.readyState = TestWebSocket.OPEN;
    socket.sent.length = 0;

    socket.message({ type: "task_queue", tasks: [task] });
    await Promise.resolve();
    await Promise.resolve();

    expect(socket.sent.map((item) => JSON.parse(item))).toContainEqual(
      expect.objectContaining({ type: "open_task_page" })
    );
  });

  it("任务回写确认后先进入任务间隔冷却再请求下一页", async () => {
    const nextTask: FineJobQueueAction = {
      id: "task-next-1",
      job_id: "job-1",
      review_item_id: "review-1",
      action_type: "start_conversation",
      task_type: "TEST_DELAY",
      status: "queued",
      execution_state: "queued",
      execution_epoch: 0,
      job_title: "下一个测试岗位",
      company_name: "测试公司",
      encrypt_job_id: "encrypt-1",
      close_page_after_completion: false,
      delay_seconds: 5
    };
    vi.spyOn(Math, "random").mockReturnValue(0.5);
    vi.spyOn(globalThis, "fetch").mockResolvedValue(response({
      executor: {
        id: "executor-1", plugin_version: "0.1.0", protocol_version: "1.1",
        permission_state: "paused", queue_state: "running", risk_state: "none",
        browser_connected: true, task_cooldown_max_seconds: 8, page_load_wait_max_seconds: 3,
        runtime_phase: "idle"
      },
      queue: { actions: [] }
    }));
    browser.tabs.query.mockResolvedValue([]);
    const client = new FineJobExecutorClient();
    await client.start();
    const socket = sockets[0];
    if (!socket) throw new Error("测试 WebSocket 未建立");
    socket.readyState = TestWebSocket.OPEN;
    socket.sent.length = 0;

    socket.message({
      type: "task_result_synced",
      task_id: "task-done-1",
      execution_epoch: 1,
      queue: { actions: [nextTask] }
    });
    await Promise.resolve();

    expect(socket.sent.map((item) => JSON.parse(item))).toContainEqual(expect.objectContaining({
      type: "runtime_state",
      phase: "task_cooldown",
      detail: "任务间隔冷却等待 6 秒"
    }));

    await vi.advanceTimersByTimeAsync(5_999);
    expect(socket.sent.map((item) => JSON.parse(item))).not.toContainEqual(
      expect.objectContaining({ type: "open_task_page" })
    );

    await vi.advanceTimersByTimeAsync(1);
    await Promise.resolve();
    const sentMessages = socket.sent.map((item) => JSON.parse(item));
    expect(sentMessages).toContainEqual(expect.objectContaining({ type: "runtime_state", phase: "idle" }));
    expect(sentMessages).toContainEqual(expect.objectContaining({ type: "open_task_page" }));
  });

  it("测试任务按队列下发的运行时间延迟回传成功", async () => {
    const task: FineJobQueueAction = {
      id: "task-delay-1",
      job_id: "job-1",
      review_item_id: "review-1",
      action_type: "start_conversation",
      task_type: "TEST_DELAY",
      status: "queued",
      execution_state: "queued",
      execution_epoch: 2,
      job_title: "测试岗位",
      company_name: "测试公司",
      encrypt_job_id: "encrypt-1",
      close_page_after_completion: true,
      delay_seconds: 7
    };
    vi.spyOn(globalThis, "fetch").mockResolvedValue(response({
      executor: {
        id: "executor-1", plugin_version: "0.1.0", protocol_version: "1.1",
        permission_state: "paused", queue_state: "running", risk_state: "none",
        browser_connected: true, task_cooldown_max_seconds: 4, page_load_wait_max_seconds: 3,
        runtime_phase: "idle"
      },
      queue: { actions: [task] }
    }));
    const client = new FineJobExecutorClient();
    await client.start();
    const socket = sockets[0];
    if (!socket) throw new Error("测试 WebSocket 未建立");
    socket.readyState = TestWebSocket.OPEN;
    socket.sent.length = 0;

    socket.message({ type: "page_opened", task_id: task.id, success: true, page: {} });
    await Promise.resolve();
    expect(socket.sent.map((item) => JSON.parse(item))).toContainEqual(
      expect.objectContaining({ type: "match_task", task_id: task.id })
    );
    socket.message({
      type: "task_match_synced",
      task_id: task.id,
      execution_epoch: task.execution_epoch,
      task: { ...task, status: "leased", execution_state: "running" },
      queue: { actions: [{ ...task, status: "leased", execution_state: "running" }] }
    });
    await Promise.resolve();

    await vi.advanceTimersByTimeAsync(6_999);
    expect(socket.sent.map((item) => JSON.parse(item))).not.toContainEqual(
      expect.objectContaining({ type: "task_succeeded", task_id: task.id })
    );

    await vi.advanceTimersByTimeAsync(1);
    await Promise.resolve();
    const sentMessages = socket.sent.map((item) => JSON.parse(item));
    expect(sentMessages).toContainEqual(expect.objectContaining({
      type: "task_succeeded",
      task_id: task.id,
      execution_epoch: 2,
      execution_result: "测试任务已等待 7 秒并完成",
      platform_result: { delaySeconds: 7, closePageAfterCompletion: true }
    }));
  });
});
