import path from "node:path";

export const buildDesktopDevEnv = ({ devServerUrl, workspaceRoot, processEnv }) => ({
  VITE_DEV_SERVER_URL: devServerUrl,
  KNOWLEDGE_CURATOR_APP_DATA_DIR:
    processEnv.KNOWLEDGE_CURATOR_APP_DATA_DIR ??
    path.resolve(workspaceRoot, ".local/app-data/backend-dev")
});
