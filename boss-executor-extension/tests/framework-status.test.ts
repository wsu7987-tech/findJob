import { describe, expect, it } from "vitest";

import {
  FRAMEWORK_MODE,
  createFrameworkStatus,
  isMainWorldStatus,
  refreshFrameworkDetail
} from "../src/executor/framework-mode";

describe("任务执行器状态", () => {
  it("启用受控真实动作和FineJob连接", () => {
    expect(FRAMEWORK_MODE.realActionsEnabled).toBe(true);
    expect(FRAMEWORK_MODE.fineJobConnected).toBe(true);
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

  it("三层正常时显示等待FineJob通信", () => {
    const status = createFrameworkStatus("/web/geek/jobs");
    status.background = "ready";
    status.mainWorld = "ready";
    refreshFrameworkDetail(status);
    expect(status.detail).toContain("等待FineJob通信");
  });
});
