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
import type { MainWorldExecutionResult } from "../src/finejob/types";

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

  open(): void {
    this.readyState = TestWebSocket.OPEN;
    for (const listener of this.listeners.get("open") ?? []) listener({});
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
          browser_connected: true
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
          browser_connected: true
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
        browser_connected: true
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

    expect(fetchSpy.mock.calls.filter(([url]) => String(url).endsWith("/heartbeat"))).toHaveLength(2);
  });

  it("FineJob暂时不可用时保留accepted结果，恢复后只重试状态回写", async () => {
    const heartbeatBody = {
      executor: {
        id: "executor-1", plugin_version: "0.1.0", protocol_version: "1.1",
        permission_state: "paused", queue_state: "paused", risk_state: "none",
        browser_connected: true
      },
      queue: { actions: [] }
    };
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(response(heartbeatBody));
    const client = new FineJobExecutorClient();
    await client.start();

    const result: MainWorldExecutionResult = {
      actionId: "action-1",
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
      "action-1:1": result
    });

    fetchSpy.mockResolvedValueOnce(response({
      action: {
        id: "action-1", job_id: "job-1", review_item_id: "review-1",
        action_type: "BOSS_DEFAULT_GREETING", status: "succeeded",
        execution_state: "request_accepted", execution_epoch: 1,
        queue_position: 1, page_open_attempts: 1, job_title: "测试岗位",
        company_name: "测试公司", encrypt_job_id: "encrypt-1",
        verification_state: "not_required", verification_method: "none",
        verification_attempts: 0
      }
    }));
    await client.reportExecutionResult(result);

    expect(storageData.finejobBossExecutorPendingResultsV1).toEqual({});
    const completeCalls = fetchSpy.mock.calls.filter(([url]) => String(url).includes("/complete"));
    expect(completeCalls).toHaveLength(2);
  });
});
