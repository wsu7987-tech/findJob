import fs from "node:fs";
import path from "node:path";

export interface ShellConfig {
  quickCaptureHotkey: string;
  quickCaptureScreenshotHotkey: string;
  closeToTray: boolean;
  quickCaptureAlwaysOnTop: boolean;
}

const DEFAULT_SHELL_CONFIG: ShellConfig = {
  quickCaptureHotkey: "CommandOrControl+Shift+Space",
  quickCaptureScreenshotHotkey: "CommandOrControl+Shift+4",
  closeToTray: true,
  quickCaptureAlwaysOnTop: true
};

const parseBoolean = (value: unknown, fallback: boolean) => {
  if (typeof value === "boolean") {
    return value;
  }
  return fallback;
};

export const loadShellConfig = (): ShellConfig => {
  const appDataDir =
    process.env.KNOWLEDGE_CURATOR_APP_DATA_DIR ?? path.resolve(process.cwd(), ".local/app-data");
  const configPath = path.resolve(appDataDir, "config.user.json");

  if (!fs.existsSync(configPath)) {
    return { ...DEFAULT_SHELL_CONFIG };
  }

  try {
    const parsed = JSON.parse(fs.readFileSync(configPath, "utf8")) as Record<string, unknown>;
    return {
      quickCaptureHotkey:
        typeof parsed.quick_capture_hotkey === "string" && parsed.quick_capture_hotkey.trim()
          ? parsed.quick_capture_hotkey.trim()
          : DEFAULT_SHELL_CONFIG.quickCaptureHotkey,
      quickCaptureScreenshotHotkey:
        typeof parsed.quick_capture_screenshot_hotkey === "string" &&
        parsed.quick_capture_screenshot_hotkey.trim()
          ? parsed.quick_capture_screenshot_hotkey.trim()
          : DEFAULT_SHELL_CONFIG.quickCaptureScreenshotHotkey,
      closeToTray: parseBoolean(parsed.close_to_tray, DEFAULT_SHELL_CONFIG.closeToTray),
      quickCaptureAlwaysOnTop: parseBoolean(
        parsed.quick_capture_always_on_top,
        DEFAULT_SHELL_CONFIG.quickCaptureAlwaysOnTop
      )
    };
  } catch {
    return { ...DEFAULT_SHELL_CONFIG };
  }
};
