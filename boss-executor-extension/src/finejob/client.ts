import { browser } from "#imports";

import packageJson from "../../package.json";
import type {
  ChatObservedMessage,
  ChatSendExecutionResult,
  ChatTabHeartbeat,
  ExecutorRuntimeState,
  FineJobChatSendAction,
  FineJobQueueAction,
  MainWorldExecutionResult
} from "./types";
import type { BossPageIdentity } from "../platform/boss/types";

const API_ROOT = "http://127.0.0.1:8000/api/fine-job/boss-executor";
const CREDENTIALS_KEY = "finejobBossExecutorCredentialsV1";
const PENDING_RESULTS_KEY = "finejobBossExecutorPendingResultsV1";
const CONTROL_CHANNEL_URL = "ws://127.0.0.1:8000/api/fine-job/boss-executor/channel";
const PROTOCOL_VERSION = "1.1";
const CAPABILITIES = [
  "default_greeting",
  "page_identity",
  "task_control",
  "chat_observe",
  "chat_send",
  "chat_multitab_leader"
];
const PAGE_MATCH_TIMEOUT_MS = 5_000;
const PAGE_MATCH_PROBE_INTERVAL_MS = 1_000;
const PAGE_OPEN_TIMEOUT_MS = 10_000;
const TASK_EXECUTION_TIMEOUT_MS = 40_000;

type Credentials = { executorId: string; token: string };

const initialState = (): ExecutorRuntimeState => ({
  connected: false,
  paired: false,
  detail: "尚未与FineJob配对",
  executor: null,
  queue: [],
  lastResult: ""
});

export class FineJobExecutorClient {
  private credentials: Credentials | null = null;
  private state = initialState();
  private startupPromise: Promise<void> | null = null;
  private heartbeatPromise: Promise<void> | null = null;
  private controlSocket: WebSocket | null = null;
  private controlReconnectTimer: number | null = null;
  private suppressNextReconnect = false;
  private pendingResults: Record<string, MainWorldExecutionResult> = {};
  private pageCheckTimer: number | null = null;
  private pageCheckCount = 0;
  private pageOpenTimer: number | null = null;
  private consecutivePageMatchFailures = 0;
  private waitingForPageOpen = false;
  private currentPageTaskId = "";
  private dispatchingTaskId = "";
  private resultSyncingTaskId = "";
  private pendingDispatch: { task: FineJobQueueAction; tabId?: string } | null = null;
  private resultSyncTimers: Record<string, number> = {};
  private taskCooldownTimer: number | null = null;
  private pageLoadWaitTimer: number | null = null;
  private executionTimeoutTimer: number | null = null;
  private timedOutResultKeys = new Set<string>();

  async start(): Promise<void> {
    if (this.startupPromise) return this.startupPromise;
    const startup = (async () => {
      const stored = await browser.storage.local.get([CREDENTIALS_KEY, PENDING_RESULTS_KEY]);
      this.credentials = (stored[CREDENTIALS_KEY] as Credentials | undefined) ?? null;
      this.pendingResults = (stored[PENDING_RESULTS_KEY] as Record<string, MainWorldExecutionResult> | undefined) ?? {};
      this.state.paired = this.credentials !== null;
      this.state.detail = this.credentials ? "正在连接FineJob" : "尚未与FineJob配对";
      if (this.credentials) {
        // 插件启动时先建立控制通道，再执行一次连接确认。
        this.connectControlChannel();
        await this.testHeartbeat().catch(() => undefined);
      }
    })();
    this.startupPromise = startup;
    try {
      await startup;
    } finally {
      if (this.startupPromise === startup) this.startupPromise = null;
    }
  }

  getState(): ExecutorRuntimeState {
    return structuredClone(this.state);
  }

  async pair(code: string): Promise<void> {
    // 等待后台完成存储恢复，避免初始化结果覆盖刚配对的凭证和连接状态。
    if (this.startupPromise) await this.startupPromise;
    const response = await this.request<{ executor_id: string; token: string }>("/pair", {
      method: "POST",
      body: JSON.stringify({
        code: code.trim(),
        label: "FineJob BOSS 执行器",
        protocol_version: PROTOCOL_VERSION,
        plugin_version: packageJson.version,
        capabilities: CAPABILITIES
      })
    }, false);
    this.credentials = { executorId: response.executor_id, token: response.token };
    await browser.storage.local.set({ [CREDENTIALS_KEY]: this.credentials });
    this.state.paired = true;
    this.state.detail = "配对成功；正在连接FineJob";
    this.connectControlChannel();
    await this.testHeartbeat();
  }

