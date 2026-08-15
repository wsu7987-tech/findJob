import type {
  BrowserWindow as BrowserWindowInstance,
  Event as ElectronEvent,
  Input,
  WebContents
} from "electron";

declare const require: NodeRequire;

const { BrowserWindow } = require("electron/main") as typeof import("electron");

export interface WindowManagerOptions {
  debugLog: (message: string) => void;
  getPreloadPath: () => string;
  getRendererUrl: () => string;
  getQuickCaptureHotkey: () => string | null;
  getQuickCaptureScreenshotHotkey: () => string | null;
  isDev: boolean;
  shouldHideMainWindowOnClose: () => boolean;
  shouldKeepQuickCaptureOnTop: () => boolean;
  toggleQuickCaptureWindow: () => Promise<void>;
  startQuickCaptureScreenshot: () => Promise<void>;
}

let mainWindow: BrowserWindowInstance | null = null;
let quickCaptureWindow: BrowserWindowInstance | null = null;
let mainWindowAlwaysOnTop = false;
let quickCaptureAlwaysOnTop = true;

const focusWindow = (windowInstance: BrowserWindowInstance | null) => {
  if (!windowInstance) {
    return;
  }
  if (windowInstance.isMinimized()) {
    windowInstance.restore();
  }
  windowInstance.show();
  windowInstance.focus();
};

const withQuickCaptureHash = (url: string) => {
  const [base] = url.split("#");
  return `${base}#/quick-capture`;
};

const normalizeKey = (input: Input) => {
  if (input.key === " ") {
    return "Space";
  }
  if (input.key?.startsWith("Arrow")) {
    return input.key.replace("Arrow", "");
  }
  if (input.code?.startsWith("Key") && input.code.length === 4) {
    return input.code.slice(3).toUpperCase();
  }
  if (input.code?.startsWith("Digit") && input.code.length === 6) {
    return input.code.slice(5);
  }
  if (input.key && input.key.length === 1) {
    return input.key.toUpperCase();
  }
  return input.key ?? "";
};

const matchesAccelerator = (accelerator: string | null | undefined, input: Input) => {
  if (!accelerator || input.type !== "keyDown") {
    return false;
  }

  const parts = accelerator
    .split("+")
    .map((part) => part.trim())
    .filter(Boolean);
  if (parts.length === 0) {
    return false;
  }

  const key = parts.at(-1);
  const modifiers = new Set(parts.slice(0, -1));
  const normalizedKey = normalizeKey(input);

  if (!key || normalizedKey !== key) {
    return false;
  }

  const needsCtrl = modifiers.has("CommandOrControl") || modifiers.has("Control") || modifiers.has("CmdOrCtrl");
  const needsAlt = modifiers.has("Alt") || modifiers.has("Option");
  const needsShift = modifiers.has("Shift");
  const needsMeta = modifiers.has("Super") || modifiers.has("Meta") || modifiers.has("Command");

  return (
    Boolean(input.control) === needsCtrl &&
    Boolean(input.alt) === needsAlt &&
    Boolean(input.shift) === needsShift &&
    Boolean(input.meta) === needsMeta
  );
};

const attachShortcutFallback = (
  windowInstance: BrowserWindowInstance,
  options: WindowManagerOptions,
  windowLabel: string
) => {
  windowInstance.webContents.on("before-input-event", (event, input) => {
    if (matchesAccelerator(options.getQuickCaptureHotkey(), input)) {
      event.preventDefault();
      options.debugLog(`window shortcut fallback triggered window=${windowLabel} action=quick-capture`);
      void options.toggleQuickCaptureWindow();
      return;
    }

    if (matchesAccelerator(options.getQuickCaptureScreenshotHotkey(), input)) {
      event.preventDefault();
      options.debugLog(`window shortcut fallback triggered window=${windowLabel} action=screenshot`);
      void options.startQuickCaptureScreenshot();
    }
  });
};

