import { screen } from "electron";
import type { BrowserWindow as BrowserWindowInstance, WebContents } from "electron";

declare const require: NodeRequire;

const { BrowserWindow, desktopCapturer, ipcMain } = require("electron") as typeof import("electron");

const OVERLAY_CHANNEL_SELECTION = "quick-capture:overlay-selection";
const OVERLAY_CHANNEL_CANCEL = "quick-capture:overlay-cancel";

const buildOverlayHtml = () => `
<!doctype html>
<html>
  <body style="margin:0;cursor:crosshair;background:rgba(15,18,25,0.22);overflow:hidden;">
    <div id="selection" style="position:fixed;border:2px solid #4ade80;background:rgba(74,222,128,0.18);display:none;"></div>
    <script>
      const { ipcRenderer } = require("electron");
      const selection = document.getElementById("selection");
      let startX = 0;
      let startY = 0;
      let dragging = false;

      const render = (x, y, width, height) => {
        selection.style.display = "block";
        selection.style.left = x + "px";
        selection.style.top = y + "px";
        selection.style.width = width + "px";
        selection.style.height = height + "px";
      };

      window.addEventListener("mousedown", (event) => {
        dragging = true;
        startX = event.clientX;
        startY = event.clientY;
        render(startX, startY, 0, 0);
      });

      window.addEventListener("mousemove", (event) => {
        if (!dragging) return;
        const x = Math.min(startX, event.clientX);
        const y = Math.min(startY, event.clientY);
        const width = Math.abs(event.clientX - startX);
        const height = Math.abs(event.clientY - startY);
        render(x, y, width, height);
      });

      window.addEventListener("mouseup", (event) => {
        if (!dragging) return;
        dragging = false;
        const x = Math.min(startX, event.clientX);
        const y = Math.min(startY, event.clientY);
        const width = Math.abs(event.clientX - startX);
        const height = Math.abs(event.clientY - startY);
        if (width < 4 || height < 4) {
          ipcRenderer.send("${OVERLAY_CHANNEL_CANCEL}");
          return;
        }
        ipcRenderer.send("${OVERLAY_CHANNEL_SELECTION}", { x, y, width, height });
      });

      window.addEventListener("keydown", (event) => {
        if (event.key === "Escape") {
          ipcRenderer.send("${OVERLAY_CHANNEL_CANCEL}");
        }
      });
    </script>
  </body>
</html>`;

const waitForOverlaySelection = (overlayWindow: BrowserWindowInstance) =>
  new Promise<{ x: number; y: number; width: number; height: number } | null>((resolve) => {
    const cleanup = () => {
      ipcMain.removeListener(OVERLAY_CHANNEL_SELECTION, handleSelection);
      ipcMain.removeListener(OVERLAY_CHANNEL_CANCEL, handleCancel);
      if (!overlayWindow.isDestroyed()) {
        overlayWindow.close();
      }
    };

    const handleSelection = (_event: unknown, payload: { x: number; y: number; width: number; height: number }) => {
      cleanup();
      resolve(payload);
    };

    const handleCancel = () => {
      cleanup();
      resolve(null);
    };

    ipcMain.once(OVERLAY_CHANNEL_SELECTION, handleSelection);
    ipcMain.once(OVERLAY_CHANNEL_CANCEL, handleCancel);
  });

const captureSelectionAsBase64 = async (
  selection: { x: number; y: number; width: number; height: number },
  displayId: number
) => {
  const display = screen.getAllDisplays().find((item) => item.id === displayId) ?? screen.getPrimaryDisplay();
  const source = (
    await desktopCapturer.getSources({
      types: ["screen"],
      thumbnailSize: {
        width: Math.max(1, Math.round(display.size.width * display.scaleFactor)),
        height: Math.max(1, Math.round(display.size.height * display.scaleFactor))
      }
    })
  ).find((item) => item.display_id === String(display.id));

  if (!source) {
    return null;
  }

  const scale = display.scaleFactor;
  const image = source.thumbnail.crop({
    x: Math.round(selection.x * scale),
    y: Math.round(selection.y * scale),
    width: Math.round(selection.width * scale),
    height: Math.round(selection.height * scale)
  });
  const dataUrl = image.toDataURL();
  const [, base64 = ""] = dataUrl.split(",", 2);
  return base64 || null;
};

export interface ScreenshotFlowOptions {
  debugLog: (message: string) => void;
  showQuickCaptureWindow: () => Promise<void>;
  getQuickCaptureWebContents: () => WebContents | null;
}

export const createScreenshotController = (options: ScreenshotFlowOptions) => ({
  async startRegionScreenshot() {
    const display = screen.getPrimaryDisplay();
    const overlayWindow = new BrowserWindow({
      x: display.bounds.x,
      y: display.bounds.y,
      width: display.bounds.width,
      height: display.bounds.height,
      frame: false,
      transparent: true,
      movable: false,
      resizable: false,
      fullscreen: false,
      skipTaskbar: true,
      alwaysOnTop: true,
      focusable: true,
      hasShadow: false,
      webPreferences: {
        nodeIntegration: true,
        contextIsolation: false
      }
    });

    await overlayWindow.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(buildOverlayHtml())}`);
    overlayWindow.show();
    overlayWindow.focus();

    const selection = await waitForOverlaySelection(overlayWindow);
    if (!selection) {
      options.debugLog("region screenshot cancelled");
      return;
    }

    const imageBase64 = await captureSelectionAsBase64(selection, display.id);
    if (!imageBase64) {
      options.debugLog("region screenshot source not found");
      return;
    }

    await options.showQuickCaptureWindow();
    options.getQuickCaptureWebContents()?.send("quick-capture:screenshot-image", imageBase64);
    options.debugLog("region screenshot captured and forwarded");
  }
});
