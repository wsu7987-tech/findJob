import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ChatObservedMessage, ChatSendExecutionResult } from "../src/finejob/types";


const storage = {
  local: {} as Record<string, unknown>,
  session: {} as Record<string, unknown>
};

const area = (name: "local" | "session") => ({
  get: vi.fn(async (keys: string | string[]) => {
    const list = Array.isArray(keys) ? keys : [keys];
    return Object.fromEntries(list.map((key) => [key, storage[name][key]]));
  }),
  set: vi.fn(async (values: Record<string, unknown>) => {
    Object.assign(storage[name], values);
  })
});

const browserMock = {
  storage: { local: area("local"), session: area("session") },
  tabs: { query: vi.fn(async () => []), sendMessage: vi.fn(async () => undefined) }
};

(globalThis as unknown as Record<string, unknown>).__fineJobTestBrowser = browserMock;

const message = (
  direction: "inbound" | "outbound",
  suffix: string = direction
): ChatObservedMessage => ({
  eventId: `event-${suffix}`,
  accountUid: "100",
  platformMessageId: `message-${suffix}`,
  direction,
  messageType: "text",
  content: "测试消息",
  senderUid: direction === "inbound" ? "200" : "100",
  receiverUid: direction === "inbound" ? "100" : "200",
  clientMid: "1",
  peerUid: "200",
  encryptPeerUid: "enc",
  securityId: "security",
  encryptJobId: "job",
  jobTitle: "开发工程师",
  peerName: "王经理",
  companyName: "示例科技",
  sentAt: new Date().toISOString(),
  observedAt: new Date().toISOString(),
  source: direction === "outbound" ? "manual" : "websocket",
  rawMeta: {}
});

