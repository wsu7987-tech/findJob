import { defineUnlistedScript } from "#imports";

import type { MainWorldStatus } from "../../executor/framework-mode";
import { contentService, initContentService } from "../../message";
import { executeDefaultGreeting } from "../../platform/boss/default-greeting";
import { readBossPageIdentity } from "../../platform/boss/read-only-probe";
import {
  installBossChatObserver,
  readBossChatIdentity
} from "../../platform/boss/chat/observer";
import { bossChatSender } from "../../platform/boss/chat/sender";

const currentStatus = (): MainWorldStatus => ({
  component: "main-world",
  frameworkMode: true,
  hostname: window.location.hostname,
  pathname: window.location.pathname,
  readyState: document.readyState
});

export default defineUnlistedScript(async () => {
  try {
    // MAIN World平时只读取最小岗位身份；只有收到单个已授权命令时才读取token并执行一次平台动作。
    initContentService();
    await contentService.reportMainWorldReady(currentStatus());
    const chatObserver = installBossChatObserver(async (message) => {
      await contentService.reportChatMessage(message);
    });

    const syncChatObservationPermission = async () => {
      chatObserver.setEnabled(await contentService.isChatListeningEnabled());
    };
    await syncChatObservationPermission();

    const reportChatIdentity = async () => {
      await contentService.reportChatIdentity(readBossChatIdentity());
    };
    await reportChatIdentity();
    const chatIdentityTimer = window.setInterval(() => {
      void reportChatIdentity().catch(() => undefined);
    }, 5_000);

    let commandRunning = false;
    const commandTimer = window.setInterval(() => {
      if (commandRunning) return;
      commandRunning = true;
      void contentService.takeMainCommand().then(async (command) => {
        if (!command) return;
        if (command.type === "BOSS_PAGE_PROBE") {
          await contentService.reportBossPageIdentity(readBossPageIdentity());
        } else if (command.type === "BOSS_DEFAULT_GREETING") {
          const result = await executeDefaultGreeting(command);
          await contentService.reportExecutionResult(result);
        } else {
          const result = await bossChatSender.send(command.action);
          await contentService.reportChatSendResult(result);
        }
      }).catch((error) => {
        console.error("[FineJob BOSS 执行器] 默认招呼动作执行失败", error);
      }).finally(() => {
        commandRunning = false;
      });
    }, 250);
    window.addEventListener("pagehide", () => window.clearInterval(commandTimer), { once: true });
    window.addEventListener("pagehide", () => {
      window.clearInterval(chatIdentityTimer);
      chatObserver.uninstall();
    }, { once: true });
  } catch (error) {
    console.error("[FineJob BOSS 执行器] 无法向 Content 报告状态", error);
  }
});
