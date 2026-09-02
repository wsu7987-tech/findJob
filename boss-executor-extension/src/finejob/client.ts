import { browser } from "#imports";

import packageJson from "../../package.json";
import type { BossReadOnlySnapshot } from "../platform/boss/types";
import type {
  ChatObservedMessage,
  ChatSendExecutionResult,
  ChatTabHeartbeat,
  ExecutorRuntimeState,
  FineJobChatSendAction,
  FineJobQueueAction,
  MainWorldExecutionResult
} from "./types";

const API_ROOT = "http://127.0.0.1:8000/api/fine-job/boss-executor";
const CREDENTIALS_KEY = "finejobBossExecutorCredentialsV1";
const PENDING_RESULTS_KEY = "finejobBossExecutorPendingResultsV1";
const COMMAND_MESSAGE = "finejob:boss-executor:execute:v1";
const CONTROL_CHANNEL_URL = "ws://127.0.0.1:8000/api/fine-job/boss-executor/channel";
const PROTOCOL_VERSION = "1.1";
const TASK_HEARTBEAT_TIMEOUT_MS = 10_000;
const CAPABILITIES = [
  "default_greeting",
  "page_identity",
  "queue_control",
  "chat_observe",
  "chat_send",
  "chat_multitab_leader"
];

type Credentials = { executorId: string; token: string };

const initialState = (): ExecutorRuntimeState => ({
  connected: false,
  paired: false,
  detail: "尚未与FineJob配对",
  executor: null,
  queue: [],
  failedQueue: [],
  currentAction: null,
  lastResult: ""
});

export class FineJobExecutorClient {
  private credentials: Credentials | null = null;
  private state = initialState();
  private startupPromise: Promise<void> | null = null;
  private heartbeatPromise: Promise<void> | null = null;
  private controlSocket: WebSocket | null = null;
  private controlReconnectTimer: number | null = null;
  private taskHeartbeatTimer: number | null = null;
  private latestSnapshot: BossReadOnlySnapshot | null = null;
  private lastPageReportKey = "";
  private dispatchingKey = "";
  private pendingResults: Record<string, MainWorldExecutionResult> = {};