  async testHeartbeat(): Promise<void> {
    if (!this.credentials) throw new Error("插件尚未与FineJob配对");
    if (this.heartbeatPromise) return this.heartbeatPromise;
    this.heartbeatPromise = (async () => {
      try {
        const heartbeat = await this.request<{
          executor: ExecutorRuntimeState["executor"];
          queue: { actions: FineJobQueueAction[] };
        }>("/heartbeat", {
          method: "POST",
          body: JSON.stringify({
            protocol_version: PROTOCOL_VERSION,
            plugin_version: packageJson.version,
            capabilities: CAPABILITIES,
            browser_connected: true,
            risk_state: "none"
          })
        });
        this.state.connected = true;
        this.state.paired = true;
        this.state.executor = heartbeat.executor;
        this.state.queue = heartbeat.queue.actions;
        if (!this.shouldKeepDetailAfterHeartbeat()) {
          this.state.detail = "FineJob通信正常";
        }
        await this.flushPendingResults();
      } catch (error) {
        this.state.connected = false;
        this.state.detail = (error as Error).message || "FineJob连接失败";
        throw error;
      }
    })();
    try {
      await this.heartbeatPromise;
    } finally {
      this.heartbeatPromise = null;
    }
  }

  private connectControlChannel(): void {
    if (!this.credentials || (this.controlSocket && (
      this.controlSocket.readyState === WebSocket.OPEN
      || this.controlSocket.readyState === WebSocket.CONNECTING
    ))) return;
    const token = encodeURIComponent(this.credentials.token);
    const socket = new WebSocket(CONTROL_CHANNEL_URL + "?token=" + token);
    this.controlSocket = socket;
    socket.addEventListener("open", () => {
      // 控制通道建立后主动同步一次心跳，立即刷新FineJob的插件联通状态。
      void this.testHeartbeat().catch(() => undefined);
    });
    socket.addEventListener("message", (event) => {
      void this.handleControlMessage(socket, String(event.data));
    });
    socket.addEventListener("close", (event) => {
      if (this.controlSocket !== socket) return;
      this.controlSocket = null;
      if (this.suppressNextReconnect) {
        this.suppressNextReconnect = false;
        this.state.connected = false;
        return;
      }
      if (event.code === 4001) {
        void this.clearLocalConnection();
        return;
      }
      this.scheduleControlReconnect();
    });
  }

  private scheduleControlReconnect(): void {
    if (!this.credentials || this.controlReconnectTimer !== null) return;
    // 控制通道断开后只通过一次性延迟重连恢复，不建立轮询请求。
    this.controlReconnectTimer = globalThis.setTimeout(() => {
      this.controlReconnectTimer = null;
      this.connectControlChannel();
    }, 2000) as unknown as number;
  }

  private async handleControlMessage(socket: WebSocket, rawMessage: string): Promise<void> {
    let message: Record<string, unknown>;
    try {
      message = JSON.parse(rawMessage) as Record<string, unknown>;
    } catch {
      return;
    }
    if (message.type === "task_queue" && Array.isArray(message.tasks)) {
      this.state.queue = message.tasks as FineJobQueueAction[];
      if (this.isExecutorMessage(message.executor)) this.state.executor = message.executor;
      if (this.state.executor?.queue_state !== "running") {
        this.stopPageChecks();
        this.stopPageOpenTimer();
        this.stopExecutionWaits();
        this.waitingForPageOpen = false;
        this.state.detail = "自动打招呼已暂停";
        return;
      }
      if (this.resultSyncingTaskId) return;
      if (this.hasActiveQueueTask() && !this.dispatchingTaskId && !this.pendingDispatch) {
        this.stopPageChecks();
        this.stopPageOpenTimer();
        this.waitingForPageOpen = false;
        this.state.detail = "已有任务正在执行，等待状态回写";
        return;
      }
      if (this.taskCooldownTimer !== null || this.pageLoadWaitTimer !== null || this.dispatchingTaskId) return;
      if (this.waitingForPageOpen) {
        if (this.state.queue.length === 0) {
          this.waitingForPageOpen = false;
          this.state.detail = "当前没有待执行任务";
          return;
        }
        this.state.detail = "正在等 FineJob 打开页面";
        return;
      }
      this.state.detail = this.state.queue.length > 0 ? "正在匹配任务页面" : "当前没有待执行任务";
      this.requestTaskPage(false);
      return;
    }
    if (message.type === "executor_control" && socket.readyState === WebSocket.OPEN) {
      const command = message.command === "start" ? "start" : "pause";
      this.applyQueueState(command === "start" ? "running" : "paused");
      socket.send(JSON.stringify({
        type: "executor_state_changed",
        request_id: String(message.request_id || ""),
        queue_state: this.state.executor?.queue_state
      }));
      if (command === "start") this.requestTaskPage(false);
      return;
    }
    if (message.type === "page_opened") {
      const taskId = String(message.task_id || "");
      const opened = message.success === true;
      this.stopPageOpenTimer();
      this.waitingForPageOpen = false;
      if (!opened) {
        this.stopPageChecks();
        this.currentPageTaskId = "";
        if (message.queue_empty === true) this.state.queue = [];
        this.state.detail = message.queue_empty === true
          ? "当前没有待执行任务"
          : `FineJob 打开页面失败：${String(message.message || "未知错误")}`;
        if (
          message.busy !== true
          && message.queue_empty !== true
          && this.state.executor?.queue_state === "running"
          && this.hasQueuedTask()
          && !this.isTaskBusy(false)
        ) {
          this.requestTaskPage(false);
        }
        return;
      }
      this.currentPageTaskId = taskId;
      const task = this.state.queue.find((item) => item.id === taskId);
      if (task?.task_type === "TEST_DELAY") {
        this.dispatchTestDelay(task);
        return;
      }
      this.startPageChecks();
      return;
    }
    if (message.type === "task_match_synced") {
      if (typeof message.task_id === "string" && Array.isArray((message.queue as { actions?: unknown[] } | undefined)?.actions)) {
        this.state.queue = (message.queue as { actions: FineJobQueueAction[] }).actions;
      }
      this.handleTaskMatchSynced(message);
      return;
    }
    if (message.type === "task_result_synced") {
      await this.handleTaskResultSynced(message);
      return;
    }
    if (message.type === "task_sync_failed") {
      await this.handleTaskSyncFailed(message);
      return;
    }
    if (message.type !== "heartbeat_test" || !message.request_id || socket.readyState !== WebSocket.OPEN) return;
    try {
      await this.testHeartbeat();
      socket.send(JSON.stringify({ type: "heartbeat_test_result", request_id: message.request_id, ok: true }));
    } catch (error) {
      socket.send(JSON.stringify({
        type: "heartbeat_test_result",
        request_id: message.request_id,
        ok: false,
        message: (error as Error).message || "插件心跳失败。"
      }));
    }
  }

