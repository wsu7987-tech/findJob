import { browser } from "#imports";

import { fineJobExecutorClient, type FineJobExecutorClient } from "./client";
import type {
  ChatObservedMessage,
  ChatSendCommand,
  ChatSendExecutionResult,
  ChatTabHeartbeat,
  FineJobChatSendAction
} from "./types";


const LEADER_STATE_KEY = "finejobBossChatLeaderV1";
const EVENT_OUTBOX_KEY = "finejobBossChatEventOutboxV1";
const TAB_STALE_MS = 15_000;
const LEASE_MS = 20_000;

type Candidate = ChatTabHeartbeat & { receivedAt: number };
type LeaderLease = { tabId: string; epoch: number; expiresAt: number };
type ActiveAction = { action: FineJobChatSendAction; deadlineAt: number };

export class BossChatCoordinator {
  private readonly candidates = new Map<string, Candidate>();
  private readonly leaders = new Map<string, LeaderLease>();
  private readonly activeActions = new Map<string, ActiveAction>();
  private eventOutbox: Record<string, ChatObservedMessage> = {};
  private timer: number | null = null;
  private tickRunning = false;
  private listenEnabled = false;

  constructor(private readonly client: FineJobExecutorClient = fineJobExecutorClient) {}

  async start(): Promise<void> {
    const local = await browser.storage.local.get(EVENT_OUTBOX_KEY);
    this.eventOutbox = (local[EVENT_OUTBOX_KEY] as Record<string, ChatObservedMessage> | undefined) ?? {};
    const session = await browser.storage.session.get(LEADER_STATE_KEY);
    const stored = session[LEADER_STATE_KEY] as Record<string, LeaderLease> | undefined;
    for (const [accountUid, lease] of Object.entries(stored ?? {})) {
      this.leaders.set(accountUid, lease);
    }
    if (this.timer === null) {
      this.timer = globalThis.setInterval(() => void this.tick(), 2_000) as unknown as number;
    }
    await this.tick();
  }

  async reportTabHeartbeat(heartbeat: ChatTabHeartbeat): Promise<{ isLeader: boolean; leaderEpoch: number }> {
    const key = `${heartbeat.accountUid}:${heartbeat.tabId}`;
    this.candidates.set(key, { ...heartbeat, receivedAt: Date.now() });
    const leader = await this.elect(heartbeat.accountUid);
    return {
      isLeader: leader?.tabId === heartbeat.tabId,
      leaderEpoch: leader?.epoch ?? 0
    };
  }

  async reportMessage(tabId: string, message: ChatObservedMessage): Promise<{ accepted: boolean }> {
    const runtime = await this.client.getChatRuntime();
    this.listenEnabled = runtime.listen_enabled;
    if (!this.listenEnabled) return { accepted: false };
    const leader = await this.elect(message.accountUid);
    // 入站消息只接受领导者标签页；人工发出的消息允许任意标签页上报并触发人工接管。
    if (message.direction === "inbound" && leader?.tabId !== tabId) {
      return { accepted: false };
    }
    this.eventOutbox[message.eventId] = message;
    const entries = Object.entries(this.eventOutbox);
    if (entries.length > 200) this.eventOutbox = Object.fromEntries(entries.slice(-200));
    await this.persistOutbox();
    void this.tick();
    return { accepted: true };
  }

  async reportSendResult(result: ChatSendExecutionResult): Promise<void> {
    await this.client.completeChatSend(result);
    for (const [accountUid, active] of this.activeActions) {
      if (active.action.id === result.actionId) this.activeActions.delete(accountUid);
    }
  }

  private async tick(): Promise<void> {
    if (this.tickRunning) return;
    this.tickRunning = true;
    try {
      this.pruneCandidates();
      const runtime = await this.client.getChatRuntime();
      this.listenEnabled = runtime.listen_enabled;
      const accounts = new Set<string>();
      for (const candidate of this.candidates.values()) accounts.add(candidate.accountUid);
      for (const accountUid of accounts) {
        const leader = await this.elect(accountUid);
        if (!leader) continue;
        await this.client.reportChatHeartbeat(
          this.candidates.get(`${accountUid}:${leader.tabId}`) as Candidate,
          leader.epoch
        );
        if (runtime.listen_enabled) await this.flushAccountOutbox(accountUid, leader.epoch);
        if (runtime.send_enabled) await this.claimAndDispatch(accountUid, leader);
      }
      await this.expireUnreportedActions();
    } catch {
      // 后端或页面暂时不可用时保留 outbox，下一个 tick 自动续传。
    } finally {
      this.tickRunning = false;
    }
  }

  isListeningEnabled(): boolean {
    return this.listenEnabled;
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

  private async flushAccountOutbox(accountUid: string, leaderEpoch: number): Promise<void> {
    const messages = Object.values(this.eventOutbox)
      .filter((item) => item.accountUid === accountUid)
      .slice(0, 50);
    if (messages.length === 0) return;
    await this.client.reportChatMessages(messages, leaderEpoch);
    for (const message of messages) delete this.eventOutbox[message.eventId];
    await this.persistOutbox();
  }

  private async claimAndDispatch(accountUid: string, leader: LeaderLease): Promise<void> {
    if (this.activeActions.has(accountUid)) return;
    const action = await this.client.claimChatSendAction(accountUid, leader.tabId, leader.epoch);
    if (!action) return;
    await this.client.markChatDispatchStarted(action);
    this.activeActions.set(accountUid, { action, deadlineAt: Date.now() + 30_000 });
    const command: ChatSendCommand = { type: "BOSS_CHAT_SEND", targetTabId: leader.tabId, action };
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
