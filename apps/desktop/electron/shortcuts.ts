import { globalShortcut } from "electron";

import type { ShellConfig } from "./shell-config";

export interface ShortcutControllerOptions {
  debugLog: (message: string) => void;
  toggleQuickCaptureWindow: () => Promise<void>;
  startQuickCaptureScreenshot: () => Promise<void>;
}

const logShortcutConsole = (message: string) => {
  console.log(`[shortcut] ${message}`);
};

const getShortcutRegistrationState = (accelerator: string) =>
  typeof globalShortcut.isRegistered === "function"
    ? globalShortcut.isRegistered(accelerator)
    : "unknown";

const normalizeAccelerator = (value: string | null | undefined) => {
  if (typeof value !== "string") {
    return null;
  }

  const normalized = value.trim();
  return normalized.length > 0 ? normalized : null;
};

const registerShortcut = (
  accelerator: string | null,
  label: string,
  callback: () => void,
  debugLog: (message: string) => void
) => {
  if (!accelerator) {
    debugLog("shortcut skipped because accelerator is empty");
    logShortcutConsole(`${label} not registered because accelerator is empty`);
    return false;
  }

  try {
    const registered = globalShortcut.register(accelerator, callback);
    const isRegistered = getShortcutRegistrationState(accelerator);
    debugLog(
      `shortcut register attempt accelerator=${accelerator} registered=${registered} isRegistered=${isRegistered}`
    );
    logShortcutConsole(
      `${label} register accelerator=${accelerator} registered=${registered} isRegistered=${isRegistered}`
    );
    return registered;
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    debugLog(`shortcut registration failed accelerator=${accelerator} error=${message}`);
    logShortcutConsole(`${label} register failed accelerator=${accelerator} error=${message}`);
    return false;
  }
};

export const createShortcutController = (options: ShortcutControllerOptions) => ({
  register(config: ShellConfig) {
    globalShortcut.unregisterAll();
    const quickCaptureRegistered = registerShortcut(
      normalizeAccelerator(config.quickCaptureHotkey),
      "quick-capture",
      () => {
        logShortcutConsole(`hit quick-capture accelerator=${config.quickCaptureHotkey}`);
        options.debugLog("quick capture shortcut triggered");
        void options.toggleQuickCaptureWindow();
      },
      options.debugLog
    );
    const screenshotRegistered = registerShortcut(
      normalizeAccelerator(config.quickCaptureScreenshotHotkey),
      "quick-capture-screenshot",
      () => {
        logShortcutConsole(`hit quick-capture-screenshot accelerator=${config.quickCaptureScreenshotHotkey}`);
        options.debugLog("quick capture screenshot shortcut triggered");
        void options.startQuickCaptureScreenshot();
      },
      options.debugLog
    );
    options.debugLog(
      `shortcuts registered quickCapture=${quickCaptureRegistered} screenshot=${screenshotRegistered}`
    );
    return { quickCaptureRegistered, screenshotRegistered };
  },
  unregisterAll() {
    globalShortcut.unregisterAll();
  }
});