  async disconnect(): Promise<void> {
    try {
      if (this.credentials) {
        await this.request("/disconnect", { method: "POST" });
      }
    } finally {
      await this.clearLocalConnection();
    }
  }

  private async clearLocalConnection(): Promise<void> {
    if (this.controlReconnectTimer !== null) {
      globalThis.clearTimeout(this.controlReconnectTimer);
      this.controlReconnectTimer = null;
    }
    for (const timer of Object.values(this.resultSyncTimers)) globalThis.clearTimeout(timer);
    this.resultSyncTimers = {};
    const socket = this.controlSocket;
    this.controlSocket = null;
    this.suppressNextReconnect = false;
    this.stopPageChecks();
    this.stopPageOpenTimer();
    this.stopExecutionWaits();
    this.stopExecutionTimeout();
    this.waitingForPageOpen = false;
    this.currentPageTaskId = "";
    this.dispatchingTaskId = "";
    this.resultSyncingTaskId = "";
    this.pendingDispatch = null;
    this.consecutivePageMatchFailures = 0;
    this.timedOutResultKeys.clear();
    if (socket) socket.close();
    this.credentials = null;
    this.state = initialState();
    await browser.storage.local.remove(CREDENTIALS_KEY);
  }

  async control(command: "start" | "pause"): Promise<void> {
    this.applyQueueState(command === "start" ? "running" : "paused");
    const response = await this.request<{ executor: ExecutorRuntimeState["executor"] }>("/control", {
      method: "POST",
      body: JSON.stringify({ command })
    });
    if (response.executor) this.state.executor = response.executor;
    if (command === "start") this.requestTaskPage(false);
  }

  private applyQueueState(queueState: "running" | "paused"): void {
    if (this.state.executor) this.state.executor.queue_state = queueState;
    this.state.detail = queueState === "running" ? "自动打招呼运行中" : "自动打招呼已暂停";
    if (queueState === "paused") {
      this.stopPageChecks();
      this.stopPageOpenTimer();
      this.stopExecutionWaits();
      this.stopExecutionTimeout();
      this.waitingForPageOpen = false;
      this.dispatchingTaskId = "";
      this.pendingDispatch = null;
      this.reportRuntimeState("idle");
    }
  }

  async reportExecutionResult(result: MainWorldExecutionResult): Promise<void> {
    if (this.timedOutResultKeys.has(this.resultKey(result)) && result.statusCode !== "TASK_EXECUTION_TIMEOUT") return;
    this.stopExecutionTimeout();
    this.dispatchingTaskId = "";
    this.resultSyncingTaskId = result.taskId;
    const succeeded = result.outcome === "accepted" || result.outcome === "succeeded";
    await this.persistPendingResult(result);
    if (succeeded) {
      this.waitingForPageOpen = true;
      this.state.detail = `${this.taskLabel(result.taskId)}任务执行完成，正在同步状态`;
    } else if (result.outcome === "unknown") {
      this.state.detail = `${this.taskLabel(result.taskId)}任务执行结果未知，正在同步状态`;
    } else {
      this.state.detail = `${this.taskLabel(result.taskId)}任务执行失败，正在同步状态`;
    }
    if (this.sendControlMessage({
      type: succeeded ? "task_succeeded" : "task_failed",
      task_id: result.taskId,
      execution_epoch: result.executionEpoch,
      execution_result: result.message,
      error_message: result.message,
      status_code: result.statusCode,
      outcome: result.outcome,
      contacted: result.contacted,
      platform_result: result.evidence,
      completed_at: new Date().toISOString(),
      failed_at: new Date().toISOString()
    })) {
      this.scheduleResultSyncFallback(result);
      return;
    }
    this.state.detail = "同步失败：与 FineJob 的运行通道未连接";
    try {
      await this.submitExecutionResult(result);
    } catch (error) {
      this.state.detail = (error as Error).message;
    }
  }

