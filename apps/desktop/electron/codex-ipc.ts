import type { IpcMain, WebContents } from "electron";

interface CodexController {
  start: (cols?: number, rows?: number) => Promise<unknown>;
  resume: (cols?: number, rows?: number) => Promise<unknown>;
  startWorkflow: (launch: {
    cols?: number;
    rows?: number;
    model: string;
    reasoningEffort: string;
    sessionRef?: string;
  }) => Promise<unknown>;
  write: (data: string) => void;
  submitPrompt?: (prompt: string) => Promise<boolean>;
  submitEnter?: () => Promise<boolean>;
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
  ipcMain.handle("codex:start-workflow", (_event, launch: {
    cols?: number;
    rows?: number;
    model: string;
    reasoningEffort: string;
    sessionRef?: string;
  }) => controller.startWorkflow(launch));
  ipcMain.handle("codex:state", () => controller.state());
  ipcMain.on("codex:input", (_event, data: string) => {
    if (typeof data === "string") controller.write(data);
  });
  ipcMain.handle("codex:submit-prompt", (_event, prompt: string) => {
    if (typeof prompt !== "string" || !controller.submitPrompt) return false;
    return controller.submitPrompt(prompt);
  });
  ipcMain.handle("codex:submit-enter", () => controller.submitEnter?.() ?? false);
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
