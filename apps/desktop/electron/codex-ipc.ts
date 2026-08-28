import type { IpcMain, WebContents } from "electron";

interface CodexController {
  start: (cols?: number, rows?: number) => Promise<unknown>;
  resume: (cols?: number, rows?: number) => Promise<unknown>;
  write: (data: string) => void;
  resize: (cols: number, rows: number) => void;
  interrupt: () => void;
  stop: () => void;
  state: () => unknown;
}

export const registerCodexIpc = (
  ipcMain: IpcMain,
  controller: CodexController,
  getWebContents: () => WebContents | null
) => {
  ipcMain.handle("codex:start", (_event, size?: { cols?: number; rows?: number }) =>
    controller.start(size?.cols, size?.rows)
  );
  ipcMain.handle("codex:resume", (_event, size?: { cols?: number; rows?: number }) =>
    controller.resume(size?.cols, size?.rows)
  );
  ipcMain.handle("codex:state", () => controller.state());
  ipcMain.on("codex:input", (_event, data: string) => {
    if (typeof data === "string") controller.write(data);
  });
  ipcMain.on("codex:resize", (_event, size: { cols: number; rows: number }) => {
    if (Number.isInteger(size?.cols) && Number.isInteger(size?.rows)) {
      controller.resize(size.cols, size.rows);
    }
  });
  ipcMain.on("codex:interrupt", () => controller.interrupt());
  ipcMain.on("codex:stop", () => controller.stop());

  return (channel: "codex:output" | "codex:status", payload: unknown) => {
    const contents = getWebContents();
    if (contents && !contents.isDestroyed()) contents.send(channel, payload);
  };
};
