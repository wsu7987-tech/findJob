import { beforeEach, describe, expect, it, vi } from "vitest";

import { resolveBossContactContext } from "../src/platform/boss/chat/contact-context";

describe("联系人上下文加密 UID 回退", () => {
  beforeEach(() => vi.unstubAllGlobals());

  it.each([
    ["encryptFriendId", "friend-encrypted"],
    ["encryptUid", "uid-encrypted"],
    ["encryptBossId", "boss-encrypted"]
  ])("读取 %s", async (field, expected) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      json: async () => ({ zpData: { result: [{ [field]: expected, securityId: "security", encryptJobId: "job" }] } })
    }));
    const context = await resolveBossContactContext("account", `peer-${field}`, "job");
    expect(context).toMatchObject({ encryptPeerUid: expected, securityId: "security" });
  });

  it("接口失败时返回空上下文，发送前置校验将阻止发布", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
    await expect(resolveBossContactContext("account", "peer-failure", "job")).resolves.toMatchObject({
      encryptPeerUid: "", securityId: "", encryptJobId: ""
    });
  });
});
