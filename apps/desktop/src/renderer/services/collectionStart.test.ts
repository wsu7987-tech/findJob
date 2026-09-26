// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { createCollectionStartCoordinator } from "./collectionStart";

beforeEach(() => { localStorage.clear(); vi.useFakeTimers(); });
afterEach(() => { vi.clearAllTimers(); vi.useRealTimers(); });

it("未知响应与查询 404 保留意图，重新进入只查询同一身份", async () => {
  const request = vi.fn().mockRejectedValue(new Error("response lost"));
  const dependencies = { request, getOrigin: async () => "http://isolated-backend" };
  const first = createCollectionStartCoordinator(dependencies);
  await expect(first.start("/start", { pages: 1 }, "custom.capture")).rejects.toThrow("response lost");
  const operationId = first.state.intent?.operation_id;
  expect(operationId).toBeTruthy();
  expect(first.state.submitting).toBe(false);
  first.dispose();
  const reopened = createCollectionStartCoordinator(dependencies);
  await reopened.restore();
  expect(reopened.state.intent?.operation_id).toBe(operationId);
  await expect(reopened.start("/start", {}, "custom.capture")).rejects.toThrow("待确认");
  expect(request.mock.calls.filter(([path]) => path === "/start")).toHaveLength(1);
  reopened.dispose();
});

it("只有服务端 rejected 或 started 才释放未决意图", async () => {
  const request = vi.fn().mockRejectedValue(new Error("timeout"));
  const coordinator = createCollectionStartCoordinator({ request, getOrigin: async () => "http://isolated-backend" });
  await expect(coordinator.start("/start", {}, "smart.create")).rejects.toThrow();
  const operationId = coordinator.state.intent!.operation_id;
  request.mockResolvedValue({ response_type: "operation_receipt", operation_id: operationId, status: "unknown", phase: "dispatching" });
  await coordinator.resolve();
  expect(coordinator.state.intent).not.toBeNull();
  request.mockResolvedValue({ response_type: "operation_receipt", operation_id: operationId, status: "rejected", phase: "finished" });
  await coordinator.resolve();
  expect(coordinator.state.intent).toBeNull();
  expect(localStorage.length).toBe(0);
  coordinator.dispose();
});