  async getChatRuntime(): Promise<{
    listen_enabled: boolean;
    generation_enabled: boolean;
    send_enabled: boolean;
  }> {
    const response = await this.chatRequest<{ runtime: {
      listen_enabled: boolean;
      generation_enabled: boolean;
      send_enabled: boolean;
    } }>("/runtime", { method: "GET" }, false);
    return response.runtime;
  }

  async reportChatHeartbeat(heartbeat: ChatTabHeartbeat, leaderEpoch: number): Promise<void> {
    await this.chatRequest("/executor/heartbeat", {
      method: "POST",
      body: JSON.stringify({
        account_uid: heartbeat.accountUid,
        tab_id: heartbeat.tabId,
        leader_epoch: leaderEpoch,
        is_leader: true,
        lease_expires_at: new Date(Date.now() + 20_000).toISOString()
      })
    });
  }

  async reportChatMessages(messages: ChatObservedMessage[], leaderEpoch: number): Promise<void> {
    await this.chatRequest("/executor/events/batch", {
      method: "POST",
      body: JSON.stringify({
        events: messages.map((message) => ({
          event_id: message.eventId,
          event_type: "message",
          account_uid: message.accountUid,
          leader_epoch: leaderEpoch,
          message: {
            platform_message_id: message.platformMessageId,
            direction: message.direction,
            message_type: message.messageType,
            content: message.content,
            sender_uid: message.senderUid,
            receiver_uid: message.receiverUid,
            client_mid: message.clientMid,
            peer_uid: message.peerUid,
            encrypt_peer_uid: message.encryptPeerUid,
            security_id: message.securityId,
            encrypt_job_id: message.encryptJobId,
            job_title: message.jobTitle,
            peer_name: message.peerName,
            company_name: message.companyName,
            sent_at: message.sentAt,
            observed_at: message.observedAt,
            source: message.source,
            raw_meta: message.rawMeta
          }
        }))
      })
    });
  }

  async claimChatSendAction(
    accountUid: string,
    tabId: string,
    leaderEpoch: number
  ): Promise<FineJobChatSendAction | null> {
    const response = await this.chatRequest<{ action: FineJobChatSendAction | null }>(
      "/executor/actions/claim",
      {
        method: "POST",
        body: JSON.stringify({
          account_uid: accountUid,
          tab_id: tabId,
          leader_epoch: leaderEpoch
        })
      }
    );
    return response.action;
  }

  async markChatDispatchStarted(action: FineJobChatSendAction): Promise<void> {
    await this.chatRequest(`/executor/actions/${encodeURIComponent(action.id)}/dispatch-started`, {
      method: "POST",
      body: JSON.stringify({ execution_epoch: action.execution_epoch })
    });
  }

  async completeChatSend(result: ChatSendExecutionResult): Promise<void> {
    await this.chatRequest(`/executor/actions/${encodeURIComponent(result.actionId)}/complete`, {
      method: "POST",
      body: JSON.stringify({
        execution_epoch: result.executionEpoch,
        outcome: result.outcome,
        platform_message_id: result.platformMessageId,
        client_mid: result.clientMid,
        status_code: result.statusCode,
        message: result.message,
        evidence: result.evidence
      })
    });
  }

  async reportBossPageIdentity(tabId: string, identity: BossPageIdentity): Promise<void> {
    const matched = identity.state === "ready" && identity.job
      ? this.state.queue.find((task) =>
          task.status === "queued"
          && task.encrypt_job_id === identity.job?.encryptJobId
          && (!this.currentPageTaskId || task.id === this.currentPageTaskId)
        )
      : undefined;
    if (matched && identity.job && !this.isTaskBusy()) {
      this.stopPageChecks();
      this.waitingForPageOpen = false;
      this.currentPageTaskId = matched.id;
      this.consecutivePageMatchFailures = 0;
      await this.dispatchDefaultGreeting(tabId, matched);
      return;
    }
    if (
      this.pageCheckTimer === null
      && !this.waitingForPageOpen
      && !this.isTaskBusy()
      && this.state.queue.length > 0
      && this.state.executor?.queue_state === "running"
    ) {
      this.requestTaskPage(false);
    }
  }

