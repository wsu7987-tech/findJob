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
  startTransportDebug?: (cols?: number, rows?: number, candidateId?: string) => Promise<unknown>;
  write: (data: string) => void;
  submitPrompt?: (prompt: string) => Promise<boolean>;
  submitWorkflowPrompt?: (prompt: string) => Promise<boolean>;
  submitWorkflowKey?: () => Promise<boolean>;
  writeTransportDebugPrompt?: (prompt: string) => Promise<boolean>;
  submitTransportDebugPrompt?: (prompt: string) => Promise<boolean>;
  submitTransportDebugKey?: () => Promise<boolean>;
  submitEnter?: () => Promise<boolean>;
  resize: (cols: number, rows: number) => void;
  interrupt: () => void;
  stop: () => void;
  state: () => unknown;
  transportDebugInfo?: () => unknown;
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
  ipcMain.handle("codex:start-transport-debug", (_event, options?: { cols?: number; rows?: number; candidateId?: string }) =>
    controller.startTransportDebug?.(options?.cols, options?.rows, options?.candidateId) ?? false
  );
  ipcMain.handle("codex:state", () => controller.state());
  ipcMain.handle("codex:transport-debug-info", () => controller.transportDebugInfo?.() ?? null);
  ipcMain.on("codex:input", (_event, data: string) => {
    if (typeof data === "string") controller.write(data);
  });
  ipcMain.handle("codex:submit-prompt", (_event, prompt: string) => {
    if (typeof prompt !== "string" || !controller.submitPrompt) return false;
    return controller.submitPrompt(prompt);
  });
  ipcMain.handle("codex:submit-enter", () => controller.submitEnter?.() ?? false);
  ipcMain.handle("codex:submit-workflow-prompt", (_event, prompt: string) => {
    if (typeof prompt !== "string" || !controller.submitWorkflowPrompt) return false;
    return controller.submitWorkflowPrompt(prompt);
  });
  ipcMain.handle("codex:submit-workflow-key", () => controller.submitWorkflowKey?.() ?? false);
  ipcMain.handle("codex:write-transport-debug-prompt", (_event, prompt: string) => {
    if (typeof prompt !== "string" || !controller.writeTransportDebugPrompt) return false;
    return controller.writeTransportDebugPrompt(prompt);
  });
  ipcMain.handle("codex:submit-transport-debug-prompt", (_event, prompt: string) => {
    if (typeof prompt !== "string" || !controller.submitTransportDebugPrompt) return false;
    return controller.submitTransportDebugPrompt(prompt);
  });
  ipcMain.handle("codex:submit-transport-debug-key", () => controller.submitTransportDebugKey?.() ?? false);
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
