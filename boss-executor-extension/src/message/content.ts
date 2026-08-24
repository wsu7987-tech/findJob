import type { BackgroundService } from "./background";
import {
  isMainWorldStatus,
  refreshFrameworkDetail,
  type FrameworkStatus,
  type MainWorldStatus
} from "../executor/framework-mode";
import { isBossReadOnlySnapshot } from "../platform/boss/types";
import type {
  ChatIdentity,
  ChatObservedMessage,
  ChatSendExecutionResult,
  MainWorldCommand,
  MainWorldExecutionResult
} from "../finejob/types";

export const CONTENT_NAMESPACE = "fine-job:boss-executor:content:v1";

export class ContentService {
  private readonly mainWorldWaiters = new Set<() => void>();
  private readonly mainCommands: MainWorldCommand[] = [];
  private readonly tabId = typeof crypto.randomUUID === "function"
    ? crypto.randomUUID()
    : `tab-${Date.now()}-${Math.random().toString(16).slice(2)}`;

  constructor(
    private readonly background: BackgroundService,
    private readonly status: FrameworkStatus
  ) {}

  async refreshBackground(): Promise<void> {
    try {
      const health = await this.background.health();
      this.status.background =
        health.ok && !health.frameworkMode && health.realActionsEnabled ? "ready" : "error";
    } catch {
      this.status.background = "error";
    }
    refreshFrameworkDetail(this.status);
  }

  async reportMainWorldReady(value: unknown): Promise<{ accepted: true }> {
    if (!isMainWorldStatus(value)) {
      this.status.mainWorld = "error";
      refreshFrameworkDetail(this.status);
      throw new Error("Main World 状态载荷无效");
    }

    this.status.mainWorld = "ready";
    this.status.page = value.pathname;
    refreshFrameworkDetail(this.status);
    for (const resolve of this.mainWorldWaiters) resolve();
    this.mainWorldWaiters.clear();
    return { accepted: true };
  }

  async reportBossSnapshot(value: unknown): Promise<{ accepted: true }> {
    if (!isBossReadOnlySnapshot(value)) {
      this.status.bossProbe = "unavailable";
      this.status.bossSnapshot = null;
      throw new Error("BOSS 岗位识别载荷无效");
    }

    this.status.bossProbe = value.state;
    this.status.bossSnapshot = value;
    this.status.page = value.pathname;
    await this.background.reportBossSnapshot(value);
    return { accepted: true };
  }

  async enqueueMainCommand(command: MainWorldCommand): Promise<{ accepted: boolean }> {
    if (command.type === "BOSS_CHAT_SEND") {
      if (command.targetTabId !== this.tabId) return { accepted: false };
      if (!command.action?.id || command.action.execution_epoch < 1 || !command.action.text) {
        throw new Error("聊天发送命令载荷无效");
      }
    } else if (
      !command.actionId || command.executionEpoch < 1 || !command.encryptJobId
    ) throw new Error("执行命令载荷无效");
    const duplicate = this.mainCommands.some(
      (item) => item.type === command.type && (
        item.type === "BOSS_CHAT_SEND" && command.type === "BOSS_CHAT_SEND"
          ? item.action.id === command.action.id && item.action.execution_epoch === command.action.execution_epoch
          : item.type === "BOSS_DEFAULT_GREETING" && command.type === "BOSS_DEFAULT_GREETING"
            && item.actionId === command.actionId && item.executionEpoch === command.executionEpoch
      )
    );
    if (!duplicate) this.mainCommands.push(command);
    return { accepted: true };
  }

  async takeMainCommand(): Promise<MainWorldCommand | null> {
    return this.mainCommands.shift() ?? null;
  }

  async reportExecutionResult(result: MainWorldExecutionResult): Promise<{ accepted: true }> {
    await this.background.reportExecutionResult(result);
    return { accepted: true };
  }

  async reportChatIdentity(identity: ChatIdentity): Promise<{ accepted: true }> {
    if (!identity.accountUid || !identity.loggedIn) return { accepted: true };
    await this.background.reportChatTabHeartbeat({
      ...identity,
      tabId: this.tabId,
      visible: document.visibilityState === "visible"
    });
    return { accepted: true };
  }

  async isChatListeningEnabled(): Promise<boolean> {
    return this.background.isChatListeningEnabled();
  }

  async reportChatMessage(message: ChatObservedMessage): Promise<{ accepted: boolean }> {
    if (!message.eventId || !message.accountUid || !message.peerUid || !message.platformMessageId) {
      throw new Error("聊天观察消息载荷无效");
    }
    return this.background.reportChatMessage(this.tabId, message);
  }

  async reportChatSendResult(result: ChatSendExecutionResult): Promise<{ accepted: true }> {
    await this.background.reportChatSendResult(result);
    return { accepted: true };
  }

  async waitForMainWorldReady(timeoutMs = 3000): Promise<void> {
    if (this.status.mainWorld === "ready") return;

    await new Promise<void>((resolve, reject) => {
      const done = () => {
        window.clearTimeout(timeoutId);
        resolve();
      };
      const timeoutId = window.setTimeout(() => {
        this.mainWorldWaiters.delete(done);
        this.status.mainWorld = "error";
        refreshFrameworkDetail(this.status);
        reject(new Error("Main World 健康检查超时"));
      }, timeoutMs);
      this.mainWorldWaiters.add(done);
    });
  }
}

export type { MainWorldStatus };
