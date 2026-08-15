import path from "node:path";

import { describe, expect, it } from "vitest";

import { buildDesktopDevEnv } from "../scripts/dev-env.js";

describe("buildDesktopDevEnv", () => {
  it("defaults Electron to the backend-dev app data directory in development", () => {
    const env = buildDesktopDevEnv({
      devServerUrl: "http://127.0.0.1:5173",
      workspaceRoot: "D:\\agent\\KnowledgeCurator",
      processEnv: {}
    });

    expect(env.VITE_DEV_SERVER_URL).toBe("http://127.0.0.1:5173");
    expect(env.KNOWLEDGE_CURATOR_APP_DATA_DIR).toBe(
      path.resolve("D:\\agent\\KnowledgeCurator", ".local/app-data/backend-dev")
    );
  });

  it("keeps an explicit app data directory when one is already provided", () => {
    const env = buildDesktopDevEnv({
      devServerUrl: "http://127.0.0.1:5173",
      workspaceRoot: "D:\\agent\\KnowledgeCurator",
      processEnv: {
        KNOWLEDGE_CURATOR_APP_DATA_DIR: "E:\\custom-app-data"
      }
    });

    expect(env.KNOWLEDGE_CURATOR_APP_DATA_DIR).toBe("E:\\custom-app-data");
  });
});