  private startPageChecks(): void {
    if (
      this.state.queue.length === 0
      || this.waitingForPageOpen
      || this.isTaskBusy()
      || this.state.executor?.queue_state !== "running"
    ) return;
    this.stopPageChecks();
    // 每轮最多匹配5秒，超时后交给FineJob打开目标页面。
    this.pageCheckCount = 0;
    this.state.detail = "正在匹配任务页面";
    this.requestCurrentPageProbe();
    // 5秒窗口内持续触发轻量页面身份读取，避免页面刚加载时错过匹配。
    this.pageCheckTimer = globalThis.setInterval(() => {
      this.pageCheckCount += 1;
      if (this.pageCheckCount >= Math.floor(PAGE_MATCH_TIMEOUT_MS / PAGE_MATCH_PROBE_INTERVAL_MS)) {
        this.handlePageCheckLimit();
        return;
      }
      this.requestCurrentPageProbe();
    }, PAGE_MATCH_PROBE_INTERVAL_MS) as unknown as number;
  }

  private stopPageChecks(): void {
    if (this.pageCheckTimer !== null) {
      globalThis.clearInterval(this.pageCheckTimer);
      this.pageCheckTimer = null;
    }
  }

  private startPageOpenTimer(): void {
    this.stopPageOpenTimer();
    // FineJob打开页面没有返回结果时退出等待态，避免插件长期卡在打开页面。
    this.pageOpenTimer = globalThis.setTimeout(() => {
      this.pageOpenTimer = null;
      if (!this.waitingForPageOpen) return;
      this.waitingForPageOpen = false;
      this.sendControlMessage({
        type: "execution_error",
        error_message: "FineJob 打开页面后未返回页面打开结果",
        occurred_at: new Date().toISOString()
      });
      this.closeControlChannelForIssue("FineJob 打开页面超时，已断开连接");
    }, PAGE_OPEN_TIMEOUT_MS) as unknown as number;
  }

  private stopPageOpenTimer(): void {
    if (this.pageOpenTimer !== null) {
      globalThis.clearTimeout(this.pageOpenTimer);
      this.pageOpenTimer = null;
    }
  }

  private handlePageCheckLimit(): void {
    this.stopPageChecks();
    if (this.state.queue.length === 0 || this.isTaskBusy(false)) return;
    this.consecutivePageMatchFailures += 1;
    this.sendControlMessage({
      type: "execution_error",
      task_id: this.currentPageTaskId,
      failure_kind: "page_match_failed",
      disconnect: this.consecutivePageMatchFailures >= 3,
      error_message: "任务页面打开后5秒内未匹配执行任务",
      occurred_at: new Date().toISOString()
    });
    if (this.consecutivePageMatchFailures >= 3) {
      this.closeControlChannelForIssue("连续匹配页面失败3次，已断开连接");
      return;
    }
    this.state.detail = "匹配任务页面失败，正在请求重新打开";
    this.requestTaskPage(true);
  }

  private requestTaskPage(retry: boolean): void {
    if (this.waitingForPageOpen || this.state.queue.length === 0 || this.isTaskBusy(false)) return;
    if (!this.sendControlMessage({ type: "open_task_page" })) return;
    this.waitingForPageOpen = true;
    this.startPageOpenTimer();
    if (!retry) this.currentPageTaskId = "";
    this.state.detail = "正在等 FineJob 打开页面";
  }

  private requestCurrentPageProbe(): void {
    if (this.state.queue.length === 0 || this.waitingForPageOpen || this.isTaskBusy()) return;
    void browser.tabs.query({ url: ["*://zhipin.com/*", "*://*.zhipin.com/*"] }).then((tabs) => {
      const tab = tabs.find((item) => item.active) ?? tabs[0];
      if (tab?.id !== undefined) {
        void browser.tabs.sendMessage(tab.id, { type: "finejob:boss-executor:probe:v1" }).catch(() => undefined);
      }
    }).catch(() => undefined);
  }

  private closeControlChannelForIssue(detail: string): void {
    this.suppressNextReconnect = true;
    this.state.connected = false;
    this.state.detail = detail;
    this.controlSocket?.close();
  }

  private async dispatchDefaultGreeting(tabId: string, task: FineJobQueueAction): Promise<void> {
    if (this.isTaskBusy()) return;
    if (!this.sendControlMessage({
      type: "match_task",
      task_id: task.id,
      execution_epoch: task.execution_epoch
    })) return;
    this.dispatchingTaskId = task.id;
    this.pendingDispatch = { task, tabId };
    this.state.detail = `正在锁定任务：${task.job_title}`;
  }

  private startDefaultGreetingAfterMatch(tabId: string, task: FineJobQueueAction): void {
    this.state.detail = `正在执行任务：${task.job_title}`;
    this.startExecutionTimeout(task);
    void browser.tabs.query({ url: ["*://zhipin.com/*", "*://*.zhipin.com/*"] }).then(async (tabs) => {
      for (const tab of tabs) {
        if (tab.id === undefined) continue;
        await browser.tabs.sendMessage(tab.id, {
          type: "finejob:boss-executor:execute:v1",
          command: {
            type: "BOSS_DEFAULT_GREETING",
            taskId: task.id,
            executionEpoch: task.execution_epoch,
            encryptJobId: task.encrypt_job_id,
            targetTabId: tabId
          }
        }).catch(() => undefined);
      }
    }).catch(() => undefined);
  }

