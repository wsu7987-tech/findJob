import { browser } from "#imports";

import type { FineJobExecutorClient } from "./client";
import { SharedActionGate } from "./shared-action-gate";
import type {
  BossChatCoordinatorStatus,
  ChatObservedMessage,
  ChatSendCommand,
  ChatSendExecutionResult,
  ChatTabHeartbeat,
  FineJobChatSendAction,
  ResumeSnapshotResult
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
type ResumeSnapshotWaiter = {
  actionId: string;
  resolve: (value: ResumeSnapshotResult) => void;
  reject: (reason: Error) => void;
  timer: number;
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
  private readonly pageBatches = new Map<string, { pageKey: string; count: number }>();
  private readonly resumeSnapshotWaiters = new Map<string, ResumeSnapshotWaiter>();

  constructor(
    private readonly client: FineJobExecutorClient,
    private readonly actionGate = new SharedActionGate()
  ) {}

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
    await this.refreshRuntime();
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
      // 所有真实动作在 FineJob 确认收到结果后复用执行器既有 cooldown gate。
      this.client.enterTaskCooldown?.(() => void this.processAccounts());
    } catch (error) {
      this.lastError = `自动代聊发送结果等待回传：${(error as Error).message}`;
    }
  }

  async reportResumeSnapshot(result: ResumeSnapshotResult): Promise<void> {
    const waiter = this.resumeSnapshotWaiters.get(result.requestId);
    if (!waiter || waiter.actionId !== result.actionId) return;
    globalThis.clearTimeout(waiter.timer);
    this.resumeSnapshotWaiters.delete(result.requestId);
    if (result.error) waiter.reject(new Error(result.error));
    else if (!Array.isArray(result.attachments) || !result.observedAt) waiter.reject(new Error("resume_snapshot_invalid"));
    else waiter.resolve(result);
  }

  private async processAccounts(): Promise<void> {
    if (this.processing) return;
    this.processing = true;
    try {
      await this.refreshRuntime();
      this.pruneCandidates();
      await this.flushResultOutbox();
      if (this.actionGate.isActive() || this.client.isTaskCooldownActive?.()) return;
      const accounts = new Set<string>();
      for (const candidate of this.candidates.values()) accounts.add(candidate.accountUid);
      for (const accountUid of accounts) {
        const leader = await this.elect(accountUid);
        if (!leader) continue;
        await this.client.reportChatHeartbeat(
          this.candidates.get(`${accountUid}:${leader.tabId}`) as Candidate,
          leader.epoch
        );
        // 即使自动生成关闭，也持续上报已观察 raw delta，供已确认动作的 preflight 使用。
        await this.flushAccountOutbox(accountUid, leader.epoch);
        await this.claimAndSnapshotResumeList(accountUid, leader);
        await this.claimAndDispatch(accountUid, leader);
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

  async isSendingEnabled(): Promise<boolean> {
    // MAIN World 的最终发送边界不信任内存快照；读取失败时按关闭处理。
    try {
      const runtime = await this.client.getChatRuntime();
      this.listenEnabled = runtime.listen_enabled;
      this.runtimeKnown = true;
      await this.updateRuntimeCache(runtime.listen_enabled, runtime.generation_enabled, runtime.send_enabled);
      return runtime.send_enabled;
    } catch (error) {
      this.lastError = (error as Error).message || "自动代聊发送开关读取失败";
      return false;
    }
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
      // 补传成功同样是已确认的真实动作结果，必须进入共享 cooldown。
      this.client.enterTaskCooldown?.(() => void this.processAccounts());
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

  private async refreshRuntime(): Promise<void> {
    try {
      const runtime = await this.client.getChatRuntime();
      this.listenEnabled = runtime.listen_enabled;
      this.runtimeKnown = true;
      await this.updateRuntimeCache(runtime.listen_enabled, runtime.generation_enabled, runtime.send_enabled);
    } catch (error) {
      // 离线期间仍保留接收 outbox，但发送一律关闭，等待下一次成功读取。
      this.runtimeKnown = false;
      if (this.runtimeCache) this.runtimeCache.sendEnabled = false;
      this.lastError = (error as Error).message || "自动代聊运行配置读取失败";
    }
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
    // 当前 /web/geek/chat 是唯一聊天页面匹配条件，具体 HR 由 Action 自身身份字段决定。
    const candidates = await this.client.listEligibleChatSendActions(accountUid);
    const matched = candidates.find((item) => item.action_type === "chat_message" || item.action_type === "resume_send");
    if (!matched) return;
    if (!this.actionGate.tryEnterBusy()) return;
    let claimed = false;
    let dispatchStarted = false;
    let dispatchStartRequested = false;
    let action: FineJobChatSendAction | null = null;
    try {
      const batch = this.pageBatches.get(accountUid);
      action = await this.client.claimChatSendAction(
        matched.id, accountUid, leader.tabId, leader.epoch
      );
      if (!action) return;
      claimed = true;
      let resumeSnapshot: Record<string, unknown> = {};
      if (action.action_type === "resume_send") {
        try {
          const result = await this.requestResumeSnapshot(action, leader);
          resumeSnapshot = {
            resume_list_snapshot: { attachments: result.attachments },
            resume_list_observed_at: result.observedAt
          };
        } catch (error) {
          resumeSnapshot = { resume_list_snapshot: { error: (error as Error).message || "resume_snapshot_failed" } };
        }
      }
      const preflight = await this.client.preflightChatSend(action, {
        // raw delta 已先上传；省略创建时基线，后端读取会话真实最新 revision。
        account_uid: accountUid,
        current_page_kind: "chat",
        current_page_key: `boss:${accountUid}:chat`,
        freshness_observed_at: new Date().toISOString(),
        ...resumeSnapshot
      });
      if (preflight.decision !== "dispatch" || !preflight.dispatch_token) return;
      dispatchStartRequested = true;
      await this.client.markChatDispatchStarted(preflight.action, preflight.dispatch_token);
      dispatchStarted = true;
      const pageKey = `boss:${accountUid}:chat`;
      this.pageBatches.set(accountUid, {
        pageKey,
        count: batch?.pageKey === pageKey ? (batch.count + 1) : 1
      });
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
    } catch (error) {
      // 请求 dispatch_started 后结果不确定时，保守回写 unknown，等待统一结果通道进入 cooldown。
      if ((dispatchStarted || dispatchStartRequested) && action) {
        await this.reportSendResult({
          actionId: action.id,
          executionEpoch: action.execution_epoch,
          outcome: "unknown",
          platformMessageId: "",
          clientMid: "",
          statusCode: "chat_dispatch_handoff_failed",
          message: (error as Error).message || "聊天发送交接失败",
          evidence: {}
        });
        return;
      }
      this.lastError = (error as Error).message || "聊天动作执行前失败";
    } finally {
      // claim、snapshot、Preflight 前未跨越真实平台副作用边界，可立即释放共享 gate。
      if (!claimed || (!dispatchStarted && !dispatchStartRequested)) this.actionGate.releaseBusy();
    }
  }

  private async claimAndSnapshotResumeList(accountUid: string, leader: LeaderLease): Promise<void> {
    // 旧版本后台客户端尚未提供只读 helper 接口时，继续执行既有业务 Action 链。
    if (typeof this.client.listPendingResumeListActions !== "function") return;
    const pending = await this.client.listPendingResumeListActions(accountUid);
    const candidate = pending[0];
    if (!candidate) return;
    const action = await this.client.claimChatSendAction(
      candidate.id, accountUid, leader.tabId, leader.epoch
    );
    if (!action || action.id !== candidate.id || action.operation_kind !== "resume_list") return;
    try {
      await this.client.completeResumeListSnapshot(action, await this.requestResumeSnapshot(action, leader));
    } catch (error) {
      await this.client.completeResumeListSnapshot(action, {
        requestId: "",
        actionId: action.id,
        attachments: [],
        observedAt: new Date().toISOString(),
        error: (error as Error).message || "resume_snapshot_failed"
      });
    }
  }

  private async requestResumeSnapshot(action: FineJobChatSendAction, leader: LeaderLease): Promise<ResumeSnapshotResult> {
    const requestId = crypto.randomUUID();
    const result = new Promise<ResumeSnapshotResult>((resolve, reject) => {
      const timer = globalThis.setTimeout(() => {
        this.resumeSnapshotWaiters.delete(requestId);
        reject(new Error("resume_snapshot_timeout"));
      }, 5_000) as unknown as number;
      this.resumeSnapshotWaiters.set(requestId, { actionId: action.id, resolve, reject, timer });
    });
    try {
      const command = {
        type: "BOSS_RESUME_SNAPSHOT_REQUEST" as const,
        targetTabId: leader.tabId,
        leaderEpoch: leader.epoch,
        requestId,
        actionId: action.id
      };
      // 领导者使用 Content 侧生成的逻辑 tabId，因此沿用聊天发送的标签页广播与目标页过滤。
      const tabs = await browser.tabs.query({ url: ["*://zhipin.com/*", "*://*.zhipin.com/*"] });
      const responses = await Promise.allSettled(
        tabs.flatMap((tab) => tab.id === undefined ? [] : [browser.tabs.sendMessage(tab.id, {
          type: "finejob:boss-executor:execute:v1",
          command
        })])
      );
      const accepted = responses.some(
        (response) => response.status === "fulfilled"
          && (response.value as { accepted?: boolean } | undefined)?.accepted === true
      );
      if (!accepted) {
        throw new Error("resume_snapshot_request_rejected");
      }
    } catch (error) {
      const waiter = this.resumeSnapshotWaiters.get(requestId);
      if (waiter) {
        globalThis.clearTimeout(waiter.timer);
        this.resumeSnapshotWaiters.delete(requestId);
        waiter.reject(error as Error);
      }
    }
    return result;
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
