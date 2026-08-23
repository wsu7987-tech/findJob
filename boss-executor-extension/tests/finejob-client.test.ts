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

describe("FineJob执行结果可靠回写", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.restoreAllMocks();
    for (const key of Object.keys(storageData)) delete storageData[key];
    storageData.finejobBossExecutorCredentialsV1 = { executorId: "executor-1", token: "token-1" };
  });

  afterEach(() => {
    vi.clearAllTimers();
    vi.useRealTimers();
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
