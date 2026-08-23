import { browser } from "#imports";

import packageJson from "../../package.json";
import type { BossReadOnlySnapshot } from "../platform/boss/types";
import type {
  ExecutorRuntimeState,
  FineJobQueueAction,
  MainWorldExecutionResult
} from "./types";

const API_ROOT = "http://127.0.0.1:8000/api/fine-job/boss-executor";
const CREDENTIALS_KEY = "finejobBossExecutorCredentialsV1";
const PENDING_RESULTS_KEY = "finejobBossExecutorPendingResultsV1";
const COMMAND_MESSAGE = "finejob:boss-executor:execute:v1";
const PROTOCOL_VERSION = "1.1";
const CAPABILITIES = ["default_greeting", "page_identity", "queue_control", "contact_verification_snapshot"];

type Credentials = { executorId: string; token: string };

const initialState = (): ExecutorRuntimeState => ({
  connected: false,
  paired: false,
  detail: "尚未与FineJob配对",
  executor: null,
  queue: [],
  currentAction: null,
  lastResult: ""
});

export class FineJobExecutorClient {
  private credentials: Credentials | null = null;
  private state = initialState();
  private timer: number | null = null;
  private tickRunning = false;
  private latestSnapshot: BossReadOnlySnapshot | null = null;
  private lastPageReportKey = "";
  private dispatchingKey = "";
  private pendingResults: Record<string, MainWorldExecutionResult> = {};

  async start(): Promise<void> {
    const stored = await browser.storage.local.get([CREDENTIALS_KEY, PENDING_RESULTS_KEY]);
    this.credentials = (stored[CREDENTIALS_KEY] as Credentials | undefined) ?? null;
    this.pendingResults = (stored[PENDING_RESULTS_KEY] as Record<string, MainWorldExecutionResult> | undefined) ?? {};
    this.state.paired = this.credentials !== null;
    this.state.detail = this.credentials ? "正在连接FineJob" : "尚未与FineJob配对";
    if (this.timer === null) {
      this.timer = globalThis.setInterval(() => void this.tick(), 1500) as unknown as number;
    }
    await this.tick();
  }

  getState(): ExecutorRuntimeState {
    return structuredClone(this.state);
  }

  async pair(code: string): Promise<void> {
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
    await this.tick();
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
  }

  async returnToReview(actionId: string): Promise<void> {
    await this.request(`/actions/${encodeURIComponent(actionId)}/return-to-review`, {
      method: "POST",
      body: JSON.stringify({ reason: "用户在插件中退回待确认" })
    });
    this.state.currentAction = null;
    await this.tick();
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

  private async tick(): Promise<void> {
    if (this.tickRunning || !this.credentials) return;
    this.tickRunning = true;
    try {
      const snapshot = this.latestSnapshot;
      const heartbeat = await this.request<{
        executor: ExecutorRuntimeState["executor"];
        queue: { actions: FineJobQueueAction[] };
      }>("/heartbeat", {
        method: "POST",
        body: JSON.stringify({
          protocol_version: PROTOCOL_VERSION,
          plugin_version: packageJson.version,
          capabilities: CAPABILITIES,
          browser_connected: Boolean(snapshot),
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
      this.state.detail = "FineJob通信正常";

      await this.flushPendingResults();

      if (
        heartbeat.executor?.permission_state === "allowed" &&
        heartbeat.executor.queue_state === "running" &&
        heartbeat.executor.risk_state === "none"
      ) {
        const claimed = await this.request<{ action: FineJobQueueAction | null }>("/actions/claim", {
          method: "POST"
        });
        if (claimed.action) {
          const previousVerificationState = this.state.currentAction?.verification_state;
          this.state.currentAction = claimed.action;
          this.replaceQueueAction(claimed.action);
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
          this.state.currentAction = null;
        }
      }
    } catch (error) {
      this.state.connected = false;
      this.state.detail = (error as Error).message || "FineJob连接失败";
    } finally {
      this.tickRunning = false;
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
    return ["waiting_page_ready", "page_verified", "ready_to_dispatch"].includes(action.execution_state)
      || (action.execution_state === "request_accepted" && action.verification_state === "waiting_snapshot");
  }

  private isActiveAction(action: FineJobQueueAction): boolean {
    if (["queued", "request_accepted", "succeeded", "failed_after_dispatch", "cancelled", "blocked", "unknown_after_dispatch"].includes(action.execution_state)) {
      return action.execution_state === "request_accepted"
        && ["waiting_refresh", "refreshing", "waiting_snapshot"].includes(action.verification_state);
    }
    return true;
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
}

export const fineJobExecutorClient = new FineJobExecutorClient();
