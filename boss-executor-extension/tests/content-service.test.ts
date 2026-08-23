import { describe, expect, it, vi } from "vitest";

import { createFrameworkStatus } from "../src/executor/framework-mode";
import type { BackgroundService } from "../src/message/background";
import { ContentService } from "../src/message/content";

const createBackground = (ok = true): BackgroundService =>
  ({
    health: vi.fn().mockResolvedValue({
      ok,
      component: "background",
      frameworkMode: true,
      realActionsEnabled: false
    })
  }) as unknown as BackgroundService;

describe("Content 服务", () => {
  it("串联 Background 与 MAIN World 的只读健康状态", async () => {
    const status = createFrameworkStatus("/");
    const service = new ContentService(createBackground(), status);

    await service.refreshBackground();
    await service.reportMainWorldReady({
      component: "main-world",
      frameworkMode: true,
      hostname: "www.zhipin.com",
      pathname: "/web/geek/jobs",
      readyState: "interactive"
    });

    expect(status.background).toBe("ready");
    expect(status.mainWorld).toBe("ready");
    expect(status.page).toBe("/web/geek/jobs");
  });

  it("拒绝伪造或缺字段的 MAIN World 状态", async () => {
    const status = createFrameworkStatus("/");
    const service = new ContentService(createBackground(), status);

    await expect(service.reportMainWorldReady({ component: "main-world" })).rejects.toThrow(
      "状态载荷无效"
    );
    expect(status.mainWorld).toBe("error");
  });

  it("只接受校验通过的岗位只读快照", async () => {
    const status = createFrameworkStatus("/");
    const service = new ContentService(createBackground(), status);
    await service.reportBossSnapshot({
      component: "boss-read-only-probe",
      readOnly: true,
      pathname: "/web/geek/jobs",
      pageKind: "search",
      state: "waiting",
      loggedIn: true,
      jobCount: 10,
      job: null,
      reason: "等待当前岗位详情",
      observedAt: 1
    });

    expect(status.bossProbe).toBe("waiting");
    expect(status.bossSnapshot?.jobCount).toBe(10);

    await expect(service.reportBossSnapshot({ readOnly: true })).rejects.toThrow(
      "岗位识别载荷无效"
    );
    expect(status.bossProbe).toBe("unavailable");

    await expect(
      service.reportBossSnapshot({
        component: "boss-read-only-probe",
        readOnly: true,
        pathname: "/web/geek/jobs",
        pageKind: "search",
        state: "waiting",
        loggedIn: true,
        jobCount: 1,
        job: {
          encryptJobId: "job-1",
          securityId: "security-1",
          encryptBossId: "boss-1",
          jobName: "前端工程师",
          bossName: "王经理",
          bossTitle: "招聘经理",
          lid: "lid-1",
          contacted: false,
          identitySource: "vue-list-detail",
          bossIdentifierVerified: true
        },
        reason: "等待状态不应携带岗位",
        observedAt: 2
      })
    ).rejects.toThrow("岗位识别载荷无效");
  });
});
