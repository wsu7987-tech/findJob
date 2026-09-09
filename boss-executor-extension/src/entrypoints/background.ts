import { defineProxy } from "comctx";

import { browser, defineBackground } from "#imports";

import {
  BACKGROUND_NAMESPACE,
  BackgroundService,
  ProvideBackgroundAdapter
} from "../message/background";
import { fineJobExecutorClient } from "../finejob/client";
import { bossChatCoordinator } from "../finejob/chat-coordinator";

const bossMatches = ["*://zhipin.com/*", "*://*.zhipin.com/*"];
const liveZpTokens = new Map<number, string>();

const readHeader = (headers: chrome.webRequest.HttpHeader[] | undefined, name: string): string => {
  const target = name.toLowerCase();
  return headers?.find((header) => header.name.toLowerCase() === target)?.value?.trim() ?? "";
};

const installLiveZpTokenCapture = (): void => {
  browser.webRequest.onBeforeSendHeaders.addListener(
    (details) => {
      // 简历交换请求不能反向覆盖已从 BOSS 页面正常接口采集的凭证。
      if (details.tabId < 0 || new URL(details.url).pathname === "/wapi/zpchat/exchange/request") return;
      const zpToken = readHeader(details.requestHeaders, "zp_token");
      if (!zpToken) return;
      liveZpTokens.set(details.tabId, zpToken);
      void browser.tabs.sendMessage(details.tabId, {
        type: "finejob:boss-chat:zp-token:v1",
        zpToken
      }).catch(() => undefined);
    },
    { urls: bossMatches },
    ["requestHeaders", "extraHeaders"]
  );
  browser.runtime.onMessage.addListener((message, sender) => {
    if (message?.type !== "finejob:boss-chat:get-zp-token:v1") return;
    return Promise.resolve({ zpToken: sender.tab?.id === undefined ? "" : liveZpTokens.get(sender.tab.id) ?? "" });
  });
};

export default defineBackground({
  main() {
    // Background 负责服务入口、执行凭证和 FineJob 任务列表。
    const [provideBackgroundService] = defineProxy(() => new BackgroundService(), {
      namespace: BACKGROUND_NAMESPACE
    });
    provideBackgroundService(new ProvideBackgroundAdapter());
    installLiveZpTokenCapture();
    void fineJobExecutorClient.start().catch((error) => {
      console.error("[FineJob BOSS 执行器] 后端通信启动失败", error);
    });
    void bossChatCoordinator.start().catch((error) => {
      console.error("[FineJob BOSS 执行器] 自动代聊协调器启动失败", error);
    });
  }
});
