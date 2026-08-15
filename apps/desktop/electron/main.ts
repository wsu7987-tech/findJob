import path from "node:path";
import { appendFileSync, writeFileSync } from "node:fs";
import { pathToFileURL } from "node:url";
import type { Event as ElectronEvent, OpenDialogOptions } from "electron";

import { registerQuickCaptureIpc } from "./quick-capture-ipc";
import { createQuitState } from "./quit-state";
import { loadShellConfig } from "./shell-config";
import { createScreenshotController } from "./screenshot";
import { createShortcutController } from "./shortcuts";
import { createTrayController } from "./tray";
import { createWindowManager } from "./windows";

declare const require: NodeRequire;

const debugLogPath = path.resolve(process.cwd(), "electron-startup.log");
const debugLog = (message: string) => {
  appendFileSync(debugLogPath, `${new Date().toISOString()} ${message}\n`, "utf8");
};

debugLog("main module loaded: before browser_init");

(process as typeof process & { activateUvLoop?: () => void }).activateUvLoop ??= () => {};
require("electron/js2c/browser_init");
debugLog("main module loaded: after browser_init");
const bootstrapKeepAlive = setInterval(() => {}, 1000);

const { app, BrowserWindow, dialog, ipcMain, shell } = require("electron/main") as typeof import("electron");
const isDev = Boolean(process.env.VITE_DEV_SERVER_URL);
const quitState = createQuitState();

const getAppRoot = () => app.getAppPath();

const getRendererUrl = () =>
  process.env.VITE_DEV_SERVER_URL ??
  pathToFileURL(path.resolve(getAppRoot(), "dist/index.html")).href;

const getPreloadPath = () => path.resolve(getAppRoot(), "dist-electron/preload.cjs");

let shellConfig = loadShellConfig();

const windowManager = createWindowManager({
  debugLog,
  getPreloadPath,
  getRendererUrl,
  getQuickCaptureHotkey: () => shellConfig.quickCaptureHotkey,
  getQuickCaptureScreenshotHotkey: () => shellConfig.quickCaptureScreenshotHotkey,
  isDev,
  shouldHideMainWindowOnClose: () => quitState.shouldHideMainWindowOnClose(shellConfig.closeToTray),
  shouldKeepQuickCaptureOnTop: () => shellConfig.quickCaptureAlwaysOnTop,
  toggleQuickCaptureWindow: async () => windowManager.toggleQuickCaptureWindow(),
  startQuickCaptureScreenshot: async () => screenshotController.startRegionScreenshot()
});

const screenshotController = createScreenshotController({
  debugLog,
  showQuickCaptureWindow: () => windowManager.showQuickCaptureWindow(),
  getQuickCaptureWebContents: () => windowManager.getQuickCaptureWebContents()
});

const shortcutController = createShortcutController({
  debugLog,
  toggleQuickCaptureWindow: () => windowManager.toggleQuickCaptureWindow(),
  startQuickCaptureScreenshot: async () => {
    await screenshotController.startRegionScreenshot();
  }
});

const trayController = createTrayController({
  app,
  showMainWindow: () => windowManager.showMainWindow(),
  showQuickCaptureWindow: () => windowManager.showQuickCaptureWindow(),
  startQuickCaptureScreenshot: async () => screenshotController.startRegionScreenshot(),
  debugLog
});

const reloadShellConfig = () => {
  shellConfig = loadShellConfig();
  return shortcutController.register(shellConfig);
};

const updateShellConfig = (
  updates: Partial<
    Pick<
      typeof shellConfig,
      "quickCaptureHotkey" | "quickCaptureScreenshotHotkey" | "closeToTray" | "quickCaptureAlwaysOnTop"
    >
  >
) => {
  shellConfig = {
    ...shellConfig,
    ...updates
  };
  return shortcutController.register(shellConfig);
};

