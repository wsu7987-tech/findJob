import type { App, Tray as ElectronTray } from "electron";

declare const require: NodeRequire;

const { Menu, Tray, nativeImage } = require("electron/main") as typeof import("electron");

export interface TrayControllerOptions {
  app: App;
  showMainWindow: () => Promise<void>;
  showQuickCaptureWindow: () => Promise<void>;
  startQuickCaptureScreenshot: () => Promise<void>;
  debugLog: (message: string) => void;
}

export const createTrayController = (options: TrayControllerOptions) => {
  let tray: ElectronTray | null = null;

  return {
    ensureTray() {
      if (tray) {
        return tray;
      }

      tray = new Tray(nativeImage.createEmpty());
      tray.setToolTip("Knowledge Curator");
      tray.setContextMenu(
        Menu.buildFromTemplate([
          { label: "打开主工作台", click: () => void options.showMainWindow() },
          { label: "打开快速采集窗", click: () => void options.showQuickCaptureWindow() },
          { label: "开始截图识别", click: () => void options.startQuickCaptureScreenshot() },
          { type: "separator" },
          { label: "退出程序", click: () => options.app.quit() }
        ])
      );
      tray.on("double-click", () => {
        void options.showMainWindow();
      });
      options.debugLog("tray initialized");
      return tray;
    },
    destroyTray() {
      tray?.destroy();
      tray = null;
    }
  };
};
