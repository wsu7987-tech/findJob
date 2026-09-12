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

export interface CodexWorkflowLaunchOptions {
  cols?: number;
  rows?: number;
  model: string;
  reasoningEffort: string;
  sessionRef?: string;
}

export type WorkflowSessionMode = "live_reused" | "resumed_explicit" | "new_from_workflow_state";

export const buildCodexInteractiveArgs = (options: {
  tuiWorkspace: string;
  resumeSessionRef?: string;
  model?: string;
  reasoningEffort?: string;
}) => {
  const args = [
    ...(options.resumeSessionRef ? ["resume", options.resumeSessionRef] : []),
    "--sandbox", "read-only", "--ask-for-approval", "on-request", "--no-alt-screen", "-C", options.tuiWorkspace
  ];
  if (options.model) args.push("--model", options.model);
  if (options.reasoningEffort) args.push("--config", `model_reasoning_effort=\"${options.reasoningEffort}\"`);
  return args;
};

// 只有 Codex CLI 接受的明确 UUID 才能跨终端恢复；runtime: 仅绑定当前本地终端。
export const isResumableCodexSessionId = (sessionRef: string | undefined) =>
  Boolean(sessionRef && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(sessionRef));

const MANAGED_SKILLS = ["finejob", "finejob-profile"] as const;

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

export const writeManagedWorkspace = (
  options: Pick<CodexSessionOptions, "appDataDir" | "workspaceRoot" | "pythonPath">
) => {
  const tuiWorkspace = path.resolve(options.appDataDir, "codex-workspace");
  const configDir = path.resolve(tuiWorkspace, ".codex");
  const managedSkillsDir = path.resolve(tuiWorkspace, ".agents", "skills");
  const skillResourcesDir = path.resolve(
    options.workspaceRoot,
    "apps",
    "desktop",
    "resources",
    "codex",
    "skills"
  );
  fs.mkdirSync(configDir, { recursive: true });
  fs.mkdirSync(managedSkillsDir, { recursive: true });
  for (const skillName of MANAGED_SKILLS) {
    const sourceDir = path.resolve(skillResourcesDir, skillName);
    const destinationDir = path.resolve(managedSkillsDir, skillName);
    if (!fs.existsSync(sourceDir)) {
      throw new Error(`缺少 FineJob Skill 资源：${sourceDir}`);
    }
    // 每次启动同步正式 Skill 资源，确保托管工作区使用仓库中的当前版本。
    fs.rmSync(destinationDir, { recursive: true, force: true });
    fs.cpSync(sourceDir, destinationDir, { recursive: true });
  }
  const config = [
    "[mcp_servers.finejob]",
    `command = ${JSON.stringify(options.pythonPath)}`,
    `args = ["-m", "backend.app.mcp.fine_job_server"]`,
    `cwd = ${JSON.stringify(options.workspaceRoot)}`,
    `env_vars = ["FINE_JOB_BACKEND_ORIGIN", "FINE_JOB_MCP_RUN_TOKEN"]`,
    ""
  ].join("\n");
  fs.writeFileSync(path.resolve(configDir, "config.toml"), config, "utf8");
  return tuiWorkspace;
};

