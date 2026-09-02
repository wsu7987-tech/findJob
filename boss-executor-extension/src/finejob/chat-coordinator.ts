import { browser } from "#imports";

import { fineJobExecutorClient, type FineJobExecutorClient } from "./client";
import type {
  BossChatCoordinatorStatus,
  ChatObservedMessage,
  ChatSendCommand,
  ChatSendExecutionResult,
  ChatTabHeartbeat,
  FineJobChatSendAction
} from "./types";


const LEADER_STATE_KEY = "finejobBossChatLeaderV1";
const EVENT_OUTBOX_KEY = "finejobBossChatEventOutboxV1";
const RESULT_OUTBOX_KEY = "finejobBossChatResultOutboxV1";
const RUNTIME_CACHE_KEY = "finejobBossChatRuntimeCacheV1";
const TAB_STALE_MS = 15_000;
const LEASE_MS = 20_000;
const EVENT_OUTBOX_MAX_ITEMS = 200;
const EVENT_OUTBOX_MAX_BYTES = 2 * 1024 * 1024;

type Candidate = ChatTabHeartbeat & { receivedAt: number };
type LeaderLease = { tabId: string; epoch: number; expiresAt: number };
type ActiveAction = { action: FineJobChatSendAction; deadlineAt: number };
type RuntimeCache = {
  listenEnabled: boolean;
  generationEnabled: boolean;
  sendEnabled: boolean;
  updatedAt: string;
};

export class BossChatCoordinator {
  private readonly candidates = new Map<string, Candidate>();
  private readonly leaders = new Map<string, LeaderLease>();
  private readonly activeActions = new Map<string, ActiveAction>();
  private eventOutbox: Record<string, ChatObservedMessage> = {};
  private resultOutbox: Record<string, ChatSendExecutionResult> = {};
  private processing = false;
  private listenEnabled = false;
  private runtimeKnown = false;
  private runtimeCache: RuntimeCache | null = null;
  private eventOutboxBlocked = false;
  private lastSuccessfulFlushAt = "";
  private lastError = "";

  constructor(private readonly client: FineJobExecutorClient = fineJobExecutorClient) {}

  async start(): Promise<void> {
    const local = await browser.storage.local.get([
      EVENT_OUTBOX_KEY,
      RESULT_OUTBOX_KEY,
      RUNTIME_CACHE_KEY
    ]);
    this.eventOutbox = (local[EVENT_OUTBOX_KEY] as Record<string, ChatObservedMessage> | undefined) ?? {};
    this.resultOutbox = (local[RESULT_OUTBOX_KEY] as Record<string, ChatSendExecutionResult> | undefined) ?? {};
    const runtime = local[RUNTIME_CACHE_KEY] as RuntimeCache | undefined;
    this.runtimeCache = runtime ?? null;
    this.listenEnabled = runtime?.listenEnabled ?? false;
    this.runtimeKnown = runtime !== undefined;
    this.eventOutboxBlocked = this.isOutboxAtCapacity();
    const session = await browser.storage.session.get(LEADER_STATE_KEY);
    const stored = session[LEADER_STATE_KEY] as Record<string, LeaderLease> | undefined;
    for (const [accountUid, lease] of Object.entries(stored ?? {})) {
      this.leaders.set(accountUid, lease);
    }
    // 只在协调器启动时读取一次运行配置，后续由页面事件和发送结果触发处理。
    try {
      const currentRuntime = await this.client.getChatRuntime();
      this.listenEnabled = currentRuntime.listen_enabled;
      this.runtimeKnown = true;
      await this.updateRuntimeCache(
        currentRuntime.listen_enabled,
        currentRuntime.generation_enabled,
        currentRuntime.send_enabled
      );
    } catch (error) {
      this.lastError = (error as Error).message || "自动代聊运行配置读取失败";
    }
    await this.processAccounts();
  }

  async reportTabHeartbeat(heartbeat: ChatTabHeartbeat): Promise<{ isLeader: boolean; leaderEpoch: number }> {
    const key = `${heartbeat.accountUid}:${heartbeat.tabId}`;
    this.candidates.set(key, { ...heartbeat, receivedAt: Date.now() });
    const leader = await this.elect(heartbeat.accountUid);
    void this.processAccounts();
    return {
      isLeader: leader?.tabId === heartbeat.tabId,
      leaderEpoch: leader?.epoch ?? 0
    };
  }