  private dispatchTestDelay(task: FineJobQueueAction): void {
    if (this.isTaskBusy()) return;
    if (!this.sendControlMessage({
      type: "match_task",
      task_id: task.id,
      execution_epoch: task.execution_epoch
    })) return;
    this.dispatchingTaskId = task.id;
    this.pendingDispatch = { task };
    this.state.detail = `正在锁定任务：${task.job_title}`;
  }

  private startTestDelayAfterMatch(task: FineJobQueueAction): void {
    const delaySeconds = this.normalizeDelaySeconds(task.delay_seconds);
    this.state.detail = `正在执行任务：${task.job_title}`;
    this.startExecutionTimeout(task);
    // 测试任务按后端下发秒数延迟完成，便于验证执行器队列流转。
    globalThis.setTimeout(() => {
      if (this.dispatchingTaskId !== task.id) return;
      void this.reportExecutionResult({
        taskId: task.id,
        executionEpoch: task.execution_epoch,
        outcome: "succeeded",
        contacted: null,
        statusCode: "TEST_DELAY_COMPLETED",
        message: `测试任务已等待 ${delaySeconds} 秒并完成`,
        evidence: { delaySeconds, closePageAfterCompletion: task.close_page_after_completion }
      });
    }, delaySeconds * 1000);
  }

  private normalizeDelaySeconds(value: unknown): number {
    const delaySeconds = Math.floor(Number(value));
    if (!Number.isFinite(delaySeconds)) return 3;
    return Math.min(600, Math.max(1, delaySeconds));
  }

  private scheduleTaskCooldown(): void {
    if (this.taskCooldownTimer !== null || this.state.queue.length === 0) return;
    const seconds = this.randomDelaySeconds(4, this.state.executor?.task_cooldown_max_seconds ?? 4);
    const detail = `任务间隔冷却等待 ${seconds} 秒`;
    this.waitingForPageOpen = false;
    this.stopPageChecks();
    this.state.detail = detail;
    this.reportRuntimeState("task_cooldown", detail, seconds);
    this.taskCooldownTimer = globalThis.setTimeout(() => {
      this.taskCooldownTimer = null;
      this.reportRuntimeState("idle");
      if (this.state.executor?.queue_state === "running" && this.state.queue.length > 0) {
        this.state.detail = "正在匹配任务页面";
        this.requestTaskPage(false);
      }
    }, seconds * 1000) as unknown as number;
  }

  private schedulePageLoadWait(task: FineJobQueueAction, run: () => void | Promise<void>): void {
    if (this.pageLoadWaitTimer !== null) return;
    const seconds = this.randomDelaySeconds(3, this.state.executor?.page_load_wait_max_seconds ?? 3);
    this.state.detail = `页面加载冷却等待 ${seconds} 秒：${task.job_title}`;
    this.pageLoadWaitTimer = globalThis.setTimeout(() => {
      this.pageLoadWaitTimer = null;
      if (this.dispatchingTaskId !== task.id || this.state.executor?.queue_state !== "running") return;
      void run();
    }, seconds * 1000) as unknown as number;
  }

  private stopExecutionWaits(): void {
    if (this.taskCooldownTimer !== null) {
      globalThis.clearTimeout(this.taskCooldownTimer);
      this.taskCooldownTimer = null;
    }
    if (this.pageLoadWaitTimer !== null) {
      globalThis.clearTimeout(this.pageLoadWaitTimer);
      this.pageLoadWaitTimer = null;
    }
  }

  private startExecutionTimeout(task: FineJobQueueAction): void {
    this.stopExecutionTimeout();
    const resultKey = `${task.id}:${task.execution_epoch}`;
    // 单个任务执行超过40秒时主动回写结果未知，避免队列长期停留在执行中。
    this.executionTimeoutTimer = globalThis.setTimeout(() => {
      this.executionTimeoutTimer = null;
      if (this.dispatchingTaskId !== task.id) return;
      this.timedOutResultKeys.add(resultKey);
      void this.reportExecutionResult({
        taskId: task.id,
        executionEpoch: task.execution_epoch,
        outcome: "unknown",
        contacted: null,
        statusCode: "TASK_EXECUTION_TIMEOUT",
        message: "任务执行超过40秒未返回结果",
        evidence: { timeoutSeconds: TASK_EXECUTION_TIMEOUT_MS / 1000 }
      });
    }, TASK_EXECUTION_TIMEOUT_MS) as unknown as number;
  }

  private stopExecutionTimeout(): void {
    if (this.executionTimeoutTimer === null) return;
    globalThis.clearTimeout(this.executionTimeoutTimer);
    this.executionTimeoutTimer = null;
  }

  private isTaskBusy(includeActiveQueue = true): boolean {
    return Boolean(
      this.dispatchingTaskId
      || this.resultSyncingTaskId
      || this.pendingDispatch
      || this.taskCooldownTimer !== null
      || this.pageLoadWaitTimer !== null
      || (includeActiveQueue && this.hasActiveQueueTask())
    );
  }

