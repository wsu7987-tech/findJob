import { describe, expect, it, vi } from "vitest";

import { registerQuickCaptureIpc } from "./quick-capture-ipc";

describe("registerQuickCaptureIpc", () => {
  it("registers a shell:update-config handler that forwards the payload", async () => {
    const handlers = new Map<string, (...args: unknown[]) => unknown>();
    const ipcMain = {
      handle: vi.fn((channel: string, handler: (...args: unknown[]) => unknown) => {
        handlers.set(channel, handler);
      })
    };
    const updateShellConfig = vi.fn().mockReturnValue({
      quickCaptureRegistered: true,
      screenshotRegistered: true
    });

    registerQuickCaptureIpc({
      ipcMain: ipcMain as never,
      showQuickCaptureWindow: vi.fn(),
      hideQuickCaptureWindow: vi.fn(),
      startQuickCaptureScreenshot: vi.fn(),
      getMainWindowState: vi.fn().mockReturnValue({ alwaysOnTop: false, fullscreen: false }),
      setMainWindowAlwaysOnTop: vi.fn().mockReturnValue({ alwaysOnTop: false }),
      toggleMainWindowFullscreen: vi.fn(),
      getQuickCaptureWindowState: vi.fn().mockReturnValue({ alwaysOnTop: true }),
      setQuickCaptureAlwaysOnTop: vi.fn().mockReturnValue({ alwaysOnTop: true }),
      reloadShellConfig: vi.fn(),
      updateShellConfig
    });

    const handler = handlers.get("shell:update-config");

    expect(handler).toBeTypeOf("function");

    const payload = {
      quickCaptureHotkey: "CommandOrControl+Shift+W",
      quickCaptureScreenshotHotkey: "Alt+Shift+A",
      closeToTray: false,
      quickCaptureAlwaysOnTop: false
    };
    const result = await handler?.({}, payload);

    expect(updateShellConfig).toHaveBeenCalledWith(payload);
    expect(result).toEqual({
      quickCaptureRegistered: true,
      screenshotRegistered: true
    });
  });
});