describe("多标签页聊天领导者", () => {
  beforeEach(() => {
    storage.local = {};
    storage.session = {};
    vi.clearAllMocks();
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.clearAllTimers();
    vi.useRealTimers();
  });

  it("同一账号只有领导者提交入站消息，人工出站允许任意标签页上报", async () => {
    const { BossChatCoordinator } = await import("../src/finejob/chat-coordinator");
    const client = {
      getChatRuntime: vi.fn(async () => ({ listen_enabled: true, generation_enabled: false, send_enabled: false })),
      reportChatHeartbeat: vi.fn(async () => undefined),
      reportChatMessages: vi.fn(async () => { throw new Error("测试保留 outbox"); }),
      completeChatSend: vi.fn()
    };
    const coordinator = new BossChatCoordinator(client as never);
    await coordinator.start();
    const base = { accountUid: "100", loggedIn: true, pathname: "/web/geek/chat", observedAt: Date.now(), visible: true };

    const first = await coordinator.reportTabHeartbeat({ ...base, tabId: "tab-a" });
    const second = await coordinator.reportTabHeartbeat({ ...base, tabId: "tab-b" });
    expect(first.isLeader).toBe(true);
    expect(second.isLeader).toBe(false);
    expect((await coordinator.reportMessage("tab-b", message("inbound"))).accepted).toBe(false);
    expect((await coordinator.reportMessage("tab-b", message("outbound"))).accepted).toBe(true);

    const outbox = storage.local.finejobBossChatEventOutboxV1 as Record<string, ChatObservedMessage>;
    expect(Object.keys(outbox)).toEqual(["event-outbound"]);
  });

  it("领导者心跳失效后递增 epoch 并切换标签页", async () => {
    const { BossChatCoordinator } = await import("../src/finejob/chat-coordinator");
    const client = {
      getChatRuntime: vi.fn(async () => ({ listen_enabled: false, generation_enabled: false, send_enabled: false })),
      completeChatSend: vi.fn()
    };
    const coordinator = new BossChatCoordinator(client as never);
    const base = { accountUid: "100", loggedIn: true, pathname: "/web/geek/chat", observedAt: Date.now(), visible: true };
    const first = await coordinator.reportTabHeartbeat({ ...base, tabId: "tab-a" });

    vi.advanceTimersByTime(16_000);
    const replacement = await coordinator.reportTabHeartbeat({
      ...base,
      tabId: "tab-b",
      observedAt: Date.now()
    });
    expect(first.leaderEpoch).toBe(1);
    expect(replacement).toEqual({ isLeader: true, leaderEpoch: 2 });
  });

  it("后端不可用时仍按本地监听缓存先保存消息", async () => {
    storage.local.finejobBossChatRuntimeCacheV1 = {
      listenEnabled: true,
      generationEnabled: true,
      sendEnabled: false,
      updatedAt: new Date().toISOString()
    };
    const { BossChatCoordinator } = await import("../src/finejob/chat-coordinator");
    const client = {
      getChatRuntime: vi.fn(async () => { throw new Error("后端离线"); }),
      completeChatSend: vi.fn()
    };
    const coordinator = new BossChatCoordinator(client as never);
    await coordinator.start();
    await coordinator.reportTabHeartbeat({
      accountUid: "100",
      tabId: "tab-a",
      loggedIn: true,
      pathname: "/web/geek/chat",
      observedAt: Date.now(),
      visible: true
    });

    await expect(coordinator.reportMessage("tab-a", message("inbound"))).resolves.toEqual({ accepted: true });
    const outbox = storage.local.finejobBossChatEventOutboxV1 as Record<string, ChatObservedMessage>;
    expect(outbox["event-inbound"]).toBeTruthy();
    expect(client.getChatRuntime).toHaveBeenCalled();
  });

  it("待上传区达到容量后保留旧消息并明确拒绝新消息", async () => {
    storage.local.finejobBossChatEventOutboxV1 = Object.fromEntries(
      Array.from({ length: 200 }, (_, index) => {
        const item = message("inbound", String(index));
        return [item.eventId, item];
      })
    );
    const { BossChatCoordinator } = await import("../src/finejob/chat-coordinator");
    const client = {
      getChatRuntime: vi.fn(async () => ({ listen_enabled: true, generation_enabled: true, send_enabled: false })),
      completeChatSend: vi.fn()
    };
    const coordinator = new BossChatCoordinator(client as never);
    await coordinator.start();
    await coordinator.reportTabHeartbeat({
      accountUid: "100",
      tabId: "tab-a",
      loggedIn: true,
      pathname: "/web/geek/chat",
      observedAt: Date.now(),
      visible: true
    });

    await expect(coordinator.reportMessage("tab-a", message("inbound", "overflow"))).resolves.toEqual({ accepted: false });
    const outbox = storage.local.finejobBossChatEventOutboxV1 as Record<string, ChatObservedMessage>;
    expect(Object.keys(outbox)).toHaveLength(200);
    expect(outbox["event-0"]).toBeTruthy();
    expect(outbox["event-overflow"]).toBeUndefined();
    expect(coordinator.getStatus()).toMatchObject({
      eventOutboxCount: 200,
      eventOutboxBlocked: true
    });
  });

  it("发送结果先持久化，后端恢复后只补传结果", async () => {
    const { BossChatCoordinator } = await import("../src/finejob/chat-coordinator");
    const completeChatSend = vi.fn().mockRejectedValueOnce(new Error("后端离线"));
    const client = {
      getChatRuntime: vi.fn(async () => ({ listen_enabled: false, generation_enabled: false, send_enabled: false })),
      completeChatSend
    };
    const coordinator = new BossChatCoordinator(client as never);
    await coordinator.start();
    const result: ChatSendExecutionResult = {
      actionId: "action-1",
      executionEpoch: 2,
      outcome: "accepted",
      platformMessageId: "",
      clientMid: "mid-1",
      statusCode: "mqtt_puback",
      message: "已提交",
      evidence: {}
    };

    await coordinator.reportSendResult(result);
    expect(storage.local.finejobBossChatResultOutboxV1).toEqual({ "action-1:2": result });
    expect(coordinator.getStatus().resultOutboxCount).toBe(1);

    completeChatSend.mockResolvedValueOnce(undefined);
    await coordinator.reportSendResult(result);
    expect(storage.local.finejobBossChatResultOutboxV1).toEqual({});
    expect(completeChatSend).toHaveBeenCalledTimes(2);
  });
});
