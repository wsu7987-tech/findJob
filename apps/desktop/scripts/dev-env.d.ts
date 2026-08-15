export interface DesktopDevEnvOptions {
  devServerUrl: string;
  workspaceRoot: string;
  processEnv: NodeJS.ProcessEnv;
}

export function buildDesktopDevEnv(
  options: DesktopDevEnvOptions
): {
  VITE_DEV_SERVER_URL: string;
  KNOWLEDGE_CURATOR_APP_DATA_DIR: string;
};
