import type { ContextBridge, IpcRenderer } from "electron";

declare const require: NodeRequire;

const { contextBridge, ipcRenderer } = require("electron/renderer") as {
  contextBridge: ContextBridge;
  ipcRenderer: IpcRenderer;
};

const desktopBridge = {
  chooseFile: (options?: { title?: string; filters?: Array<{ name: string; extensions: string[] }> }) =>
    ipcRenderer.invoke("dialog:pick-file", options) as Promise<string | null>,
  chooseDirectory: (options?: { title?: string }) =>
    ipcRenderer.invoke("dialog:pick-directory", options) as Promise<string | null>,
  saveTextFile: (options: {
    title?: string;
    defaultPath?: string;
    content: string;
    filters?: Array<{ name: string; extensions: string[] }>;
  }) => ipcRenderer.invoke("file:save-text", options) as Promise<string | null>,
  openPath: (targetPath: string) => ipcRenderer.invoke("shell:open-path", targetPath) as Promise<string>,
  showQuickCaptureWindow: () => ipcRenderer.invoke("quick-capture:show") as Promise<void>,
  hideQuickCaptureWindow: () => ipcRenderer.invoke("quick-capture:hide") as Promise<void>,
  startQuickCaptureScreenshot: () =>
    ipcRenderer.invoke("quick-capture:screenshot") as Promise<void>,
  onQuickCaptureScreenshotImage: (callback: (imageBase64: string) => void) => {
    const listener = (_event: unknown, imageBase64: string) => {
      callback(imageBase64);
    };
    ipcRenderer.on("quick-capture:screenshot-image", listener);
    return () => {
      ipcRenderer.removeListener("quick-capture:screenshot-image", listener);
    };
  },
  getMainWindowState: () =>
    ipcRenderer.invoke("main-window:state") as Promise<{
      alwaysOnTop: boolean;
      fullscreen: boolean;
    }>,
  setMainWindowAlwaysOnTop: (alwaysOnTop: boolean) =>
    ipcRenderer.invoke("main-window:set-always-on-top", alwaysOnTop) as Promise<{
      alwaysOnTop: boolean;
    }>,
  toggleMainWindowFullscreen: () =>
    ipcRenderer.invoke("main-window:toggle-fullscreen") as Promise<{
      fullscreen: boolean;
    }>,
  getQuickCaptureWindowState: () =>
    ipcRenderer.invoke("quick-capture:state") as Promise<{
      alwaysOnTop: boolean;
    }>,
  setQuickCaptureAlwaysOnTop: (alwaysOnTop: boolean) =>
    ipcRenderer.invoke("quick-capture:set-always-on-top", alwaysOnTop) as Promise<{
      alwaysOnTop: boolean;
    }>,
  refreshShellConfig: () =>
    ipcRenderer.invoke("shell:refresh-config") as Promise<{
      quickCaptureRegistered: boolean;
      screenshotRegistered: boolean;
    }>,
  updateShellConfig: (updates: {
    quickCaptureHotkey?: string;
    quickCaptureScreenshotHotkey?: string;
    closeToTray?: boolean;
    quickCaptureAlwaysOnTop?: boolean;
  }) =>
    ipcRenderer.invoke("shell:update-config", updates) as Promise<{
      quickCaptureRegistered: boolean;
      screenshotRegistered: boolean;
    }>,
  getMeta: () =>
    ipcRenderer.invoke("app:get-meta") as Promise<{
      backendOrigin: string;
      isElectron: boolean;
      version: string;
    }>,
  startCodex: (size?: { cols?: number; rows?: number }) =>
    ipcRenderer.invoke("codex:start", size) as Promise<{ status: string; runId: string | null }>,
  resumeCodex: (size?: { cols?: number; rows?: number }) =>
    ipcRenderer.invoke("codex:resume", size) as Promise<{ status: string; runId: string | null }>,
  getCodexState: () =>
    ipcRenderer.invoke("codex:state") as Promise<{ status: string; runId: string | null }>,
  writeCodex: (data: string) => ipcRenderer.send("codex:input", data),
  resizeCodex: (cols: number, rows: number) =>
    ipcRenderer.send("codex:resize", { cols, rows }),
  interruptCodex: () => ipcRenderer.send("codex:interrupt"),
  stopCodex: () => ipcRenderer.send("codex:stop"),
  onCodexOutput: (callback: (payload: { runId: string | null; data: string }) => void) => {
    const listener = (_event: unknown, payload: { runId: string | null; data: string }) => callback(payload);
    ipcRenderer.on("codex:output", listener);
    return () => ipcRenderer.removeListener("codex:output", listener);
  },
  onCodexStatus: (callback: (payload: { status: string; runId: string | null; message: string }) => void) => {
    const listener = (_event: unknown, payload: { status: string; runId: string | null; message: string }) => callback(payload);
    ipcRenderer.on("codex:status", listener);
    return () => ipcRenderer.removeListener("codex:status", listener);
  }
};

contextBridge.exposeInMainWorld("desktopBridge", desktopBridge);