app.whenReady().then(() => {
  debugLog("app.whenReady resolved");
  clearInterval(bootstrapKeepAlive);

  trayController.ensureTray();
  reloadShellConfig();
  registerQuickCaptureIpc({
    ipcMain,
    showQuickCaptureWindow: () => windowManager.showQuickCaptureWindow(),
    hideQuickCaptureWindow: () => windowManager.hideQuickCaptureWindow(),
    startQuickCaptureScreenshot: async () => screenshotController.startRegionScreenshot(),
    getMainWindowState: () => windowManager.getMainWindowState(),
    setMainWindowAlwaysOnTop: (alwaysOnTop: boolean) =>
      windowManager.setMainWindowAlwaysOnTop(alwaysOnTop),
    toggleMainWindowFullscreen: async () => windowManager.toggleMainWindowFullscreen(),
    getQuickCaptureWindowState: () => windowManager.getQuickCaptureWindowState(),
    setQuickCaptureAlwaysOnTop: (alwaysOnTop: boolean) =>
      windowManager.setQuickCaptureAlwaysOnTop(alwaysOnTop),
    reloadShellConfig,
    updateShellConfig
  });

  ipcMain.handle("dialog:pick-file", async (_event: ElectronEvent, dialogOptions?: OpenDialogOptions) => {
    const resolvedOptions: OpenDialogOptions = {
      title: dialogOptions?.title ?? "选择本地文件",
      properties: ["openFile"],
      filters:
        dialogOptions?.filters ?? [
          { name: "PDF 文件", extensions: ["pdf"] },
          { name: "Markdown 文件", extensions: ["md", "markdown"] },
          { name: "文本文件", extensions: ["txt"] }
        ]
    };

    const ownerWindow = windowManager.getMainWindow();
    const result = ownerWindow
      ? await dialog.showOpenDialog(ownerWindow, resolvedOptions)
      : await dialog.showOpenDialog(resolvedOptions);

    if (result.canceled || result.filePaths.length === 0) {
      return null;
    }

    return result.filePaths[0];
  });

  ipcMain.handle(
    "dialog:pick-directory",
    async (_event: ElectronEvent, dialogOptions?: Pick<OpenDialogOptions, "title">) => {
      const options: OpenDialogOptions = {
        title: dialogOptions?.title ?? "选择输出目录",
        properties: ["openDirectory", "createDirectory"]
      };

      const ownerWindow = windowManager.getMainWindow();
      const result = ownerWindow
        ? await dialog.showOpenDialog(ownerWindow, options)
        : await dialog.showOpenDialog(options);

      if (result.canceled || result.filePaths.length === 0) {
        return null;
      }

      return result.filePaths[0];
    }
  );

  ipcMain.handle(
    "file:save-text",
    async (
      _event: ElectronEvent,
      payload: {
        title?: string;
        defaultPath?: string;
        content: string;
        filters?: Array<{ name: string; extensions: string[] }>;
      }
    ) => {
      const options = {
        title: payload.title ?? "保存文本文件",
        defaultPath: payload.defaultPath,
        filters: payload.filters
      };

      const ownerWindow = windowManager.getMainWindow();
      const result = ownerWindow
        ? await dialog.showSaveDialog(ownerWindow, options)
        : await dialog.showSaveDialog(options);

      if (result.canceled || !result.filePath) {
        return null;
      }

      writeFileSync(result.filePath, payload.content, "utf8");
      return result.filePath;
    }
  );

  ipcMain.handle("shell:open-path", async (_event: ElectronEvent, targetPath: string) => {
    if (!targetPath) {
      return "Path is empty.";
    }
    return shell.openPath(targetPath);
  });

  ipcMain.handle("app:get-meta", () => ({
    backendOrigin: process.env.KNOWLEDGE_CURATOR_API_ORIGIN ?? "http://127.0.0.1:8000",
    isElectron: true,
    version: app.getVersion()
  }));

  void windowManager.ensureMainWindow();

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      void windowManager.ensureMainWindow();
    }
  });
});

app.on("ready", () => {
  debugLog("app ready event fired");
});

app.on("window-all-closed", () => {
  debugLog("window-all-closed");
  if (process.platform !== "darwin" && !shellConfig.closeToTray) {
    app.quit();
  }
});

app.on("before-quit", () => {
  debugLog("app before-quit");
  quitState.beginQuit();
});

app.on("quit", () => {
  debugLog("app quit");
  shortcutController.unregisterAll();
  trayController.destroyTray();
});
