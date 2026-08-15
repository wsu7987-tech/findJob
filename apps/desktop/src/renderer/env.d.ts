/// <reference types="vite/client" />

declare global {
  interface Window {
    desktopBridge?: {
      chooseFile: (options?: {
        title?: string;
        filters?: Array<{ name: string; extensions: string[] }>;
      }) => Promise<string | null>;
      chooseDirectory: (options?: {
        title?: string;
      }) => Promise<string | null>;
      saveTextFile: (options: {
        title?: string;
        defaultPath?: string;
        content: string;
        filters?: Array<{ name: string; extensions: string[] }>;
      }) => Promise<string | null>;
      openPath: (targetPath: string) => Promise<string>;
      showQuickCaptureWindow: () => Promise<void>;
      hideQuickCaptureWindow: () => Promise<void>;
      startQuickCaptureScreenshot: () => Promise<void>;
      onQuickCaptureScreenshotImage: (
        callback: (imageBase64: string) => void
      ) => () => void;
      getMainWindowState: () => Promise<{
        alwaysOnTop: boolean;
        fullscreen: boolean;
      }>;
      setMainWindowAlwaysOnTop: (alwaysOnTop: boolean) => Promise<{
        alwaysOnTop: boolean;
      }>;
      toggleMainWindowFullscreen: () => Promise<{
        fullscreen: boolean;
      }>;
      getQuickCaptureWindowState: () => Promise<{
        alwaysOnTop: boolean;
      }>;
      setQuickCaptureAlwaysOnTop: (alwaysOnTop: boolean) => Promise<{
        alwaysOnTop: boolean;
      }>;
      refreshShellConfig: () => Promise<{
        quickCaptureRegistered: boolean;
        screenshotRegistered: boolean;
      }>;
      updateShellConfig: (updates: {
        quickCaptureHotkey?: string;
        quickCaptureScreenshotHotkey?: string;
        closeToTray?: boolean;
        quickCaptureAlwaysOnTop?: boolean;
      }) => Promise<{
        quickCaptureRegistered: boolean;
        screenshotRegistered: boolean;
      }>;
      getMeta: () => Promise<{
        backendOrigin: string;
        isElectron: boolean;
        version: string;
      }>;
    };
  }
}

export {};