  async reportMessage(tabId: string, message: ChatObservedMessage): Promise<{ accepted: boolean }> {
    // 监听开关使用最近一次成功同步的本地值，避免后端离线时消息在落盘前丢失。
    if (!this.listenEnabled) return { accepted: false };
    const leader = await this.elect(message.accountUid);
    // 入站消息只接受领导者标签页；人工发出的消息允许任意标签页上报并触发人工接管。
    if (message.direction === "inbound" && leader?.tabId !== tabId) {
      return { accepted: false };
    }
    if (this.eventOutbox[message.eventId]) return { accepted: true };
    if (!this.canAppendToOutbox(message)) {
      this.eventOutboxBlocked = true;
      this.lastError = "自动代聊消息待上传区已满，请恢复 FineJob 后端连接";
      return { accepted: false };
    }
    this.eventOutbox[message.eventId] = message;
    // 收到消息后先持久化，后端上传失败时由后续页面事件继续处理。
    await this.persistOutbox();
    void this.processAccounts();
    return { accepted: true };
  }

  async reportSendResult(result: ChatSendExecutionResult): Promise<void> {
    await this.persistSendResult(result);
    for (const [accountUid, active] of this.activeActions) {
      if (active.action.id === result.actionId) this.activeActions.delete(accountUid);
    }
    try {
      await this.flushResultOutbox();
      void this.processAccounts();
    } catch (error) {
      this.lastError = `自动代聊发送结果等待回传：${(error as Error).message}`;
    }
  }

  private async processAccounts(): Promise<void> {
    if (this.processing) return;
    this.processing = true;
    try {
      this.pruneCandidates();
      await this.flushResultOutbox();
      const accounts = new Set<string>();
      for (const candidate of this.candidates.values()) accounts.add(candidate.accountUid);
      for (const accountUid of accounts) {
        const leader = await this.elect(accountUid);
        if (!leader) continue;
        await this.client.reportChatHeartbeat(
          this.candidates.get(`${accountUid}:${leader.tabId}`) as Candidate,
          leader.epoch
        );
        if (this.listenEnabled) await this.flushAccountOutbox(accountUid, leader.epoch);
        if (this.runtimeCache?.sendEnabled) await this.claimAndDispatch(accountUid, leader);
      }
      await this.expireUnreportedActions();
      this.lastError = "";
    } catch (error) {
      // 后端或页面暂时不可用时保留 outbox，等待下一次业务事件重新处理。
      this.lastError = (error as Error).message || "自动代聊协调器暂时不可用";
    } finally {
      this.processing = false;
    }
  }

  isListeningEnabled(): boolean {
    return this.listenEnabled;
  }

  getStatus(): BossChatCoordinatorStatus {
    return {
      listenEnabled: this.listenEnabled,
      runtimeKnown: this.runtimeKnown,
      eventOutboxCount: Object.keys(this.eventOutbox).length,
      eventOutboxBytes: this.outboxBytes(),
      eventOutboxBlocked: this.eventOutboxBlocked,
      resultOutboxCount: Object.keys(this.resultOutbox).length,
      lastSuccessfulFlushAt: this.lastSuccessfulFlushAt,
      lastError: this.lastError
    };
  }

  private pruneCandidates(): void {
    const cutoff = Date.now() - TAB_STALE_MS;
    for (const [key, candidate] of this.candidates) {
      if (candidate.receivedAt < cutoff) this.candidates.delete(key);
    }
  }

  private async elect(accountUid: string): Promise<LeaderLease | null> {
    const candidates = [...this.candidates.values()]
      .filter((item) =>
        item.accountUid === accountUid
        && item.pathname.includes("/web/geek/chat")
        && item.receivedAt >= Date.now() - TAB_STALE_MS
      )
      .sort((left, right) => Number(right.visible) - Number(left.visible) || left.tabId.localeCompare(right.tabId));
    if (candidates.length === 0) return null;
    const current = this.leaders.get(accountUid);
    const currentAlive = current && candidates.some((item) => item.tabId === current.tabId);
    if (currentAlive && current.expiresAt > Date.now()) {
      if (current.expiresAt - Date.now() < LEASE_MS / 2) {
        current.expiresAt = Date.now() + LEASE_MS;
        await this.persistLeaders();
      }
      return current;
    }
    const next: LeaderLease = {
      tabId: candidates[0]?.tabId ?? "",
      epoch: (current?.epoch ?? 0) + 1,
      expiresAt: Date.now() + LEASE_MS
    };
    this.leaders.set(accountUid, next);
    await this.persistLeaders();
    return next;
  }

  private async persistLeaders(): Promise<void> {
    await browser.storage.session.set({
      [LEADER_STATE_KEY]: Object.fromEntries(this.leaders)
    });
  }

  private async persistOutbox(): Promise<void> {
    await browser.storage.local.set({ [EVENT_OUTBOX_KEY]: this.eventOutbox });
  }

  private resultKey(result: ChatSendExecutionResult): string {
    return `${result.actionId}:${result.executionEpoch}`;
  }