export const createWindowManager = (options: WindowManagerOptions) => {
  const createMainWindow = async () => {
    mainWindow = new BrowserWindow({
      width: 1440,
      height: 960,
      minWidth: 960,
      minHeight: 620,
      backgroundColor: "#0c111b",
      autoHideMenuBar: true,
      alwaysOnTop: mainWindowAlwaysOnTop,
      webPreferences: {
        preload: options.getPreloadPath(),
        contextIsolation: true,
        nodeIntegration: false
      }
    });

    mainWindow.webContents.on("preload-error", (_event, preloadPath, error) => {
      options.debugLog(`preload error path=${preloadPath} message=${error.message}`);
    });

    mainWindow.webContents.on("did-fail-load", (_event, errorCode, errorDescription, validatedURL) => {
      options.debugLog(
        `did-fail-load code=${errorCode} description=${errorDescription} url=${validatedURL}`
      );
    });

    mainWindow.on("close", (event: ElectronEvent) => {
      if (!options.shouldHideMainWindowOnClose()) {
        return;
      }
      event.preventDefault();
      mainWindow?.hide();
      options.debugLog("mainWindow hidden to tray");
    });
    attachShortcutFallback(mainWindow, options, "main");

    await mainWindow.loadURL(options.getRendererUrl());
    options.debugLog(`window loaded url=${options.getRendererUrl()}`);

    if (options.isDev) {
      mainWindow.webContents.openDevTools({ mode: "detach" });
    }

    return mainWindow;
  };

  const createQuickCaptureWindow = async () => {
    quickCaptureAlwaysOnTop = options.shouldKeepQuickCaptureOnTop();
    quickCaptureWindow = new BrowserWindow({
      width: 520,
      height: 760,
      minWidth: 420,
      minHeight: 560,
      show: false,
      autoHideMenuBar: true,
      skipTaskbar: true,
      alwaysOnTop: quickCaptureAlwaysOnTop,
      title: "快速采集",
      webPreferences: {
        preload: options.getPreloadPath(),
        contextIsolation: true,
        nodeIntegration: false
      }
    });

    quickCaptureWindow.on("close", (event: ElectronEvent) => {
      event.preventDefault();
      quickCaptureWindow?.hide();
    });
    attachShortcutFallback(quickCaptureWindow, options, "quick-capture");

    await quickCaptureWindow.loadURL(withQuickCaptureHash(options.getRendererUrl()));
    options.debugLog(`quick capture window loaded url=${withQuickCaptureHash(options.getRendererUrl())}`);
    return quickCaptureWindow;
  };

  return {
    async ensureMainWindow() {
      if (!mainWindow || mainWindow.isDestroyed()) {
        return createMainWindow();
      }
      return mainWindow;
    },
    async ensureQuickCaptureWindow() {
      if (!quickCaptureWindow || quickCaptureWindow.isDestroyed()) {
        return createQuickCaptureWindow();
      }
      quickCaptureWindow.setAlwaysOnTop(quickCaptureAlwaysOnTop);
      return quickCaptureWindow;
    },
    getMainWindow: () => mainWindow,
    getQuickCaptureWindow: () => quickCaptureWindow,
    getQuickCaptureWebContents: (): WebContents | null => quickCaptureWindow?.webContents ?? null,
    getMainWindowState() {
      return {
        alwaysOnTop: mainWindow?.isAlwaysOnTop() ?? mainWindowAlwaysOnTop,
        fullscreen: mainWindow?.isFullScreen() ?? false
      };
    },
    getQuickCaptureWindowState() {
      return {
        alwaysOnTop: quickCaptureWindow?.isAlwaysOnTop() ?? quickCaptureAlwaysOnTop
      };
    },
    async showMainWindow() {
      focusWindow(await this.ensureMainWindow());
    },
    setMainWindowAlwaysOnTop(alwaysOnTop: boolean) {
      mainWindowAlwaysOnTop = alwaysOnTop;
      if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.setAlwaysOnTop(alwaysOnTop);
      }
      return {
        alwaysOnTop: mainWindow?.isAlwaysOnTop() ?? alwaysOnTop
      };
    },
    async toggleMainWindowFullscreen() {
      const windowInstance = await this.ensureMainWindow();
      const fullscreen = !windowInstance.isFullScreen();
      windowInstance.setFullScreen(fullscreen);
      if (!fullscreen) {
        focusWindow(windowInstance);
      }
      return {
        fullscreen: windowInstance.isFullScreen()
      };
    },
    async showQuickCaptureWindow() {
      focusWindow(await this.ensureQuickCaptureWindow());
    },
    async toggleQuickCaptureWindow() {
      const windowInstance = await this.ensureQuickCaptureWindow();
      if (windowInstance.isVisible()) {
        windowInstance.hide();
        return;
      }
      focusWindow(windowInstance);
    },
    hideQuickCaptureWindow() {
      quickCaptureWindow?.hide();
    },
    setQuickCaptureAlwaysOnTop(alwaysOnTop: boolean) {
      quickCaptureAlwaysOnTop = alwaysOnTop;
      if (quickCaptureWindow && !quickCaptureWindow.isDestroyed()) {
        quickCaptureWindow.setAlwaysOnTop(alwaysOnTop);
      }
      return {
        alwaysOnTop: quickCaptureWindow?.isAlwaysOnTop() ?? alwaysOnTop
      };
    }
  };
};