  async start(): Promise<void> {
    if (this.startupPromise) return this.startupPromise;
    const startup = (async () => {
      const stored = await browser.storage.local.get([CREDENTIALS_KEY, PENDING_RESULTS_KEY]);
      this.credentials = (stored[CREDENTIALS_KEY] as Credentials | undefined) ?? null;
      this.pendingResults = (stored[PENDING_RESULTS_KEY] as Record<string, MainWorldExecutionResult> | undefined) ?? {};
      this.state.paired = this.credentials !== null;
      this.state.detail = this.credentials ? "正在连接FineJob" : "尚未与FineJob配对";
      if (this.credentials) {
        // 插件启动时只执行一次连接确认，并建立桌面端命令通道。
        await this.testHeartbeat().catch(() => undefined);
        this.connectControlChannel();
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
    this.state.detail = "配对成功；自动打招呼默认未授权";
    await this.testHeartbeat();
    this.connectControlChannel();
  }

  async testHeartbeat(): Promise<void> {
    if (!this.credentials) throw new Error("插件尚未与FineJob配对");
    if (this.heartbeatPromise) return this.heartbeatPromise;
    this.heartbeatPromise = (async () => {
      try {
        const snapshot = this.latestSnapshot;
        const heartbeat = await this.request<{
          executor: ExecutorRuntimeState["executor"];
          queue: { actions: FineJobQueueAction[]; failed_actions?: FineJobQueueAction[] };
        }>("/heartbeat", {
          method: "POST",
          body: JSON.stringify({
            protocol_version: PROTOCOL_VERSION,
            plugin_version: packageJson.version,
            capabilities: CAPABILITIES,
            // 该字段记录插件心跳是否已确认，不再依据当前BOSS页面快照判断。
            browser_connected: true,
            current_action_id: this.state.currentAction?.id ?? null,
            current_epoch: this.state.currentAction?.execution_epoch ?? null,
            page_kind: snapshot?.pageKind ?? "other",
            page_state: snapshot?.state ?? "waiting",
            logged_in: snapshot?.loggedIn ?? false,
            risk_state: snapshot && !snapshot.loggedIn ? "login" : "none"
          })
        });
        this.state.connected = true;
        this.state.paired = true;
        this.state.executor = heartbeat.executor;
        this.state.queue = heartbeat.queue.actions;
        this.state.failedQueue = heartbeat.queue.failed_actions ?? [];
        this.state.detail = "FineJob通信正常";
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
    let message: { type?: string; request_id?: string };
    try {
      message = JSON.parse(rawMessage) as { type?: string; request_id?: string };
    } catch {
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
    const socket = this.controlSocket;
    this.controlSocket = null;
    if (socket) socket.close();
    this.credentials = null;
    this.state = initialState();
    this.clearTaskHeartbeat();
    await browser.storage.local.remove(CREDENTIALS_KEY);
  }

  async control(command: "allow" | "pause" | "resume" | "emergency_stop"): Promise<void> {
    const snapshot = await this.request<{ executor: ExecutorRuntimeState["executor"] }>("/control", {
      method: "POST",
      body: JSON.stringify({ command })
    });
    if (snapshot.executor) this.state.executor = snapshot.executor;
    this.state.detail = command === "allow" || command === "resume"
      ? "自动打招呼已允许"
      : command === "emergency_stop" ? "已紧急停止" : "队列已暂停";
    if (command === "allow" || command === "resume") await this.startNextAction();
  }

  async returnToReview(actionId: string): Promise<void> {
    await this.request(`/actions/${encodeURIComponent(actionId)}/return-to-review`, {
      method: "POST",
      body: JSON.stringify({ reason: "用户在插件中退回待确认" })
    });
    this.state.currentAction = null;
    this.clearTaskHeartbeat();
    await this.startNextAction();
  }

  async retryFailedAction(actionId: string): Promise<void> {
    const response = await this.request<{ queue: { actions: FineJobQueueAction[]; failed_actions?: FineJobQueueAction[] } }>(
      `/actions/${encodeURIComponent(actionId)}/retry-failed`,
      { method: "POST" }
    );
    this.state.queue = response.queue.actions;
    this.state.failedQueue = response.queue.failed_actions ?? [];
    await this.startNextAction();
  }

  async cancelFailedAction(actionId: string): Promise<void> {
    const response = await this.request<{ queue: { actions: FineJobQueueAction[]; failed_actions?: FineJobQueueAction[] } }>(
      `/actions/${encodeURIComponent(actionId)}/cancel-failed`,
      { method: "POST" }
    );
    this.state.queue = response.queue.actions;
    this.state.failedQueue = response.queue.failed_actions ?? [];
  }

  async retryAllFailed(): Promise<void> {
    const response = await this.request<{ queue: { actions: FineJobQueueAction[]; failed_actions?: FineJobQueueAction[] } }>(
      "/failed-actions/retry-all",
      { method: "POST" }
    );
    this.state.queue = response.queue.actions;
    this.state.failedQueue = response.queue.failed_actions ?? [];
    await this.startNextAction();
  }

  async cancelAllFailed(): Promise<void> {
    const response = await this.request<{ queue: { actions: FineJobQueueAction[]; failed_actions?: FineJobQueueAction[] } }>(
      "/failed-actions/cancel-all",
      { method: "POST" }
    );
    this.state.queue = response.queue.actions;
    this.state.failedQueue = response.queue.failed_actions ?? [];
  }

  async reportSnapshot(snapshot: BossReadOnlySnapshot): Promise<void> {
    this.latestSnapshot = snapshot;
    const action = this.state.currentAction;
    if (!action || !this.canReportSnapshot(action)) {
      return;
    }
    const reportKey = `${action.id}:${action.execution_epoch}:${action.verification_state}:${snapshot.state}:${snapshot.job?.encryptJobId ?? ""}:${snapshot.job?.contacted ?? ""}:${snapshot.observedAt}`;
    if (reportKey === this.lastPageReportKey) return;
    this.lastPageReportKey = reportKey;

    try {
      const response = await this.request<{ action: FineJobQueueAction }>(
        `/actions/${encodeURIComponent(action.id)}/page-status`,
        {
          method: "POST",
          body: JSON.stringify({
            execution_epoch: action.execution_epoch,
            state: snapshot.state,
            logged_in: snapshot.loggedIn,
            page_kind: snapshot.pageKind,
            encrypt_job_id: snapshot.job?.encryptJobId ?? "",
            contacted: snapshot.job?.contacted ?? null,
            observed_at: snapshot.observedAt,
            reason: snapshot.reason
          })
        }
      );
      this.state.currentAction = response.action;
      this.replaceQueueAction(response.action);
      if (response.action.execution_state === "ready_to_dispatch") {
        await this.dispatch(response.action);
      } else if (!this.isActiveAction(response.action)) {
        this.state.currentAction = null;
      }
    } catch (error) {
      this.state.detail = (error as Error).message;
    }
  }

  async reportExecutionResult(result: MainWorldExecutionResult): Promise<void> {
    await this.persistPendingResult(result);
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

  private async startNextAction(): Promise<void> {
    if (!this.credentials) return;
    try {
      // 每项任务领取前先确认一次插件与FineJob的连接。
      await this.testHeartbeat();
      const executor = this.state.executor;
      if (
        executor?.permission_state !== "allowed"
        || executor.queue_state !== "running"
        || executor.risk_state !== "none"
      ) return;
      const claimed = await this.request<{ action: FineJobQueueAction | null }>("/actions/claim", {
        method: "POST"
      });
      if (claimed.action) {
        const previousVerificationState = this.state.currentAction?.verification_state;
        this.state.currentAction = claimed.action;
        this.replaceQueueAction(claimed.action);
        this.scheduleTaskHeartbeat(claimed.action);
        if (previousVerificationState !== claimed.action.verification_state) {
          this.lastPageReportKey = "";
        }
        if (claimed.action.execution_state === "ready_to_dispatch") {
          await this.dispatch(claimed.action);
        } else if (this.latestSnapshot) {
          this.lastPageReportKey = "";
          await this.reportSnapshot(this.latestSnapshot);
        }
      } else {
        this.clearTaskHeartbeat();
        this.state.currentAction = null;
      }
    } catch (error) {
      this.state.connected = false;
      this.state.detail = (error as Error).message || "FineJob连接失败";
    }
  }

  private async dispatch(action: FineJobQueueAction): Promise<void> {
    const key = `${action.id}:${action.execution_epoch}`;
    if (this.dispatchingKey === key) return;

    const tabs = await browser.tabs.query({ url: ["*://zhipin.com/*", "*://*.zhipin.com/*"] });
    const target = tabs.find((tab) => tab.id !== undefined && (tab.url ?? "").includes(action.encrypt_job_id));
    if (target?.id === undefined) {
      this.state.detail = "未找到与队列岗位一致的BOSS详情标签页";
      return;
    }

    this.dispatchingKey = key;
    const response = await this.request<{ action: FineJobQueueAction }>(
      `/actions/${encodeURIComponent(action.id)}/dispatch-started`,
      { method: "POST", body: JSON.stringify({ execution_epoch: action.execution_epoch }) }
    );
    this.state.currentAction = response.action;
    this.scheduleTaskHeartbeat(response.action);
    try {
      await browser.tabs.sendMessage(target.id, {
        type: COMMAND_MESSAGE,
        command: {
          type: "BOSS_DEFAULT_GREETING",
          actionId: action.id,
          executionEpoch: action.execution_epoch,
          encryptJobId: action.encrypt_job_id
        }
      });
    } catch (error) {
      await this.reportExecutionResult({
        actionId: action.id,
        executionEpoch: action.execution_epoch,
        outcome: "unknown",
        contacted: null,
        statusCode: "CONTENT_COMMAND_FAILED",
        message: `真实执行许可后无法调用页面执行器：${(error as Error).message}`,
        evidence: {}
      });
    }
  }

  private replaceQueueAction(action: FineJobQueueAction): void {
    const index = this.state.queue.findIndex((item) => item.id === action.id);
    if (index >= 0) this.state.queue[index] = action;
  }

  private canReportSnapshot(action: FineJobQueueAction): boolean {
    return ["waiting_page_ready", "page_verified", "ready_to_dispatch"].includes(action.execution_state);
  }

  private isActiveAction(action: FineJobQueueAction): boolean {
    return !["queued", "succeeded", "failed_before_dispatch", "failed_after_dispatch", "cancelled", "blocked", "unknown_after_dispatch", "request_accepted"].includes(action.execution_state);
  }

  private resultKey(result: MainWorldExecutionResult): string {
    return `${result.actionId}:${result.executionEpoch}`;
  }

  private async persistPendingResult(result: MainWorldExecutionResult): Promise<void> {
    this.pendingResults[this.resultKey(result)] = result;
    await browser.storage.local.set({ [PENDING_RESULTS_KEY]: this.pendingResults });
  }

  private async removePendingResult(result: MainWorldExecutionResult): Promise<void> {
    delete this.pendingResults[this.resultKey(result)];
    await browser.storage.local.set({ [PENDING_RESULTS_KEY]: this.pendingResults });
  }

  private async submitExecutionResult(result: MainWorldExecutionResult): Promise<void> {
    const response = await this.request<{ action: FineJobQueueAction }>(
      `/actions/${encodeURIComponent(result.actionId)}/complete`,
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
    this.state.lastResult = `${response.action.job_title}：${result.message}`;
    this.state.currentAction = this.isActiveAction(response.action) ? response.action : null;
    this.replaceQueueAction(response.action);
    this.dispatchingKey = "";
    if (!this.state.currentAction) {
      this.clearTaskHeartbeat();
      await this.startNextAction();
    }
  }

  private scheduleTaskHeartbeat(action: FineJobQueueAction): void {
    this.clearTaskHeartbeat();
    const actionKey = `${action.id}:${action.execution_epoch}`;
    // 任务长时间未回传时只补发一次心跳测试，不建立持续检查循环。
    this.taskHeartbeatTimer = globalThis.setTimeout(() => {
      if (`${this.state.currentAction?.id}:${this.state.currentAction?.execution_epoch}` !== actionKey) return;
      void this.testHeartbeat().catch(() => undefined);
    }, TASK_HEARTBEAT_TIMEOUT_MS) as unknown as number;
  }

  private clearTaskHeartbeat(): void {
    if (this.taskHeartbeatTimer === null) return;
    globalThis.clearTimeout(this.taskHeartbeatTimer);
    this.taskHeartbeatTimer = null;
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