  private async persistSendResult(result: ChatSendExecutionResult): Promise<void> {
    const key = this.resultKey(result);
    const existing = this.resultOutbox[key];
    // 已取得明确平台结果时，不允许后续超时状态覆盖它。
    if (existing && existing.outcome !== "unknown" && result.outcome === "unknown") return;
    this.resultOutbox[key] = result;
    await browser.storage.local.set({ [RESULT_OUTBOX_KEY]: this.resultOutbox });
  }

  private async flushResultOutbox(): Promise<void> {
    for (const [key, result] of Object.entries(this.resultOutbox)) {
      await this.client.completeChatSend(result);
      delete this.resultOutbox[key];
      await browser.storage.local.set({ [RESULT_OUTBOX_KEY]: this.resultOutbox });
    }
  }

  private async updateRuntimeCache(
    listenEnabled: boolean,
    generationEnabled: boolean,
    sendEnabled: boolean
  ): Promise<void> {
    if (
      this.runtimeCache?.listenEnabled === listenEnabled
      && this.runtimeCache.generationEnabled === generationEnabled
      && this.runtimeCache.sendEnabled === sendEnabled
    ) return;
    this.runtimeCache = {
      listenEnabled,
      generationEnabled,
      sendEnabled,
      updatedAt: new Date().toISOString()
    };
    await browser.storage.local.set({ [RUNTIME_CACHE_KEY]: this.runtimeCache });
  }

  private messageBytes(message: ChatObservedMessage): number {
    return new TextEncoder().encode(JSON.stringify(message)).byteLength;
  }

  private outboxBytes(): number {
    return Object.values(this.eventOutbox).reduce(
      (total, message) => total + this.messageBytes(message),
      0
    );
  }

  private isOutboxAtCapacity(): boolean {
    return Object.keys(this.eventOutbox).length >= EVENT_OUTBOX_MAX_ITEMS
      || this.outboxBytes() >= EVENT_OUTBOX_MAX_BYTES;
  }

  private canAppendToOutbox(message: ChatObservedMessage): boolean {
    return Object.keys(this.eventOutbox).length < EVENT_OUTBOX_MAX_ITEMS
      && this.outboxBytes() + this.messageBytes(message) <= EVENT_OUTBOX_MAX_BYTES;
  }

  private async flushAccountOutbox(accountUid: string, leaderEpoch: number): Promise<void> {
    const messages = Object.values(this.eventOutbox)
      .filter((item) => item.accountUid === accountUid)
      .slice(0, 50);
    if (messages.length === 0) return;
    await this.client.reportChatMessages(messages, leaderEpoch);
    for (const message of messages) delete this.eventOutbox[message.eventId];
    await this.persistOutbox();
    this.eventOutboxBlocked = false;
    this.lastSuccessfulFlushAt = new Date().toISOString();
  }

  private async claimAndDispatch(accountUid: string, leader: LeaderLease): Promise<void> {
    if (this.activeActions.has(accountUid)) return;
    const action = await this.client.claimChatSendAction(accountUid, leader.tabId, leader.epoch);
    if (!action) return;
    await this.client.markChatDispatchStarted(action);
    this.activeActions.set(accountUid, { action, deadlineAt: Date.now() + 30_000 });
    const command: ChatSendCommand = {
      type: "BOSS_CHAT_SEND",
      targetTabId: leader.tabId,
      leaderEpoch: leader.epoch,
      action
    };
    const tabs = await browser.tabs.query({ url: ["*://zhipin.com/*", "*://*.zhipin.com/*"] });
    const results = await Promise.allSettled(
      tabs.flatMap((tab) => tab.id === undefined ? [] : [browser.tabs.sendMessage(tab.id, {
        type: "finejob:boss-chat:execute:v1",
        command
      })])
    );
    const accepted = results.some(
      (result) => result.status === "fulfilled"
        && Boolean((result.value as { accepted?: boolean } | undefined)?.accepted)
    );
    if (!accepted) {
      await this.reportSendResult({
        actionId: action.id,
        executionEpoch: action.execution_epoch,
        outcome: "unknown",
        platformMessageId: "",
        clientMid: "",
        statusCode: "leader_tab_unavailable",
        message: "未找到可执行发送的领导者标签页",
        evidence: {}
      });
    }
  }

  private async expireUnreportedActions(): Promise<void> {
    for (const active of [...this.activeActions.values()]) {
      if (active.deadlineAt > Date.now()) continue;
      await this.reportSendResult({
        actionId: active.action.id,
        executionEpoch: active.action.execution_epoch,
        outcome: "unknown",
        platformMessageId: "",
        clientMid: "",
        statusCode: "main_world_result_timeout",
        message: "页面发送结果回写超时，未自动重试",
        evidence: {}
      });
    }
  }
}

export const bossChatCoordinator = new BossChatCoordinator();
