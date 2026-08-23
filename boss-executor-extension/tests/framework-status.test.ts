import { describe, expect, it } from "vitest";

import {
  FRAMEWORK_MODE,
  createFrameworkStatus,
  isMainWorldStatus,
  refreshFrameworkDetail
} from "../src/executor/framework-mode";

describe("只读框架状态", () => {
  it("永久禁用真实动作和 FineJob 连接", () => {
    expect(FRAMEWORK_MODE.realActionsEnabled).toBe(false);
    expect(FRAMEWORK_MODE.fineJobConnected).toBe(false);
  });

  it("只接受完整的 MAIN World 状态", () => {
    expect(
      isMainWorldStatus({
        component: "main-world",
        frameworkMode: true,
        hostname: "www.zhipin.com",
        pathname: "/web/geek/jobs",
        readyState: "complete"
      })
    ).toBe(true);
    expect(isMainWorldStatus({ component: "main-world", frameworkMode: true })).toBe(false);
  });

  it("三层正常时显示只读成功说明", () => {
    const status = createFrameworkStatus("/web/geek/jobs");
    status.background = "ready";
    status.mainWorld = "ready";
    refreshFrameworkDetail(status);
    expect(status.detail).toContain("三层框架通信正常");
  });
});
