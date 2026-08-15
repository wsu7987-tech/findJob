type FilePickerOptions = {
  title?: string;
  filters?: Array<{ name: string; extensions: string[] }>;
};

type DirectoryPickerOptions = {
  title?: string;
};

type SaveTextFileOptions = {
  title?: string;
  defaultPath?: string;
  content: string;
  filters?: Array<{ name: string; extensions: string[] }>;
};

type DesktopBridgeShape = {
  chooseFile?: (options?: FilePickerOptions) => Promise<string | null>;
  chooseDirectory?: (options?: DirectoryPickerOptions) => Promise<string | null>;
  saveTextFile?: (options: SaveTextFileOptions) => Promise<string | null>;
  openPath?: (targetPath: string) => Promise<string>;
  showQuickCaptureWindow?: () => Promise<void>;
  hideQuickCaptureWindow?: () => Promise<void>;
  startQuickCaptureScreenshot?: () => Promise<void>;
  onQuickCaptureScreenshotImage?: (callback: (imageBase64: string) => void) => () => void;
  getMainWindowState?: () => Promise<{ alwaysOnTop: boolean; fullscreen: boolean }>;
  setMainWindowAlwaysOnTop?: (alwaysOnTop: boolean) => Promise<{ alwaysOnTop: boolean }>;
  toggleMainWindowFullscreen?: () => Promise<{ fullscreen: boolean }>;
  getQuickCaptureWindowState?: () => Promise<{ alwaysOnTop: boolean }>;
  setQuickCaptureAlwaysOnTop?: (alwaysOnTop: boolean) => Promise<{ alwaysOnTop: boolean }>;
  refreshShellConfig?: () => Promise<{
    quickCaptureRegistered: boolean;
    screenshotRegistered: boolean;
  }>;
  updateShellConfig?: (updates: {
    quickCaptureHotkey?: string;
    quickCaptureScreenshotHotkey?: string;
    closeToTray?: boolean;
    quickCaptureAlwaysOnTop?: boolean;
  }) => Promise<{
    quickCaptureRegistered: boolean;
    screenshotRegistered: boolean;
  }>;
  getMeta?: () => Promise<{
    backendOrigin: string;
    isElectron: boolean;
    version: string;
  }>;
};

type WindowLike = {
  desktopBridge?: DesktopBridgeShape;
};

const getDesktopBridge = (windowLike: WindowLike = window) => windowLike.desktopBridge ?? null;

export const hasFilePicker = (windowLike: WindowLike = window) =>
  typeof getDesktopBridge(windowLike)?.chooseFile === "function";

export const hasDirectoryPicker = (windowLike: WindowLike = window) =>
  typeof getDesktopBridge(windowLike)?.chooseDirectory === "function";

export const chooseFile = async (
  options?: FilePickerOptions,
  windowLike: WindowLike = window
) => {
  const bridge = getDesktopBridge(windowLike);
  if (!bridge?.chooseFile) {
    return null;
  }

  return bridge.chooseFile(options);
};

export const chooseDirectory = async (
  options?: DirectoryPickerOptions,
  windowLike: WindowLike = window
) => {
  const bridge = getDesktopBridge(windowLike);
  if (!bridge?.chooseDirectory) {
    return null;
  }

  return bridge.chooseDirectory(options);
};

export const saveTextFile = async (
  options: SaveTextFileOptions,
  windowLike: WindowLike = window
) => {
  const bridge = getDesktopBridge(windowLike);
  if (!bridge?.saveTextFile) {
    return null;
  }

  return bridge.saveTextFile(options);
};

export const openPath = async (targetPath: string, windowLike: WindowLike = window) => {
  const bridge = getDesktopBridge(windowLike);
  if (!bridge?.openPath) {
    return "Desktop bridge unavailable.";
  }

  return bridge.openPath(targetPath);
};

export const showQuickCaptureWindow = async (windowLike: WindowLike = window) => {
  const bridge = getDesktopBridge(windowLike);
  if (!bridge?.showQuickCaptureWindow) {
    return;
  }

  return bridge.showQuickCaptureWindow();
};

export const hideQuickCaptureWindow = async (windowLike: WindowLike = window) => {
  const bridge = getDesktopBridge(windowLike);
  if (!bridge?.hideQuickCaptureWindow) {
    return;
  }

  return bridge.hideQuickCaptureWindow();
};

export const startQuickCaptureScreenshot = async (windowLike: WindowLike = window) => {
  const bridge = getDesktopBridge(windowLike);
  if (!bridge?.startQuickCaptureScreenshot) {
    return;
  }

  return bridge.startQuickCaptureScreenshot();
};

export const onQuickCaptureScreenshotImage = (
  callback: (imageBase64: string) => void,
  windowLike: WindowLike = window
) => {
  const bridge = getDesktopBridge(windowLike);
  if (!bridge?.onQuickCaptureScreenshotImage) {
    return () => {};
  }

  return bridge.onQuickCaptureScreenshotImage(callback);
};

export const getMainWindowState = async (windowLike: WindowLike = window) => {
  const bridge = getDesktopBridge(windowLike);
  if (!bridge?.getMainWindowState) {
    return null;
  }

  return bridge.getMainWindowState();
};

export const setMainWindowAlwaysOnTop = async (
  alwaysOnTop: boolean,
  windowLike: WindowLike = window
) => {
  const bridge = getDesktopBridge(windowLike);
  if (!bridge?.setMainWindowAlwaysOnTop) {
    return null;
  }

  return bridge.setMainWindowAlwaysOnTop(alwaysOnTop);
};

export const toggleMainWindowFullscreen = async (windowLike: WindowLike = window) => {
  const bridge = getDesktopBridge(windowLike);
  if (!bridge?.toggleMainWindowFullscreen) {
    return null;
  }

  return bridge.toggleMainWindowFullscreen();
};

export const getQuickCaptureWindowState = async (windowLike: WindowLike = window) => {
  const bridge = getDesktopBridge(windowLike);
  if (!bridge?.getQuickCaptureWindowState) {
    return null;
  }

  return bridge.getQuickCaptureWindowState();
};

export const setQuickCaptureAlwaysOnTop = async (
  alwaysOnTop: boolean,
  windowLike: WindowLike = window
) => {
  const bridge = getDesktopBridge(windowLike);
  if (!bridge?.setQuickCaptureAlwaysOnTop) {
    return null;
  }

  return bridge.setQuickCaptureAlwaysOnTop(alwaysOnTop);
};

export const refreshShellConfig = async (windowLike: WindowLike = window) => {
  const bridge = getDesktopBridge(windowLike);
  if (!bridge?.refreshShellConfig) {
    return null;
  }

  return bridge.refreshShellConfig();
};

export const updateShellConfig = async (
  updates: {
    quickCaptureHotkey?: string;
    quickCaptureScreenshotHotkey?: string;
    closeToTray?: boolean;
    quickCaptureAlwaysOnTop?: boolean;
  },
  windowLike: WindowLike = window
) => {
  const bridge = getDesktopBridge(windowLike);
  if (!bridge?.updateShellConfig) {
    return null;
  }

  return bridge.updateShellConfig(updates);
};
