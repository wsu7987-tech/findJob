import { beforeEach, describe, expect, it, vi } from "vitest";

const { readBossPageIdentity } = vi.hoisted(() => ({ readBossPageIdentity: vi.fn() }));
vi.mock("../src/platform/boss/read-only-probe", () => ({ readBossPageIdentity }));

import { executeDefaultGreeting } from "../src/platform/boss/default-greeting";

const command = {
  type: "BOSS_DEFAULT_GREETING" as const,
  taskId: "task-1",
  executionEpoch: 0,
  encryptJobId: "job-1"
};

const identity = (overrides: Record<string, unknown> = {}) => ({
  component: "boss-page-identity",
  pathname: "/job_detail/job-1.html",
  pageKind: "detail",
  state: "ready",
  loggedIn: true,
  job: {
    encryptJobId: "job-1",
    securityId: "security-1",
    encryptBossId: "boss-1",
    jobName: "测试岗位",
    bossName: "招聘经理",
    bossTitle: "招聘经理",
    lid: "lid-1",
    contacted: false,
    identitySource: "standalone-job-info",
    bossIdentifierVerified: false
  },
  reason: "岗位身份已识别",
  ...overrides
});

describe("BOSS默认招呼任务", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    readBossPageIdentity.mockReset();
    (window as unknown as { Cookie?: { get(name: string): string } }).Cookie = {
      get: () => "boss-token"
    };
  });

  it("岗位不匹配时不调用平台接口", async () => {
    readBossPageIdentity.mockReturnValue(identity({ job: { ...identity().job, encryptJobId: "another-job" } }));
    const fetchSpy = vi.spyOn(globalThis, "fetch");

    const result = await executeDefaultGreeting(command);

    expect(result.statusCode).toBe("PRE_DISPATCH_PAGE_MISMATCH");
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("平台请求异常只调用一次并返回未知", async () => {
    readBossPageIdentity.mockReturnValue(identity());
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("network"));

    const result = await executeDefaultGreeting(command);

    expect(fetchSpy).toHaveBeenCalledTimes(1);
    expect(result.outcome).toBe("unknown");
    expect(result.statusCode).toBe("BOSS_REQUEST_NETWORK_UNKNOWN");
  });

  it("平台成功响应后回传成功", async () => {
    readBossPageIdentity.mockReturnValue(identity());
    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      status: 200,
      json: vi.fn().mockResolvedValue({ code: 0, message: "Success" })
    } as unknown as Response);

    const result = await executeDefaultGreeting(command);

    expect(result.outcome).toBe("accepted");
    expect(result.statusCode).toBe("BOSS_REQUEST_ACCEPTED");
  });
});
