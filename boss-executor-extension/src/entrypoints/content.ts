import { defineProxy } from "comctx";
import { reactive } from "vue";

import { browser, defineContentScript, injectScript } from "#imports";

import { createFrameworkStatus, refreshFrameworkDetail } from "../executor/framework-mode";
import {
  BACKGROUND_NAMESPACE,
  type BackgroundService,
  InjectBackgroundAdapter
} from "../message/background";
import { CONTENT_NAMESPACE, ContentService } from "../message/content";
import { ScriptElementAdapter } from "../message/content-script-share";
import { mountStatusPanel } from "../ui/mount-status-panel";
import type { MainWorldCommand } from "../finejob/types";

const bossMatches = ["*://zhipin.com/*", "*://*.zhipin.com/*"];

export default defineContentScript({
  matches: bossMatches,
  runAt: "document_start",
  world: "ISOLATED",
  async main() {
    const status = reactive(createFrameworkStatus(window.location.pathname));

    // 通过两级 comctx 代理连接 MAIN、Content 与 Background。
    const [, injectBackgroundService] = defineProxy(() => ({}) as BackgroundService, {
      namespace: BACKGROUND_NAMESPACE
    });
    const background = injectBackgroundService(new InjectBackgroundAdapter());
    const contentService = new ContentService(background, status);
    const controller = {
      pair: async (code: string) => { await background.pair(code); },
      testHeartbeat: async () => { await background.testHeartbeat(); },
      disconnect: async () => { await background.disconnect(); },
      control: async (command: "allow" | "pause" | "resume" | "emergency_stop") => {
        await background.control(command);
      },
      returnToReview: async (actionId: string) => { await background.returnToReview(actionId); },
      retryFailedAction: async (actionId: string) => { await background.retryFailedAction(actionId); },
      cancelFailedAction: async (actionId: string) => { await background.cancelFailedAction(actionId); },
      retryAllFailed: async () => { await background.retryAllFailed(); },
      cancelAllFailed: async () => { await background.cancelAllFailed(); }
    };
    const unmountPanel = mountStatusPanel(status, controller);
    window.addEventListener("pagehide", unmountPanel, { once: true });
    const [provideContentService] = defineProxy(() => contentService, {
      namespace: CONTENT_NAMESPACE,
      heartbeatTimeout: 3000
    });

    await contentService.refreshBackground();

    const runtimeHandler = (message?: { type?: string; command?: MainWorldCommand }) => {
      if (![
        "finejob:boss-executor:execute:v1",
        "finejob:boss-chat:execute:v1"
      ].includes(message?.type ?? "") || !message?.command) return;
      return contentService.enqueueMainCommand(message.command);
    };
    browser.runtime.onMessage.addListener(runtimeHandler);
    window.addEventListener("pagehide", () => browser.runtime.onMessage.removeListener(runtimeHandler), { once: true });

    const executorTimer = window.setInterval(() => {
      void background.getExecutorState().then((executorState) => {
        status.executor = executorState;
        refreshFrameworkDetail(status);
      }).catch(() => {
        status.executor.connected = false;
      });
    }, 1000);
    window.addEventListener("pagehide", () => window.clearInterval(executorTimer), { once: true });

    try {
      await injectScript("/boss.js", {
        keepInDom: true,
        modifyScript(script) {
          provideContentService(new ScriptElementAdapter(script));
        }
      });
      await contentService.waitForMainWorldReady();
    } catch (error) {
      status.mainWorld = "error";
      refreshFrameworkDetail(status);
      console.error("[FineJob BOSS 执行器] MAIN World 初始化失败", error);
    }
  }
});
