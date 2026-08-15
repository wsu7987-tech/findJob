import { afterEach, describe, expect, it, vi } from "vitest";

const unregisterAllMock = vi.fn();
const registerMock = vi.fn();
const isRegisteredMock = vi.fn();
const consoleLogMock = vi.spyOn(console, "log").mockImplementation(() => {});

vi.mock("electron", () => ({
  globalShortcut: {
    register: registerMock,
    unregisterAll: unregisterAllMock,
    isRegistered: isRegisteredMock
  }
}));

describe("createShortcutController", () => {
  afterEach(() => {
    unregisterAllMock.mockReset();
    registerMock.mockReset();
    isRegisteredMock.mockReset();
    consoleLogMock.mockClear();
  });

  it("skips empty accelerators and still unregisters previous shortcuts", async () => {
    const { createShortcutController } = await import("./shortcuts");
    const debugLog = vi.fn();
    const controller = createShortcutController({
      debugLog,
      toggleQuickCaptureWindow: vi.fn(),
      startQuickCaptureScreenshot: vi.fn()
    });

    const result = controller.register({
      quickCaptureHotkey: "   ",
      quickCaptureScreenshotHotkey: "",
      closeToTray: true,
      quickCaptureAlwaysOnTop: true
    });

    expect(unregisterAllMock).toHaveBeenCalledTimes(1);
    expect(registerMock).not.toHaveBeenCalled();
    expect(result).toEqual({
      quickCaptureRegistered: false,
      screenshotRegistered: false
    });
    expect(debugLog).toHaveBeenCalledWith("shortcut skipped because accelerator is empty");
    expect(consoleLogMock).toHaveBeenCalledWith(
      "[shortcut] quick-capture not registered because accelerator is empty"
    );
  });

  it("returns false when Electron rejects an invalid accelerator", async () => {
    registerMock.mockImplementation(() => {
      throw new Error("Invalid accelerator");
    });

    const { createShortcutController } = await import("./shortcuts");
    const debugLog = vi.fn();
    const controller = createShortcutController({
      debugLog,
      toggleQuickCaptureWindow: vi.fn(),
      startQuickCaptureScreenshot: vi.fn()
    });

    const result = controller.register({
      quickCaptureHotkey: "CommandOrControl+Shift+BadKey",
      quickCaptureScreenshotHotkey: "CommandOrControl+Shift+4",
      closeToTray: true,
      quickCaptureAlwaysOnTop: true
    });

    expect(result.quickCaptureRegistered).toBe(false);
    expect(result.screenshotRegistered).toBe(false);
    expect(debugLog).toHaveBeenCalledWith(
      expect.stringContaining("shortcut registration failed accelerator=CommandOrControl+Shift+BadKey")
    );
    expect(consoleLogMock).toHaveBeenCalledWith(
      expect.stringContaining(
        "[shortcut] quick-capture register failed accelerator=CommandOrControl+Shift+BadKey"
      )
    );
  });

  it("logs registered accelerators and hit events before running callbacks", async () => {
    registerMock.mockReturnValue(true);
    isRegisteredMock.mockReturnValue(true);

    const { createShortcutController } = await import("./shortcuts");
    const debugLog = vi.fn();
    const toggleQuickCaptureWindow = vi.fn();
    const startQuickCaptureScreenshot = vi.fn();
    const controller = createShortcutController({
      debugLog,
      toggleQuickCaptureWindow,
      startQuickCaptureScreenshot
    });

    controller.register({
      quickCaptureHotkey: "Alt+Shift+Q",
      quickCaptureScreenshotHotkey: "Alt+Shift+A",
      closeToTray: true,
      quickCaptureAlwaysOnTop: true
    });

    expect(consoleLogMock).toHaveBeenCalledWith(
      "[shortcut] quick-capture register accelerator=Alt+Shift+Q registered=true isRegistered=true"
    );
    expect(consoleLogMock).toHaveBeenCalledWith(
      "[shortcut] quick-capture-screenshot register accelerator=Alt+Shift+A registered=true isRegistered=true"
    );

    const quickCaptureCallback = registerMock.mock.calls[0]?.[1];
    const screenshotCallback = registerMock.mock.calls[1]?.[1];

    quickCaptureCallback?.();
    screenshotCallback?.();

    expect(consoleLogMock).toHaveBeenCalledWith("[shortcut] hit quick-capture accelerator=Alt+Shift+Q");
    expect(consoleLogMock).toHaveBeenCalledWith(
      "[shortcut] hit quick-capture-screenshot accelerator=Alt+Shift+A"
    );
    expect(toggleQuickCaptureWindow).toHaveBeenCalledTimes(1);
    expect(startQuickCaptureScreenshot).toHaveBeenCalledTimes(1);
  });
});
