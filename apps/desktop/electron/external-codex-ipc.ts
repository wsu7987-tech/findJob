import type { IpcMain } from "electron";

interface ExternalCodexIntegrationController {
  installMcp: () => unknown;
  installSkills: () => unknown;
  status: () => unknown;
}

export const registerExternalCodexIntegrationIpc = (
  ipcMain: IpcMain,
  controller: ExternalCodexIntegrationController
) => {
  ipcMain.handle("external-codex:status", () => controller.status());
  ipcMain.handle("external-codex:install-mcp", () => controller.installMcp());
  ipcMain.handle("external-codex:install-skills", () => controller.installSkills());
};
