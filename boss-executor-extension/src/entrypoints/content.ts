import { defineProxy } from "comctx";
import { reactive } from "vue";

import { defineContentScript, injectScript } from "#imports";

import { createFrameworkStatus, refreshFrameworkDetail } from "../executor/framework-mode";
import {
  BACKGROUND_NAMESPACE,
  type BackgroundService,
  InjectBackgroundAdapter
} from "../message/background";
import { CONTENT_NAMESPACE, ContentService } from "../message/content";
import { ScriptElementAdapter } from "../message/content-script-share";
import { mountStatusPanel } from "../ui/mount-status-panel";

const bossMatches = ["*://zhipin.com/*", "*://*.zhipin.com/*"];

export default defineContentScript({
  matches: bossMatches,
  runAt: "document_start",
  world: "ISOLATED",
  async main() {
    const status = reactive(createFrameworkStatus(window.location.pathname));
    const unmountPanel = mountStatusPanel(status);
    window.addEventListener("pagehide", unmountPanel, { once: true });

    // 适配 boss-helper 的两级 comctx 代理：MAIN → Content → Background。
    const [, injectBackgroundService] = defineProxy(() => ({}) as BackgroundService, {
      namespace: BACKGROUND_NAMESPACE
    });
    const background = injectBackgroundService(new InjectBackgroundAdapter());
    const contentService = new ContentService(background, status);
    const [provideContentService] = defineProxy(() => contentService, {
      namespace: CONTENT_NAMESPACE,
      heartbeatTimeout: 3000
    });

    await contentService.refreshBackground();

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