export const createCodexSessionController = (options: CodexSessionOptions) => {
  let terminal: IPty | null = null;
  let status: SessionStatus = "idle";
  let runtimeId: string | null = null;
  let runtimeToken: string | null = null;
  let sessionRef: string | null = null;
  let recentOutput = "";
  let firstOutputPromise: Promise<void> | null = null;
  let resolveFirstOutput: (() => void) | null = null;

  const setStatus = (next: SessionStatus, message = "") => {
    status = next;
    options.emit("codex:status", { status, runtimeId, sessionRef, message });
  };

  const start = async (
    resume: boolean,
    cols = 120,
    rows = 36,
    workflow?: Pick<CodexWorkflowLaunchOptions, "model" | "reasoningEffort" | "sessionRef">
  ) => {
    if (terminal) {
      if (!workflow || workflow.sessionRef === sessionRef) {
        return {
          status,
          runtimeId,
          sessionRef,
          workflowSessionMode: workflow ? "live_reused" satisfies WorkflowSessionMode : undefined
        };
      }
      throw new Error("当前 Codex 会话属于其他 Workflow，结束后再切换。");
    }
    if (workflow && resume) {
      throw new Error("Workflow 只能按明确 Session ID 恢复，不能恢复最近会话。");
    }
    setStatus("starting");
    recentOutput = "";
    try {
      const runtime = await options.createRuntime();
      const codexPath = await options.getCodexPath();
      runtimeId = runtime.run_id;
      runtimeToken = runtime.token;
      // runtime: 前缀只表示本地 Workflow 绑定，无法证明它是可由 CLI 恢复的线程 ID。
      const resumableSessionId = isResumableCodexSessionId(workflow?.sessionRef)
        ? workflow!.sessionRef!
        : undefined;
      sessionRef = resumableSessionId ?? `runtime:${runtime.run_id}`;
      const tuiWorkspace = writeManagedWorkspace(options);
      // Workflow 只恢复已验证的明确 UUID，通用恢复最近会话不会进入该分支。
      const args = buildCodexInteractiveArgs({
        tuiWorkspace,
        resumeSessionRef: resume ? "--last" : (
          resumableSessionId
        ),
        model: workflow?.model,
        reasoningEffort: workflow?.reasoningEffort,
      });
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
      // 等待 Codex 首屏出现后再提交自动任务，避免输入早于交互界面初始化。
      firstOutputPromise = new Promise<void>((resolve) => {
        resolveFirstOutput = resolve;
      });
      terminal.onData((data) => {
        // 保存有限的最近输出，让快速退出时的首屏错误仍能显示在状态区。
        recentOutput = `${recentOutput}${data}`.slice(-RECENT_OUTPUT_LIMIT);
        resolveFirstOutput?.();
        resolveFirstOutput = null;
        firstOutputPromise = null;
        options.emit("codex:output", { runtimeId, sessionRef, data });
      });
      terminal.onExit(({ exitCode }) => {
        resolveFirstOutput?.();
        resolveFirstOutput = null;
        firstOutputPromise = null;
        terminal = null;
        const completedRunId = runtimeId;
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
      return {
        status,
        runtimeId,
        sessionRef,
        workflowSessionMode: workflow
          ? (resumableSessionId ? "resumed_explicit" : "new_from_workflow_state") satisfies WorkflowSessionMode
          : undefined
      };
    } catch (error) {
      terminal = null;
      sessionRef = null;
      resolveFirstOutput?.();
      resolveFirstOutput = null;
      firstOutputPromise = null;
      if (runtimeId && runtimeToken) {
        void options.completeRuntime(
          runtimeId,
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
    startWorkflow: (launch: CodexWorkflowLaunchOptions) =>
      start(false, launch.cols, launch.rows, launch),
    write(data: string) {
      if (terminal && data.length <= 16_384) {
        terminal.write(data);
      }
    },
    async submitPrompt(prompt: string) {
      const currentTerminal = terminal;
      const text = prompt.trim();
      if (!currentTerminal || !text || text.length > 16_380) return false;
      const pendingFirstOutput = firstOutputPromise;
      if (pendingFirstOutput) {
        // 没有首屏时最多等待两秒，避免任务入口被终端初始化卡住。
        await Promise.race([
          pendingFirstOutput,
          new Promise<void>((resolve) => setTimeout(resolve, 2_000))
        ]);
      }
      if (terminal !== currentTerminal) return false;
      // Prompt 与 Enter 分开发送，给 Codex TUI 留出接收输入的短暂窗口。
      currentTerminal.write(text);
      await new Promise<void>((resolve) => setTimeout(resolve, 80));
      if (terminal !== currentTerminal) return false;
      currentTerminal.write("\r");
      return true;
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
      sessionRef = null;
      setStatus("idle");
    },
    state: () => ({ status, runtimeId, sessionRef })
  };
};
