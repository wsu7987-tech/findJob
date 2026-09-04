import { describe, expect, it, vi } from "vitest";

import { createFrameworkStatus } from "../src/executor/framework-mode";
import type { BackgroundService } from "../src/message/background";
import { ContentService } from "../src/message/content";

const createBackground = (ok = true): BackgroundService =>
  ({
    health: vi.fn().mockResolvedValue({
      ok,
      component: "background",
      frameworkMode: false,
      realActionsEnabled: true
    }),
    reportExecutionResult: vi.fn().mockResolvedValue({ accepted: true }),
    reportChatTabHeartbeat: vi.fn().mockResolvedValue({ isLeader: true, leaderEpoch: 1 })
  }) as unknown as BackgroundService;

describe("Content 服务", () => {
  it("串联 Background 与 MAIN World 的执行器健康状态", async () => {
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

  it("自动打招呼与自动代聊命令保持独立并按原顺序交给 Main World", async () => {
    const status = createFrameworkStatus("/web/geek/chat");
    const background = createBackground();
    const service = new ContentService(background, status);
    await service.reportChatIdentity({
      accountUid: "account-1",
      loggedIn: true,
      pathname: "/web/geek/chat",
      observedAt: 1
    });
    const heartbeat = vi.mocked(background.reportChatTabHeartbeat).mock.calls[0]?.[0];
    expect(heartbeat?.tabId).toBeTruthy();

    const greeting = {
      type: "BOSS_DEFAULT_GREETING" as const,
      taskId: "greeting-1",
      executionEpoch: 1,
      encryptJobId: "job-1"
    };
    const chat = {
      type: "BOSS_CHAT_SEND" as const,
      targetTabId: heartbeat?.tabId ?? "",
      leaderEpoch: 1,
      action: {
        id: "chat-1",
        session_id: "session-1",
        status: "running",
        text: "您好",
        execution_epoch: 1,
        account_uid: "account-1",
        peer_uid: "peer-1",
        encrypt_peer_uid: "encrypt-peer-1",
        security_id: "security-1",
        encrypt_job_id: "job-1"
      }
    };

    await expect(service.enqueueMainCommand(greeting)).resolves.toEqual({ accepted: true });
    await expect(service.enqueueMainCommand(chat)).resolves.toEqual({ accepted: true });
    await expect(service.takeMainCommand()).resolves.toEqual(greeting);
    await expect(service.takeMainCommand()).resolves.toEqual(chat);
  });

  it("领导页任期变化后丢弃旧任期的代聊发送命令", async () => {
    const status = createFrameworkStatus("/web/geek/chat");
    const background = createBackground();
    vi.mocked(background.reportChatTabHeartbeat)
      .mockResolvedValueOnce({ isLeader: true, leaderEpoch: 1 })
      .mockResolvedValueOnce({ isLeader: true, leaderEpoch: 2 });
    const service = new ContentService(background, status);
    const identity = {
      accountUid: "account-1",
      loggedIn: true,
      pathname: "/web/geek/chat",
      observedAt: 1
    };
    await service.reportChatIdentity(identity);
    const tabId = vi.mocked(background.reportChatTabHeartbeat).mock.calls[0]?.[0].tabId ?? "";
    await service.enqueueMainCommand({
      type: "BOSS_CHAT_SEND",
      targetTabId: tabId,
      leaderEpoch: 1,
      action: {
        id: "chat-old-leader",
        session_id: "session-1",
        status: "running",
        text: "您好",
        execution_epoch: 1,
        account_uid: "account-1",
        peer_uid: "peer-1",
        encrypt_peer_uid: "encrypt-peer-1",
        security_id: "security-1",
        encrypt_job_id: "job-1"
      }
    });
    await service.reportChatIdentity({ ...identity, observedAt: 2 });

    await expect(service.takeMainCommand()).resolves.toBeNull();
  });
});