  private hasActiveQueueTask(): boolean {
    return this.state.queue.some((task) =>
      task.execution_state === "running" || task.status === "running" || task.status === "leased"
    );
  }

  private hasQueuedTask(): boolean {
    return this.state.queue.some((task) => task.status === "queued" && task.execution_state === "queued");
  }

  private randomDelaySeconds(minSeconds: number, maxSeconds: number): number {
    const normalizedMax = Math.max(minSeconds, Math.floor(Number(maxSeconds) || minSeconds));
    return minSeconds + Math.floor(Math.random() * (normalizedMax - minSeconds + 1));
  }

  private reportRuntimeState(phase: "idle" | "task_cooldown", detail = "", seconds = 0): void {
    const untilAt = seconds > 0 ? new Date(Date.now() + seconds * 1000).toISOString() : "";
    this.sendControlMessage({
      type: "runtime_state",
      phase,
      detail,
      seconds,
      until_at: untilAt
    });
  }

  private isExecutorMessage(value: unknown): value is ExecutorRuntimeState["executor"] {
    return typeof value === "object" && value !== null && "queue_state" in value;
  }

  private isQueueAction(value: unknown): value is FineJobQueueAction {
    return typeof value === "object" && value !== null && "id" in value && "task_type" in value;
  }

  private handleTaskMatchSynced(message: Record<string, unknown>): void {
    const taskId = String(message.task_id || "");
    const pending = this.pendingDispatch;
    if (!pending || pending.task.id !== taskId) return;
    const task = this.isQueueAction(message.task) ? message.task : pending.task;
    this.pendingDispatch = null;
    if (task.task_type === "TEST_DELAY") {
      this.startTestDelayAfterMatch(task);
      return;
    }
    if (pending.tabId) this.startDefaultGreetingAfterMatch(pending.tabId, task);
  }

  private shouldKeepDetailAfterHeartbeat(): boolean {
    return this.state.detail.includes("正在同步状态")
      || this.state.detail.includes("同步失败")
      || this.state.detail.includes("冷却等待")
      || this.state.detail.includes("页面加载等待")
      || this.state.detail.includes("结果回写等待确认")
      || this.state.detail.includes("回写等待重试");
  }

  private async handleTaskResultSynced(message: Record<string, unknown>): Promise<void> {
    const taskId = String(message.task_id || "");
    const executionEpoch = Number(message.execution_epoch ?? NaN);
    const result = this.pendingResultFor(taskId, executionEpoch);
    if (result) {
      this.clearResultSyncFallback(result);
      await this.removePendingResult(result);
    }
    if (!this.resultSyncingTaskId || this.resultSyncingTaskId === taskId) {
      this.resultSyncingTaskId = "";
    }
    const queue = message.queue as { actions?: FineJobQueueAction[] } | undefined;
    if (Array.isArray(queue?.actions)) this.state.queue = queue.actions;
    this.waitingForPageOpen = false;
    if (this.state.queue.length === 0) {
      this.state.detail = "当前没有待执行任务";
      return;
    }
    if (this.state.executor?.queue_state === "running") {
      this.scheduleTaskCooldown();
    }
  }

  private async handleTaskSyncFailed(message: Record<string, unknown>): Promise<void> {
    const messageType = String(message.message_type || "");
    const taskId = String(message.task_id || "");
    const executionEpoch = Number(message.execution_epoch ?? NaN);
    if (messageType === "match_task") {
      if (!taskId || this.dispatchingTaskId === taskId) {
        this.dispatchingTaskId = "";
        this.pendingDispatch = null;
      }
      this.state.detail = `任务锁定失败：${String(message.message || "FineJob 未能锁定任务")}`;
      return;
    }
    if (messageType === "task_succeeded" || messageType === "task_failed") {
      this.waitingForPageOpen = false;
      const result = this.pendingResultFor(taskId, executionEpoch);
      if (result) {
        this.clearResultSyncFallback(result);
        try {
          await this.submitExecutionResult(result);
          return;
        } catch (error) {
          this.state.detail = `结果回写等待重试：${(error as Error).message}`;
          return;
        }
      }
      if (!this.resultSyncingTaskId || this.resultSyncingTaskId === taskId) {
        this.resultSyncingTaskId = "";
      }
    }
    this.state.detail = `同步失败：${String(message.message || "FineJob 未能处理插件消息")}`;
  }

  private sendControlMessage(message: Record<string, unknown>): boolean {
    if (!this.controlSocket || this.controlSocket.readyState !== WebSocket.OPEN) return false;
    try {
      this.controlSocket.send(JSON.stringify(message));
      return true;
    } catch {
      return false;
    }
  }

  private taskLabel(taskId: string): string {
    return this.state.queue.find((task) => task.id === taskId)?.job_title || "当前";
  }

