import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ChatObservedMessage, ChatSendExecutionResult } from "../src/finejob/types";
import { SharedActionGate } from "../src/finejob/shared-action-gate";


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
  frameOrigin: "remote_message",
  evidenceSource: direction === "outbound" ? "remote_outbound_echo" : "remote_message",
  serverMid: "",
  rawBody: {},
  rawMeta: {}
});

const resumeAction = () => ({
  id: "resume-action-1",
  action_type: "resume_send" as const,
  session_id: "session-1",
  status: "claimed",
  text: "",
  execution_epoch: 1,
  account_uid: "100",
  peer_uid: "200",
  encrypt_peer_uid: "enc-200",
  security_id: "security-200",
  encrypt_job_id: "job-200",
  client_mid: "1000000000000000001",
  operation_kind: "resume" as const,
  encrypt_resume_id: "resume-1",
  resume_filename: "候选人.pdf"
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

  it("简历 claim 后先取得 snapshot，Preflight 通过才 dispatch 到 sender", async () => {
    vi.useRealTimers();
    const { BossChatCoordinator } = await import("../src/finejob/chat-coordinator");
    const order: string[] = [];
    const action = resumeAction();
    let coordinator: InstanceType<typeof BossChatCoordinator>;
    browserMock.tabs.query.mockResolvedValue([{ id: 7 }] as never);
    browserMock.tabs.sendMessage.mockImplementation((async (_tabId: number, payload: { command: { type: string; requestId?: string; actionId?: string } }) => {
      if (payload.command.type === "BOSS_RESUME_SNAPSHOT_REQUEST") {
        order.push("snapshot request");
        queueMicrotask(() => {
          order.push("snapshot result");
          void coordinator.reportResumeSnapshot({
            requestId: payload.command.requestId ?? "",
            actionId: payload.command.actionId ?? "",
            attachments: [{ encryptResumeId: "resume-1", filename: "候选人.pdf" }],
            observedAt: "2026-09-08T00:00:00Z",
            error: ""
          });
        });
        return { accepted: true };
      }
      order.push("sender");
      return { accepted: true };
    }) as never);
    const client = {
      getChatRuntime: vi.fn(async () => ({ listen_enabled: true, generation_enabled: true, send_enabled: true })),
      reportChatHeartbeat: vi.fn(async () => undefined),
      completeChatSend: vi.fn(async () => undefined),
      listEligibleChatSendActions: vi.fn(async () => [action]),
      claimChatSendAction: vi.fn(async () => {
        order.push("claim");
        return action;
      }),
      preflightChatSend: vi.fn(async (_action: typeof action, snapshot: Record<string, unknown>) => {
        order.push("preflight");
        expect(snapshot.resume_list_snapshot).toEqual({
          attachments: [{ encryptResumeId: "resume-1", filename: "候选人.pdf" }]
        });
        return { decision: "dispatch", dispatch_token: "dispatch-token", action };
      }),
      markChatDispatchStarted: vi.fn(async () => { order.push("dispatch started"); })
    };
    coordinator = new BossChatCoordinator(client as never);
    await coordinator.start();
    await coordinator.reportTabHeartbeat({
      accountUid: "100", tabId: "leader-tab", loggedIn: true,
      pathname: "/web/geek/chat", observedAt: Date.now(), visible: true
    });

    await vi.waitFor(() => expect(client.markChatDispatchStarted).toHaveBeenCalledOnce());
    expect(order).toEqual(["claim", "snapshot request", "snapshot result", "preflight", "dispatch started", "sender"]);
  });

  it("snapshot timeout 进入 fail-closed Preflight，既不 dispatch 也不调用 sender", async () => {
    const { BossChatCoordinator } = await import("../src/finejob/chat-coordinator");
    const action = resumeAction();
    let snapshotRequested!: () => void;
    const snapshotRequest = new Promise<void>((resolve) => { snapshotRequested = resolve; });
    let preflightCompleted!: () => void;
    const preflightResult = new Promise<void>((resolve) => { preflightCompleted = resolve; });
    browserMock.tabs.query.mockResolvedValue([{ id: 7 }] as never);
    browserMock.tabs.sendMessage.mockImplementation((async (_tabId: number, payload: { command: { type: string } }) => {
      if (payload.command.type === "BOSS_RESUME_SNAPSHOT_REQUEST") snapshotRequested();
      return { accepted: true };
    }) as never);
    const client = {
      getChatRuntime: vi.fn(async () => ({ listen_enabled: true, generation_enabled: true, send_enabled: true })),
      reportChatHeartbeat: vi.fn(async () => undefined),
      completeChatSend: vi.fn(async () => undefined),
      listEligibleChatSendActions: vi.fn(async () => [action]),
      claimChatSendAction: vi.fn(async () => action),
      preflightChatSend: vi.fn(async (_action: typeof action, snapshot: Record<string, unknown>) => {
        expect(snapshot.resume_list_snapshot).toEqual({ error: "resume_snapshot_timeout" });
        preflightCompleted();
        return { decision: "blocked", action };
      }),
      markChatDispatchStarted: vi.fn(async () => undefined)
    };
    const coordinator = new BossChatCoordinator(client as never);
    await coordinator.start();
    await coordinator.reportTabHeartbeat({
      accountUid: "100", tabId: "leader-tab", loggedIn: true,
      pathname: "/web/geek/chat", observedAt: Date.now(), visible: true
    });
    await snapshotRequest;
    await vi.advanceTimersByTimeAsync(5_000);
    await preflightResult;

    expect(client.markChatDispatchStarted).not.toHaveBeenCalled();
    expect(browserMock.tabs.sendMessage).toHaveBeenCalledTimes(1);
  });

  it("Main World snapshot error 进入 fail-closed Preflight，既不 dispatch 也不调用 sender", async () => {
    vi.useRealTimers();
    const { BossChatCoordinator } = await import("../src/finejob/chat-coordinator");
    const action = resumeAction();
    let coordinator: InstanceType<typeof BossChatCoordinator>;
    let preflightCompleted!: () => void;
    const preflightResult = new Promise<void>((resolve) => { preflightCompleted = resolve; });
    browserMock.tabs.query.mockResolvedValue([{ id: 7 }] as never);
    browserMock.tabs.sendMessage.mockImplementation((async (_tabId: number, payload: { command: { type: string; requestId?: string; actionId?: string } }) => {
      if (payload.command.type === "BOSS_RESUME_SNAPSHOT_REQUEST") {
        queueMicrotask(() => void coordinator.reportResumeSnapshot({
          requestId: payload.command.requestId ?? "",
          actionId: payload.command.actionId ?? "",
          attachments: [],
          observedAt: "2026-09-08T00:00:00Z",
          error: "resume_snapshot_failed"
        }));
      }
      return { accepted: true };
    }) as never);
    const client = {
      getChatRuntime: vi.fn(async () => ({ listen_enabled: true, generation_enabled: true, send_enabled: true })),
      reportChatHeartbeat: vi.fn(async () => undefined),
      completeChatSend: vi.fn(async () => undefined),
      listEligibleChatSendActions: vi.fn(async () => [action]),
      claimChatSendAction: vi.fn(async () => action),
      preflightChatSend: vi.fn(async (_action: typeof action, snapshot: Record<string, unknown>) => {
        expect(snapshot.resume_list_snapshot).toEqual({ error: "resume_snapshot_failed" });
        preflightCompleted();
        return { decision: "blocked", action };
      }),
      markChatDispatchStarted: vi.fn(async () => undefined)
    };
    coordinator = new BossChatCoordinator(client as never);
    await coordinator.start();
    await coordinator.reportTabHeartbeat({
      accountUid: "100", tabId: "leader-tab", loggedIn: true,
      pathname: "/web/geek/chat", observedAt: Date.now(), visible: true
    });
    await preflightResult;

    expect(client.markChatDispatchStarted).not.toHaveBeenCalled();
    expect(browserMock.tabs.sendMessage).toHaveBeenCalledTimes(1);
  });

  it("claim 请求抛错后释放 shared gate，避免聊天循环永久 busy", async () => {
    vi.useRealTimers();
    const { BossChatCoordinator } = await import("../src/finejob/chat-coordinator");
    const gate = new SharedActionGate();
    const claimChatSendAction = vi.fn(async () => { throw new Error("claim_failed"); });
    const coordinator = new BossChatCoordinator({
      getChatRuntime: vi.fn(async () => ({ listen_enabled: true, generation_enabled: true, send_enabled: true })),
      reportChatHeartbeat: vi.fn(async () => undefined),
      completeChatSend: vi.fn(async () => undefined),
      listEligibleChatSendActions: vi.fn(async () => [resumeAction()]),
      claimChatSendAction
    } as never, gate);
    await coordinator.start();
    await coordinator.reportTabHeartbeat({
      accountUid: "100", tabId: "leader-tab", loggedIn: true,
      pathname: "/web/geek/chat", observedAt: Date.now(), visible: true
    });

    await vi.waitFor(() => expect(claimChatSendAction).toHaveBeenCalledOnce());
    expect(gate.isActive()).toBe(false);
  });

  it("snapshot 请求抛错且 Preflight 阻断后释放 shared gate", async () => {
    vi.useRealTimers();
    const { BossChatCoordinator } = await import("../src/finejob/chat-coordinator");
    const gate = new SharedActionGate();
    browserMock.tabs.query.mockResolvedValue([{ id: 7 }] as never);
    browserMock.tabs.sendMessage.mockResolvedValue({ accepted: false } as never);
    const preflightChatSend = vi.fn(async () => ({ decision: "blocked", action: resumeAction() }));
    const coordinator = new BossChatCoordinator({
      getChatRuntime: vi.fn(async () => ({ listen_enabled: true, generation_enabled: true, send_enabled: true })),
      reportChatHeartbeat: vi.fn(async () => undefined),
      completeChatSend: vi.fn(async () => undefined),
      listEligibleChatSendActions: vi.fn(async () => [resumeAction()]),
      claimChatSendAction: vi.fn(async () => resumeAction()),
      preflightChatSend
    } as never, gate);
    await coordinator.start();
    await coordinator.reportTabHeartbeat({
      accountUid: "100", tabId: "leader-tab", loggedIn: true,
      pathname: "/web/geek/chat", observedAt: Date.now(), visible: true
    });

    await vi.waitFor(() => expect(preflightChatSend).toHaveBeenCalledOnce());
    expect(gate.isActive()).toBe(false);
  });

  it("Preflight 请求抛错后释放 shared gate", async () => {
    vi.useRealTimers();
    const { BossChatCoordinator } = await import("../src/finejob/chat-coordinator");
    const gate = new SharedActionGate();
    const action = { ...resumeAction(), action_type: "chat_message" as const, operation_kind: "message" as const };
    const preflightChatSend = vi.fn(async () => { throw new Error("preflight_failed"); });
    const coordinator = new BossChatCoordinator({
      getChatRuntime: vi.fn(async () => ({ listen_enabled: true, generation_enabled: true, send_enabled: true })),
      reportChatHeartbeat: vi.fn(async () => undefined),
      completeChatSend: vi.fn(async () => undefined),
      listEligibleChatSendActions: vi.fn(async () => [action]),
      claimChatSendAction: vi.fn(async () => action),
      preflightChatSend
    } as never, gate);
    await coordinator.start();
    await coordinator.reportTabHeartbeat({
      accountUid: "100", tabId: "leader-tab", loggedIn: true,
      pathname: "/web/geek/chat", observedAt: Date.now(), visible: true
    });

    await vi.waitFor(() => expect(preflightChatSend).toHaveBeenCalledOnce());
    expect(gate.isActive()).toBe(false);
  });

  it("dispatch_started 请求抛错后以 unknown 收口并进入 shared cooldown", async () => {
    vi.useRealTimers();
    const { BossChatCoordinator } = await import("../src/finejob/chat-coordinator");
    const gate = new SharedActionGate();
    const action = { ...resumeAction(), action_type: "chat_message" as const, operation_kind: "message" as const };
    const completeChatSend = vi.fn(async () => undefined);
    const coordinator = new BossChatCoordinator({
      getChatRuntime: vi.fn(async () => ({ listen_enabled: true, generation_enabled: true, send_enabled: true })),
      reportChatHeartbeat: vi.fn(async () => undefined),
      completeChatSend,
      enterTaskCooldown: vi.fn(() => gate.enterCooldown()),
      listEligibleChatSendActions: vi.fn(async () => [action]),
      claimChatSendAction: vi.fn(async () => action),
      preflightChatSend: vi.fn(async () => ({ decision: "dispatch", dispatch_token: "token", action })),
      markChatDispatchStarted: vi.fn(async () => { throw new Error("dispatch_started_failed"); })
    } as never, gate);
    await coordinator.start();
    await coordinator.reportTabHeartbeat({
      accountUid: "100", tabId: "leader-tab", loggedIn: true,
      pathname: "/web/geek/chat", observedAt: Date.now(), visible: true
    });

    await vi.waitFor(() => expect(completeChatSend).toHaveBeenCalledOnce());
    expect(gate.isCooldownActive()).toBe(true);
  });

  it("greeting 占用共享 gate 时聊天心跳不 claim", async () => {
    vi.useRealTimers();
    const { BossChatCoordinator } = await import("../src/finejob/chat-coordinator");
    const gate = new SharedActionGate();
    expect(gate.tryEnterBusy()).toBe(true);
    const client = {
      getChatRuntime: vi.fn(async () => ({ listen_enabled: true, generation_enabled: true, send_enabled: true })),
      reportChatHeartbeat: vi.fn(async () => undefined),
      completeChatSend: vi.fn(async () => undefined),
      listEligibleChatSendActions: vi.fn(async () => [resumeAction()]),
      claimChatSendAction: vi.fn(async () => resumeAction())
    };
    const coordinator = new BossChatCoordinator(client as never, gate);
    await coordinator.start();
    await coordinator.reportTabHeartbeat({
      accountUid: "100", tabId: "leader-tab", loggedIn: true,
      pathname: "/web/geek/chat", observedAt: Date.now(), visible: true
    });

    await vi.waitFor(() => expect(client.getChatRuntime).toHaveBeenCalled());
    expect(client.claimChatSendAction).not.toHaveBeenCalled();
  });

  it("result outbox 补传成功后进入共享 cooldown，同轮不 claim 下一动作", async () => {
    vi.useRealTimers();
    const { BossChatCoordinator } = await import("../src/finejob/chat-coordinator");
    const gate = new SharedActionGate();
    expect(gate.tryEnterBusy()).toBe(true);
    const completeChatSend = vi.fn()
      .mockRejectedValueOnce(new Error("后端暂不可用"))
      .mockResolvedValueOnce(undefined);
    const client = {
      getChatRuntime: vi.fn(async () => ({ listen_enabled: true, generation_enabled: true, send_enabled: true })),
      reportChatHeartbeat: vi.fn(async () => undefined),
      completeChatSend,
      enterTaskCooldown: vi.fn(() => gate.enterCooldown()),
      isTaskCooldownActive: vi.fn(() => gate.isCooldownActive()),
      listEligibleChatSendActions: vi.fn(async () => [resumeAction()]),
      claimChatSendAction: vi.fn(async () => resumeAction())
    };
    const coordinator = new BossChatCoordinator(client as never, gate);
    await coordinator.start();
    await coordinator.reportSendResult({
      actionId: "resume-action-1", executionEpoch: 1, outcome: "accepted",
      platformMessageId: "", clientMid: "mid-1", statusCode: "transport_accepted", message: "已提交", evidence: {}
    });
    expect(gate.isActive()).toBe(true);
    await coordinator.reportTabHeartbeat({
      accountUid: "100", tabId: "leader-tab", loggedIn: true,
      pathname: "/web/geek/chat", observedAt: Date.now(), visible: true
    });

    await vi.waitFor(() => expect(completeChatSend).toHaveBeenCalledTimes(2));
    expect(gate.isCooldownActive()).toBe(true);
    expect(client.claimChatSendAction).not.toHaveBeenCalled();
  });
});
