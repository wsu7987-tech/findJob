import type { IpcMain } from "electron";
import type { ShellConfig } from "./shell-config";

export interface QuickCaptureIpcOptions {
  ipcMain: IpcMain;
  showQuickCaptureWindow: () => Promise<void>;
  hideQuickCaptureWindow: () => void;
  startQuickCaptureScreenshot: () => Promise<void>;
  getMainWindowState: () => { alwaysOnTop: boolean; fullscreen: boolean };
  setMainWindowAlwaysOnTop: (alwaysOnTop: boolean) => { alwaysOnTop: boolean };
  toggleMainWindowFullscreen: () => Promise<{ fullscreen: boolean }>;
  getQuickCaptureWindowState: () => { alwaysOnTop: boolean };
  setQuickCaptureAlwaysOnTop: (alwaysOnTop: boolean) => { alwaysOnTop: boolean };
  reloadShellConfig: () => { quickCaptureRegistered: boolean; screenshotRegistered: boolean };
  updateShellConfig: (
    updates: Partial<Pick<ShellConfig, "quickCaptureHotkey" | "quickCaptureScreenshotHotkey" | "closeToTray" | "quickCaptureAlwaysOnTop">>
  ) => { quickCaptureRegistered: boolean; screenshotRegistered: boolean };
}

export const registerQuickCaptureIpc = (options: QuickCaptureIpcOptions) => {
  options.ipcMain.handle("quick-capture:show", async () => {
    await options.showQuickCaptureWindow();
  });
  options.ipcMain.handle("quick-capture:hide", async () => {
    options.hideQuickCaptureWindow();
  });
  options.ipcMain.handle("quick-capture:screenshot", async () => {
    await options.startQuickCaptureScreenshot();
  });
  options.ipcMain.handle("main-window:state", async () => {
    return options.getMainWindowState();
  });
  options.ipcMain.handle("main-window:set-always-on-top", async (_event, alwaysOnTop: boolean) => {
    return options.setMainWindowAlwaysOnTop(Boolean(alwaysOnTop));
  });
  options.ipcMain.handle("main-window:toggle-fullscreen", async () => {
    return options.toggleMainWindowFullscreen();
  });
  options.ipcMain.handle("quick-capture:state", async () => {
    return options.getQuickCaptureWindowState();
  });
  options.ipcMain.handle("quick-capture:set-always-on-top", async (_event, alwaysOnTop: boolean) => {
    return options.setQuickCaptureAlwaysOnTop(Boolean(alwaysOnTop));
  });
  options.ipcMain.handle("shell:refresh-config", async () => {
    return options.reloadShellConfig();
  });
  options.ipcMain.handle("shell:update-config", async (_event, updates) => {
    return options.updateShellConfig(updates);
  });
};
