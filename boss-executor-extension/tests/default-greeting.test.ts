import { beforeEach, describe, expect, it, vi } from "vitest";

const { readBossPageSnapshot } = vi.hoisted(() => ({ readBossPageSnapshot: vi.fn() }));
vi.mock("../src/platform/boss/read-only-probe", () => ({ readBossPageSnapshot }));

import { executeDefaultGreeting } from "../src/platform/boss/default-greeting";

const command = {
  type: "BOSS_DEFAULT_GREETING" as const,
  actionId: "action-1",
  executionEpoch: 1,
  encryptJobId: "job-1"
};

describe("BOSS默认招呼动作", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    readBossPageSnapshot.mockReset();
    (window as unknown as { Cookie?: { get(name: string): string } }).Cookie = {
      get: () => "boss-token"
    };
  });

  it("真实请求前岗位不匹配时不调用平台接口", async () => {
    readBossPageSnapshot.mockReturnValue({
      state: "ready", loggedIn: true, pageKind: "detail",
      job: { encryptJobId: "another-job", contacted: false }, reason: "", observedAt: 1
    });
    const fetchSpy = vi.spyOn(globalThis, "fetch");

    const result = await executeDefaultGreeting(command);

    expect(result.statusCode).toBe("PRE_DISPATCH_PAGE_MISMATCH");
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("平台请求异常只调用一次且返回未知，不自动重试", async () => {
    readBossPageSnapshot.mockReturnValue({
      state: "ready", loggedIn: true, pageKind: "detail",
      job: { encryptJobId: "job-1", securityId: "security-1", contacted: false },
      reason: "", observedAt: 1
    });
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("network"));

    const result = await executeDefaultGreeting(command);

    expect(fetchSpy).toHaveBeenCalledTimes(1);
    expect(result.outcome).toBe("unknown");
    expect(result.statusCode).toBe("BOSS_REQUEST_NETWORK_UNKNOWN");
  });

  it("code=0立即回传平台已受理，不再等待旧页面按钮变化", async () => {
    readBossPageSnapshot.mockReturnValue({
      state: "ready", loggedIn: true, pageKind: "detail",
      job: { encryptJobId: "job-1", securityId: "security-1", contacted: false },
      reason: "", observedAt: 1
    });
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      status: 200,
      json: vi.fn().mockResolvedValue({ code: 0, message: "Success" })
    } as unknown as Response);

    const result = await executeDefaultGreeting(command);

    expect(fetchSpy).toHaveBeenCalledTimes(1);
    expect(readBossPageSnapshot).toHaveBeenCalledTimes(1);
    expect(result.outcome).toBe("accepted");
    expect(result.contacted).toBeNull();
    expect(result.statusCode).toBe("BOSS_REQUEST_ACCEPTED");
  });

  it("平台明确返回非零结果时标记明确失败而不是结果未知", async () => {
    readBossPageSnapshot.mockReturnValue({
      state: "ready", loggedIn: true, pageKind: "detail",
      job: { encryptJobId: "job-1", securityId: "security-1", contacted: false },
      reason: "", observedAt: 1
    });
    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      status: 200,
      json: vi.fn().mockResolvedValue({ code: 1, message: "操作过于频繁" })
    } as unknown as Response);

    const result = await executeDefaultGreeting(command);

    expect(result.outcome).toBe("failed");
    expect(result.statusCode).toBe("BOSS_RATE_LIMIT");
  });
});
