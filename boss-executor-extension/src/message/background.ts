import type { Adapter, Message, OnMessage, SendMessage } from "comctx";

import { browser } from "#imports";

import { fineJobExecutorClient } from "../finejob/client";
import { bossChatCoordinator } from "../finejob/chat-coordinator";
import type {
  ChatObservedMessage,
  ChatSendExecutionResult,
  ChatTabHeartbeat,
  ExecutorRuntimeState,
  MainWorldExecutionResult
} from "../finejob/types";
import type { BossPageIdentity } from "../platform/boss/types";

export const BACKGROUND_NAMESPACE = "fine-job:boss-executor:background:v1";

export type BackgroundHealth = {
  ok: true;
  component: "background";
  frameworkMode: false;
  realActionsEnabled: true;
};

export class BackgroundService {
  async health(): Promise<BackgroundHealth> {
    return {
      ok: true,
      component: "background",
      frameworkMode: false,
      realActionsEnabled: true
    };
  }

  async getExecutorState(): Promise<ExecutorRuntimeState> {
    return {
      ...fineJobExecutorClient.getState(),
      chat: bossChatCoordinator.getStatus()
    };
  }

  async pair(code: string): Promise<{ accepted: true }> {
    await fineJobExecutorClient.pair(code);
    return { accepted: true };
  }

  async testHeartbeat(): Promise<{ accepted: true }> {
    await fineJobExecutorClient.testHeartbeat();
    return { accepted: true };
  }

  async disconnect(): Promise<{ accepted: true }> {
    await fineJobExecutorClient.disconnect();
    return { accepted: true };
  }

  async control(command: "start" | "pause"): Promise<{ accepted: true }> {
    await fineJobExecutorClient.control(command);
    return { accepted: true };
  }

  async reportExecutionResult(result: MainWorldExecutionResult): Promise<{ accepted: true }> {
    await fineJobExecutorClient.reportExecutionResult(result);
    return { accepted: true };
  }

  async reportBossPageIdentity(tabId: string, identity: BossPageIdentity): Promise<{ accepted: true }> {
    await fineJobExecutorClient.reportBossPageIdentity(tabId, identity);
    return { accepted: true };
  }

  async reportChatTabHeartbeat(heartbeat: ChatTabHeartbeat): Promise<{ isLeader: boolean; leaderEpoch: number }> {
    return bossChatCoordinator.reportTabHeartbeat(heartbeat);
  }

  async isChatListeningEnabled(): Promise<boolean> {
    return bossChatCoordinator.isListeningEnabled();
  }

  async reportChatMessage(tabId: string, message: ChatObservedMessage): Promise<{ accepted: boolean }> {
    return bossChatCoordinator.reportMessage(tabId, message);
  }

  async reportChatSendResult(result: ChatSendExecutionResult): Promise<{ accepted: true }> {
    await bossChatCoordinator.reportSendResult(result);
    return { accepted: true };
  }
}

type MessageMeta = {
  url: string;
  injector: "content";
};

// 根据发起页面把 comctx 响应送回对应标签页。
export class ProvideBackgroundAdapter implements Adapter<MessageMeta> {
  sendMessage: SendMessage<MessageMeta> = async (message) => {
    const tabs = await browser.tabs.query({ url: message.meta.url });
    await Promise.all(
      tabs.flatMap((tab) =>
        tab.id === undefined ? [] : [browser.tabs.sendMessage(tab.id, message)]
      )
    );
  };

  onMessage: OnMessage<MessageMeta> = (callback) => {
    const handler = (message?: Partial<Message<MessageMeta>>) => callback(message);
    browser.runtime.onMessage.addListener(handler);
    return () => browser.runtime.onMessage.removeListener(handler);
  };
}

// Content 通过扩展 runtime 访问 Background 服务。
export class InjectBackgroundAdapter implements Adapter<MessageMeta> {
  sendMessage: SendMessage<MessageMeta> = (message) => {
    void browser.runtime.sendMessage(browser.runtime.id, {
      ...message,
      meta: {
        url: document.location.href,
        injector: "content"
      }
    } satisfies Message<MessageMeta>);
  };

  onMessage: OnMessage<MessageMeta> = (callback) => {
    const handler = (message?: Partial<Message<MessageMeta>>) => callback(message);
    browser.runtime.onMessage.addListener(handler);
    return () => browser.runtime.onMessage.removeListener(handler);
  };
}
