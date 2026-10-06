import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

export interface BackendProcessOptions {
  debugLog: (message: string) => void;
  appDataDir: string;
  workspaceRoot: string;
  backendOrigin: string;
  backendExecutable?: string;
}

export const createBackendProcessController = (options: BackendProcessOptions) => {
  const pythonPath =
    process.env.FINE_JOB_PYTHON_PATH ??
    path.resolve(
      options.workspaceRoot,
      ".venv",
      process.platform === "win32" ? "Scripts/python.exe" : "bin/python"
    );

  const healthReady = async () => {
    try {
      const response = await fetch(`${options.backendOrigin}/api/health`, {
        signal: AbortSignal.timeout(1_500)
      });
      if (!response.ok) return false;
      const payload = (await response.json()) as {
        status?: string;
        service?: string;
      };
      return payload.status === "ok" && payload.service === "knowledge-curator";
    } catch {
      return false;
    }
  };

  const waitUntilReady = async () => {
    for (let attempt = 0; attempt < 80; attempt += 1) {
      if (await healthReady()) {
        return;
      }
      await new Promise((resolve) => setTimeout(resolve, 250));
    }
    throw new Error("FineJob 后端启动超时。");
  };

  return {
    async start() {
      if (await healthReady()) {
        options.debugLog("backend already ready");
        return;
      }
      const executable = options.backendExecutable ?? pythonPath;
      if (!fs.existsSync(executable)) {
        throw new Error(`FineJob 后端启动文件不存在：${executable}`);
      }
      const packaged = Boolean(options.backendExecutable);
      const args = packaged
        ? [
            "--host",
            "127.0.0.1",
            "--port",
            new URL(options.backendOrigin).port || "8000"
          ]
        : [
            "-m",
            "uvicorn",
            "backend.app.main:create_app",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            new URL(options.backendOrigin).port || "8000"
          ];
      const spawned = spawn(
        executable,
        args,
        {
          cwd: packaged ? path.dirname(executable) : options.workspaceRoot,
          env: {
            ...process.env,
            KNOWLEDGE_CURATOR_APP_DATA_DIR: options.appDataDir,
            KNOWLEDGE_CURATOR_BACKEND_PORT: new URL(options.backendOrigin).port || "8000"
          },
          detached: true,
          windowsHide: true,
          stdio: "ignore"
        }
      );
      // 后端作为本机常驻进程运行，Electron 关闭后仍可由下次启动直接复用。
      spawned.unref();
      spawned.on("exit", (code) => {
        options.debugLog(`backend exited code=${code ?? "unknown"}`);
      });
      await waitUntilReady();
    },
    async createCodexRuntime() {
      await this.start();
      const response = await fetch(`${options.backendOrigin}/api/internal/codex/v1/runtime`, {
        method: "POST",
        signal: AbortSignal.timeout(5_000)
      });
      if (!response.ok) {
        throw new Error(`FineJob 运行凭证创建失败：${response.status}`);
      }
      return (await response.json()) as { run_id: string; token: string; expires_at: string };
    },
    async getCodexPath() {
      await this.start();
      const response = await fetch(`${options.backendOrigin}/api/config`, {
        signal: AbortSignal.timeout(5_000)
      });
      if (!response.ok) return process.env.FINE_JOB_CODEX_PATH ?? "codex";
      const config = (await response.json()) as { codex_cli_path?: string | null };
      return config.codex_cli_path?.trim() || process.env.FINE_JOB_CODEX_PATH || "codex";
    },
    async completeCodexRuntime(
      runId: string,
      token: string,
      status: "exited" | "failed",
      reason: string
    ) {
      try {
        await fetch(`${options.backendOrigin}/api/internal/codex/v1/runtime/${runId}/complete`, {
          method: "POST",
          headers: {
            "Authorization": `Bearer ${token}`,
            "Content-Type": "application/json",
            "X-FineJob-MCP-Contract-Version": "v1",
            "X-FineJob-Internal-API-Version": "v1"
          },
          body: JSON.stringify({ status, reason }),
          signal: AbortSignal.timeout(3_000)
        });
      } catch (error) {
        options.debugLog(
          `codex runtime completion failed: ${error instanceof Error ? error.message : String(error)}`
        );
      }
    }
  };
};
