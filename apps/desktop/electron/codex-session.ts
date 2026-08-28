import fs from "node:fs";
import path from "node:path";
import type { IPty } from "node-pty";
import { spawn as spawnPty } from "node-pty";

import { ensureCodexTerminalType, resolveCodexLaunch } from "./codex-command";

type SessionStatus = "idle" | "starting" | "running" | "exited" | "failed";

const RECENT_OUTPUT_LIMIT = 4_096;
const EXIT_SUMMARY_LIMIT = 500;

const stripTerminalControlSequences = (value: string) =>
  value
    .replace(/\x1b\][^\x07]*(?:\x07|\x1b\\)/g, "")
    .replace(/\x1b\[[0-?]*[ -/]*[@-~]/g, "")
    .replace(/\r/g, "\n");

export const buildCodexExitMessage = (exitCode: number, recentOutput: string) => {
  const summary = stripTerminalControlSequences(recentOutput)
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .slice(-4)
    .join(" ")
    .slice(0, EXIT_SUMMARY_LIMIT);
  return summary ? `Codex 已退出（${exitCode}）：${summary}` : `Codex 已退出（${exitCode}）`;
};

export interface CodexSessionOptions {
  appDataDir: string;
  workspaceRoot: string;
  backendOrigin: string;
  pythonPath: string;
  getCodexPath: () => Promise<string>;
  createRuntime: () => Promise<{ run_id: string; token: string; expires_at: string }>;
  completeRuntime: (
    runId: string,
    token: string,
    status: "exited" | "failed",
    reason: string
  ) => Promise<void>;
  emit: (channel: "codex:output" | "codex:status", payload: unknown) => void;
  debugLog: (message: string) => void;
}

const SKILL_TEXT = `---
name: finejob
description: 使用 FineJob MCP 工具完成岗位检索、岗位评估、打招呼预览与代聊回复。
---

# FineJob 业务协作

先调用 \`finejob.get_capabilities\`。读取岗位或会话上下文后保留返回的版本号，写入评估、预览和发送请求时原样传回。敏感动作返回 \`awaiting_confirmation\` 时，提示用户在 FineJob 确认卡片处理。任务进入队列后用 \`finejob.get_operation_status\` 查询结果。
`;

const quoteBatchValue = (value: string) => `"${value.replace(/"/g, '""')}"`;

const writeWindowsUtf8Launcher = (workspace: string, launch: ReturnType<typeof resolveCodexLaunch>) => {
  const launcherPath = path.resolve(workspace, "codex-utf8-launcher.cmd");
  const launchFile = launch.file.toLowerCase();
  let invocation: string;

  if (launchFile.endsWith("cmd.exe") && launch.args[2] === "/c" && launch.args[3]) {
    // cmd 脚本先切换到 UTF-8，再把会话参数转交给实际 Codex 入口。
    invocation = `call ${quoteBatchValue(launch.args[3])} %*`;
  } else if (launchFile.endsWith("powershell.exe") && launch.args[4]) {
    // PowerShell 入口沿用原有启动参数，避免 .ps1 配置失去兼容性。
    invocation = [
      "call",
      quoteBatchValue(launch.file),
      ...launch.args.slice(0, 4),
      quoteBatchValue(launch.args[4]),
      "%*"
    ].join(" ");
  } else {
    const command = launchFile.endsWith(".exe") ? quoteBatchValue(launch.file) : `call ${quoteBatchValue(launch.file)}`;
    invocation = `${command} %*`;
  }

  fs.writeFileSync(
    launcherPath,
    ["@echo off", "chcp 65001 >nul", invocation, ""].join("\r\n"),
    "utf8"
  );
  return launcherPath;
};

const writeManagedWorkspace = (options: CodexSessionOptions) => {
  const tuiWorkspace = path.resolve(options.appDataDir, "codex-workspace");
  const configDir = path.resolve(tuiWorkspace, ".codex");
  const skillDir = path.resolve(tuiWorkspace, ".agents", "skills", "finejob");
  fs.mkdirSync(configDir, { recursive: true });
  fs.mkdirSync(skillDir, { recursive: true });
  const config = [
    "[mcp_servers.finejob]",
    `command = ${JSON.stringify(options.pythonPath)}`,
    `args = ["-m", "backend.app.mcp.fine_job_server"]`,
    `cwd = ${JSON.stringify(options.workspaceRoot)}`,
    `env_vars = ["FINE_JOB_BACKEND_ORIGIN", "FINE_JOB_MCP_RUN_TOKEN"]`,
    ""
  ].join("\n");
  fs.writeFileSync(path.resolve(configDir, "config.toml"), config, "utf8");
  fs.writeFileSync(path.resolve(skillDir, "SKILL.md"), SKILL_TEXT, "utf8");
  return tuiWorkspace;
};

export const createCodexSessionController = (options: CodexSessionOptions) => {
  let terminal: IPty | null = null;
  let status: SessionStatus = "idle";
  let runId: string | null = null;
  let runtimeToken: string | null = null;
  let recentOutput = "";

  const setStatus = (next: SessionStatus, message = "") => {
    status = next;
    options.emit("codex:status", { status, runId, message });
  };

  const start = async (resume: boolean, cols = 120, rows = 36) => {
    if (terminal) {
      return { status, runId };
    }
    setStatus("starting");
    recentOutput = "";
    try {
      const runtime = await options.createRuntime();
      const codexPath = await options.getCodexPath();
      runId = runtime.run_id;
      runtimeToken = runtime.token;
      const tuiWorkspace = writeManagedWorkspace(options);
      const args = [
        ...(resume ? ["resume", "--last"] : []),
        "--sandbox",
        "read-only",
        "--ask-for-approval",
        "on-request",
        // 使用内联终端保留 Codex 的完整会话滚动历史。
        "--no-alt-screen",
        "-C",
        tuiWorkspace
      ];
      const resolvedLaunch = resolveCodexLaunch(codexPath, []);
      const launch =
        process.platform === "win32"
          ? resolveCodexLaunch(writeWindowsUtf8Launcher(tuiWorkspace, resolvedLaunch), args)
          : resolveCodexLaunch(codexPath, args);
      const terminalEnv = ensureCodexTerminalType({
        ...process.env,
        FINE_JOB_BACKEND_ORIGIN: options.backendOrigin,
        FINE_JOB_MCP_RUN_TOKEN: runtime.token
      });
      terminal = spawnPty(launch.file, launch.args, {
        name: "xterm-256color",
        cols: Math.max(20, Math.min(400, cols)),
        rows: Math.max(8, Math.min(200, rows)),
        cwd: tuiWorkspace,
        encoding: "utf8",
        env: terminalEnv,
        useConpty: process.platform === "win32"
      });
      terminal.onData((data) => {
        // 保存有限的最近输出，让快速退出时的首屏错误仍能显示在状态区。
        recentOutput = `${recentOutput}${data}`.slice(-RECENT_OUTPUT_LIMIT);
        options.emit("codex:output", { runId, data });
      });
      terminal.onExit(({ exitCode }) => {
        terminal = null;
        const completedRunId = runId;
        const completedToken = runtimeToken;
        if (completedRunId && completedToken) {
          void options.completeRuntime(
            completedRunId,
            completedToken,
            exitCode === 0 ? "exited" : "failed",
            `Codex 进程退出码 ${exitCode}`
          );
        }
        runtimeToken = null;
        setStatus(
          exitCode === 0 ? "exited" : "failed",
          buildCodexExitMessage(exitCode, recentOutput)
        );
      });
      setStatus("running");
      return { status, runId };
    } catch (error) {
      terminal = null;
      if (runId && runtimeToken) {
        void options.completeRuntime(
          runId,
          runtimeToken,
          "failed",
          error instanceof Error ? error.message : String(error)
        );
      }
      runtimeToken = null;
      setStatus("failed", error instanceof Error ? error.message : String(error));
      throw error;
    }
  };

  return {
    start: (cols?: number, rows?: number) => start(false, cols, rows),
    resume: (cols?: number, rows?: number) => start(true, cols, rows),
    write(data: string) {
      if (terminal && data.length <= 16_384) {
        terminal.write(data);
      }
    },
    resize(cols: number, rows: number) {
      terminal?.resize(Math.max(20, Math.min(400, cols)), Math.max(8, Math.min(200, rows)));
    },
    interrupt() {
      terminal?.write("\x03");
    },
    stop() {
      terminal?.kill();
      terminal = null;
      setStatus("idle");
    },
    state: () => ({ status, runId })
  };
};