  private pendingResultFor(taskId: string, executionEpoch: number): MainWorldExecutionResult | undefined {
    return Object.values(this.pendingResults).find((result) =>
      result.taskId === taskId && Number(result.executionEpoch) === executionEpoch
    );
  }

  private resultKey(result: MainWorldExecutionResult): string {
    return `${result.taskId}:${result.executionEpoch}`;
  }

  private scheduleResultSyncFallback(result: MainWorldExecutionResult): void {
    const key = this.resultKey(result);
    if (this.resultSyncTimers[key] !== undefined) globalThis.clearTimeout(this.resultSyncTimers[key]);
    // WebSocket 发送成功但未收到后端回执时，单次补偿回写避免任务停留在待执行。
    this.resultSyncTimers[key] = globalThis.setTimeout(() => {
      delete this.resultSyncTimers[key];
      const pending = this.pendingResults[key];
      if (!pending) return;
      this.waitingForPageOpen = false;
      this.state.detail = "结果回写等待确认，正在使用补偿接口同步";
      void this.submitExecutionResult(pending).catch((error) => {
        this.state.detail = `结果回写等待重试：${(error as Error).message}`;
      });
    }, 5_000) as unknown as number;
  }

  private clearResultSyncFallback(result: MainWorldExecutionResult): void {
    const key = this.resultKey(result);
    const timer = this.resultSyncTimers[key];
    if (timer === undefined) return;
    globalThis.clearTimeout(timer);
    delete this.resultSyncTimers[key];
  }

  private async persistPendingResult(result: MainWorldExecutionResult): Promise<void> {
    this.pendingResults[this.resultKey(result)] = result;
    await browser.storage.local.set({ [PENDING_RESULTS_KEY]: this.pendingResults });
  }

  private async removePendingResult(result: MainWorldExecutionResult): Promise<void> {
    this.clearResultSyncFallback(result);
    delete this.pendingResults[this.resultKey(result)];
    await browser.storage.local.set({ [PENDING_RESULTS_KEY]: this.pendingResults });
  }

  private async submitExecutionResult(result: MainWorldExecutionResult): Promise<void> {
    const response = await this.request<{ task: FineJobQueueAction; queue: { actions: FineJobQueueAction[] } }>(
      `/tasks/${encodeURIComponent(result.taskId)}/complete`,
      {
        method: "POST",
        body: JSON.stringify({
          execution_epoch: result.executionEpoch,
          outcome: result.outcome,
          contacted: result.contacted,
          status_code: result.statusCode,
          message: result.message,
          evidence: result.evidence
        })
      }
    );
    await this.removePendingResult(result);
    if (this.resultSyncingTaskId === result.taskId) this.resultSyncingTaskId = "";
    this.state.detail = "状态已通过补偿接口同步";
    this.state.queue = response.queue.actions;
    this.waitingForPageOpen = false;
    if (this.state.executor?.queue_state === "running" && this.state.queue.length > 0) {
      this.scheduleTaskCooldown();
    }
  }

  private async flushPendingResults(): Promise<void> {
    for (const result of Object.values(this.pendingResults)) {
      try {
        await this.submitExecutionResult(result);
      } catch (error) {
        this.state.detail = `结果回写等待重试：${(error as Error).message}`;
        break;
      }
    }
  }

  private async request<T = unknown>(path: string, init: RequestInit, authenticated = true): Promise<T> {
    const headers = new Headers(init.headers);
    headers.set("Content-Type", "application/json");
    if (authenticated) {
      if (!this.credentials) throw new Error("插件尚未与FineJob配对");
      headers.set("Authorization", `Bearer ${this.credentials.token}`);
    }
    let response: Response;
    try {
      response = await fetch(`${API_ROOT}${path}`, { ...init, headers, signal: AbortSignal.timeout(5000) });
    } catch {
      throw new Error("无法连接FineJob后端（127.0.0.1:8000）");
    }
    const body = await response.json().catch(() => ({})) as Record<string, unknown>;
    if (!response.ok) {
      throw new Error(String(body.error_message || `FineJob请求失败：${response.status}`));
    }
    return body as T;
  }

  private async chatRequest<T = unknown>(
    path: string,
    init: RequestInit,
    authenticated = true
  ): Promise<T> {
    const headers = new Headers(init.headers);
    headers.set("Content-Type", "application/json");
    if (authenticated) {
      if (!this.credentials) throw new Error("插件尚未与FineJob配对");
      headers.set("Authorization", `Bearer ${this.credentials.token}`);
    }
    let response: Response;
    try {
      response = await fetch(`http://127.0.0.1:8000/api/fine-job/boss-chat${path}`, {
        ...init,
        headers,
        signal: AbortSignal.timeout(5000)
      });
    } catch {
      throw new Error("无法连接FineJob自动代聊服务");
    }
    const body = await response.json().catch(() => ({})) as Record<string, unknown>;
    if (!response.ok) throw new Error(String(body.error_message || `自动代聊请求失败：${response.status}`));
    return body as T;
  }
}

export const fineJobExecutorClient = new FineJobExecutorClient();
