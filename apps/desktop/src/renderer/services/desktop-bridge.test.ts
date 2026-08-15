import { describe, expect, it, vi } from "vitest";

import {
  chooseDirectory,
  chooseFile,
  getMainWindowState,
  getQuickCaptureWindowState,
  hasDirectoryPicker,
  hasFilePicker,
  onQuickCaptureScreenshotImage,
  setMainWindowAlwaysOnTop,
  setQuickCaptureAlwaysOnTop,
  showQuickCaptureWindow,
  startQuickCaptureScreenshot,
  toggleMainWindowFullscreen
} from "./desktop-bridge";

describe("desktop bridge helpers", () => {
  it("detects file and directory picker support independently", () => {
    expect(hasFilePicker({})).toBe(false);
    expect(hasDirectoryPicker({})).toBe(false);

    expect(
      hasFilePicker({
        desktopBridge: {
          chooseFile: vi.fn()
        }
      })
    ).toBe(true);

    expect(
      hasDirectoryPicker({
        desktopBridge: {
          chooseDirectory: vi.fn()
        }
      })
    ).toBe(true);
  });

  it("delegates directory picking to the Electron bridge", async () => {
    const chooseDirectoryMock = vi.fn().mockResolvedValue("D:\\KnowledgeCurator\\outputs");

    await expect(
      chooseDirectory(
        {
          title: "选择输出目录"
        },
        {
          desktopBridge: {
            chooseDirectory: chooseDirectoryMock
          }
        }
      )
    ).resolves.toBe("D:\\KnowledgeCurator\\outputs");

    expect(chooseDirectoryMock).toHaveBeenCalledWith({
      title: "选择输出目录"
    });
  });

  it("returns null when file picking is unavailable", async () => {
    await expect(chooseFile({ title: "选择 PDF 文件" }, {})).resolves.toBeNull();
  });

  it("delegates quick capture shell actions to the Electron bridge", async () => {
    const showQuickCaptureWindowMock = vi.fn().mockResolvedValue(undefined);
    const startQuickCaptureScreenshotMock = vi.fn().mockResolvedValue(undefined);
    const getQuickCaptureWindowStateMock = vi.fn().mockResolvedValue({ alwaysOnTop: true });
    const setQuickCaptureAlwaysOnTopMock = vi.fn().mockResolvedValue({ alwaysOnTop: false });
    const getMainWindowStateMock = vi
      .fn()
      .mockResolvedValue({ alwaysOnTop: false, fullscreen: false });
    const setMainWindowAlwaysOnTopMock = vi.fn().mockResolvedValue({ alwaysOnTop: true });
    const toggleMainWindowFullscreenMock = vi.fn().mockResolvedValue({ fullscreen: true });
    const unsubscribeMock = vi.fn();
    const onQuickCaptureScreenshotImageMock = vi.fn().mockReturnValue(unsubscribeMock);

    await expect(
      showQuickCaptureWindow({
        desktopBridge: {
          showQuickCaptureWindow: showQuickCaptureWindowMock
        }
      })
    ).resolves.toBeUndefined();

    await expect(
      startQuickCaptureScreenshot({
        desktopBridge: {
          startQuickCaptureScreenshot: startQuickCaptureScreenshotMock
        }
      })
    ).resolves.toBeUndefined();

    const callback = vi.fn();
    const unsubscribe = onQuickCaptureScreenshotImage(callback, {
      desktopBridge: {
        onQuickCaptureScreenshotImage: onQuickCaptureScreenshotImageMock
      }
    });
    unsubscribe();

    await expect(
      getQuickCaptureWindowState({
        desktopBridge: {
          getQuickCaptureWindowState: getQuickCaptureWindowStateMock
        }
      })
    ).resolves.toEqual({ alwaysOnTop: true });

    await expect(
      setQuickCaptureAlwaysOnTop(false, {
        desktopBridge: {
          setQuickCaptureAlwaysOnTop: setQuickCaptureAlwaysOnTopMock
        }
      })
    ).resolves.toEqual({ alwaysOnTop: false });

    await expect(
      getMainWindowState({
        desktopBridge: {
          getMainWindowState: getMainWindowStateMock
        }
      })
    ).resolves.toEqual({ alwaysOnTop: false, fullscreen: false });

    await expect(
      setMainWindowAlwaysOnTop(true, {
        desktopBridge: {
          setMainWindowAlwaysOnTop: setMainWindowAlwaysOnTopMock
        }
      })
    ).resolves.toEqual({ alwaysOnTop: true });

    await expect(
      toggleMainWindowFullscreen({
        desktopBridge: {
          toggleMainWindowFullscreen: toggleMainWindowFullscreenMock
        }
      })
    ).resolves.toEqual({ fullscreen: true });

    expect(showQuickCaptureWindowMock).toHaveBeenCalledTimes(1);
    expect(startQuickCaptureScreenshotMock).toHaveBeenCalledTimes(1);
    expect(onQuickCaptureScreenshotImageMock).toHaveBeenCalledWith(callback);
    expect(unsubscribeMock).toHaveBeenCalledTimes(1);
    expect(getQuickCaptureWindowStateMock).toHaveBeenCalledTimes(1);
    expect(setQuickCaptureAlwaysOnTopMock).toHaveBeenCalledWith(false);
    expect(getMainWindowStateMock).toHaveBeenCalledTimes(1);
    expect(setMainWindowAlwaysOnTopMock).toHaveBeenCalledWith(true);
    expect(toggleMainWindowFullscreenMock).toHaveBeenCalledTimes(1);
  });
});
