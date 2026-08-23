import { defineUnlistedScript } from "#imports";

import type { MainWorldStatus } from "../../executor/framework-mode";
import { contentService, initContentService } from "../../message";
import { readBossPageSnapshot, snapshotFingerprint } from "../../platform/boss/read-only-probe";

const currentStatus = (): MainWorldStatus => ({
  component: "main-world",
  frameworkMode: true,
  hostname: window.location.hostname,
  pathname: window.location.pathname,
  readyState: document.readyState
});

export default defineUnlistedScript(async () => {
  try {
    // MAIN World 仅只读指定的岗位身份字段；不读取 Cookie/token，也不执行平台动作。
    initContentService();
    await contentService.reportMainWorldReady(currentStatus());

    let previousFingerprint = "";
    let reportRunning = false;
    const reportSnapshot = async () => {
      if (reportRunning) return;
      reportRunning = true;
      try {
        const snapshot = readBossPageSnapshot();
        const fingerprint = snapshotFingerprint(snapshot);
        if (fingerprint !== previousFingerprint) {
          await contentService.reportBossSnapshot(snapshot);
          previousFingerprint = fingerprint;
        }
      } finally {
        reportRunning = false;
      }
    };

    // 不 Hook BOSS Vue setter；仅按固定间隔读取快照，页面卸载时立即停止。
    await reportSnapshot();
    const probeTimer = window.setInterval(() => {
      void reportSnapshot().catch((error) => {
        console.error("[FineJob BOSS 执行器] 岗位只读识别失败", error);
      });
    }, 1000);
    window.addEventListener("pagehide", () => window.clearInterval(probeTimer), { once: true });
  } catch (error) {
    console.error("[FineJob BOSS 执行器] 无法向 Content 报告状态", error);
  }
});
