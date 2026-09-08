import { describe, expect, it } from "vitest";

import { fineJobExecutorStatusLabel } from "./fineJobExecutorPresentation";

const executor = {
  id: "executor-1", label: "BOSS", protocol_version: "1.1", plugin_version: "test",
  capabilities: [], queue_state: "running" as const, risk_state: "none", browser_connected: true,
  task_cooldown_max_seconds: 4, page_load_wait_max_seconds: 3, updated_at: "2026-09-09T00:00:00Z"
};

describe("执行器运行状态展示", () => {
  it("区分主动断开与普通连接中断", () => {
    expect(fineJobExecutorStatusLabel({ ...executor, browser_connected: false, pairing_state: "revoked" }, false)).toBe("已断开，需重新配对");
    expect(fineJobExecutorStatusLabel({ ...executor, browser_connected: false, pairing_state: "paired" }, false)).toBe("连接已中断");
  });

  it("按真实运行字段展示冷却和执行状态", () => {
    expect(fineJobExecutorStatusLabel({ ...executor, runtime_phase: "task_cooldown" }, true)).toBe("冷却中");
    expect(fineJobExecutorStatusLabel(executor, true)).toBe("执行中");
  });
});
