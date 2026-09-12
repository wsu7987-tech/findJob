import fs from "node:fs";
import path from "node:path";
import type { IPty } from "node-pty";
import { spawn as spawnPty } from "node-pty";

import { ensureCodexTerminalType, resolveCodexLaunch } from "./codex-command";

type SessionStatus = "idle" | "starting" | "running" | "exited" | "failed";

const RECENT_OUTPUT_LIMIT = 4_096;
const EXIT_SUMMARY_LIMIT = 500;
const INITIAL_TUI_OUTPUT_TIMEOUT_MS = 2_000;
const PROMPT_SUBMIT_FALLBACK_MS = 750;
const PROMPT_SUBMIT_SETTLE_MS = 300;
export const FINEJOB_WORKFLOW_COMPOSER_SUBMIT_BINDING = "enter";
export const FINEJOB_WORKFLOW_COMPOSER_SUBMIT_SEQUENCE = "\r";
export const FINEJOB_WORKFLOW_COMPOSER_SUBMIT_SEQUENCE_DISPLAY = "\\r";

export interface CodexTransportDebugSubmitCandidate {
  id: string;
  binding: string;
  keySequence: string;
  keySequenceDisplay: string;
}

// 默认调试键与物理 Enter 保持一致；候选通过启动参数写入当前会话。
export const FINEJOB_TRANSPORT_DEBUG_SUBMIT_CANDIDATES: readonly CodexTransportDebugSubmitCandidate[] = [
  { id: "enter", binding: "enter", keySequence: "\r", keySequenceDisplay: "\\r" },
  { id: "ctrl-y", binding: "ctrl-y", keySequence: "\x19", keySequenceDisplay: "\\x19" },
  { id: "ctrl-q", binding: "ctrl-q", keySequence: "\x11", keySequenceDisplay: "\\x11" },
  { id: "ctrl-o", binding: "ctrl-o", keySequence: "\x0f", keySequenceDisplay: "\\x0f" },
  { id: "ctrl-t", binding: "ctrl-t", keySequence: "\x14", keySequenceDisplay: "\\x14" }
];

const FINEJOB_WORKFLOW_COMPOSER_SUBMIT = {
  binding: FINEJOB_WORKFLOW_COMPOSER_SUBMIT_BINDING,
  keySequence: FINEJOB_WORKFLOW_COMPOSER_SUBMIT_SEQUENCE,
  keySequenceDisplay: FINEJOB_WORKFLOW_COMPOSER_SUBMIT_SEQUENCE_DISPLAY
};

type DedicatedComposerMode = "workflow" | "transport_debug" | null;

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

type KeymapConfigValue = string | string[];

const serializeKeymapConfigValue = (value: KeymapConfigValue) =>
  Array.isArray(value)
    ? `[${value.map((item) => JSON.stringify(item)).join(", ")}]`
    : JSON.stringify(value);

export const buildCodexInteractiveArgs = (options: {
  tuiWorkspace: string;
  resumeSessionRef?: string;
  model?: string;
  reasoningEffort?: string;
  workflowComposerSubmitBinding?: string;
  managedComposerSubmitBinding?: string;
  keymapOverrides?: Record<string, KeymapConfigValue>;
  unbindKeymapActions?: string[];
}) => {
  const args = [
    ...(options.resumeSessionRef ? ["resume", options.resumeSessionRef] : []),
    "--sandbox", "read-only", "--ask-for-approval", "on-request", "--no-alt-screen", "-C", options.tuiWorkspace
  ];
  if (options.model) args.push("--model", options.model);
  if (options.reasoningEffort) args.push("--config", `model_reasoning_effort=\"${options.reasoningEffort}\"`);
  if (options.workflowComposerSubmitBinding) {
    args.push("--config", `tui.keymap.composer.submit=\"${options.workflowComposerSubmitBinding}\"`);
  }
  if (options.managedComposerSubmitBinding) {
    args.push("--config", `tui.keymap.composer.submit=\"${options.managedComposerSubmitBinding}\"`);
  }
  for (const [action, binding] of Object.entries(options.keymapOverrides ?? {})) {
    // 普通会话固定提交与换行职责，避免用户配置改变自动任务的输入含义。
    args.push("--config", `tui.keymap.${action}=${serializeKeymapConfigValue(binding)}`);
  }
  for (const action of options.unbindKeymapActions ?? []) {
    // 调试会话使用应用托管按键，先解除 Codex 默认动作占用，避免启动时 keymap 冲突。
    args.push("--config", `tui.keymap.${action}=[]`);
  }
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
  let dedicatedComposerSubmit: {
    binding: string;
    keySequence: string;
    keySequenceDisplay: string;
  } | null = null;
  let dedicatedComposerMode: DedicatedComposerMode = null;
  let recentOutput = "";
  let firstOutputPromise: Promise<void> | null = null;
  let resolveFirstOutput: (() => void) | null = null;
  let outputSequence = 0;
  const outputWaiters = new Set<() => void>();
  let transportDebugPromptOutputSequence: number | null = null;

  const waitForOutputAfter = (sequence: number) => new Promise<void>((resolve) => {
    if (outputSequence > sequence) {
      resolve();
      return;
    }
    const complete = () => {
      clearTimeout(fallback);
      outputWaiters.delete(complete);
      resolve();
    };
    // PTY 输出只用于安排 Enter 的发送时机，业务开始仍由 MCP ACK 确认。
    const fallback = setTimeout(complete, PROMPT_SUBMIT_FALLBACK_MS);
    outputWaiters.add(complete);
  });

  const resolveOutputWaiters = () => {
    for (const resolve of [...outputWaiters]) resolve();
  };

  const setStatus = (next: SessionStatus, message = "") => {
    status = next;
    options.emit("codex:status", { status, runtimeId, sessionRef, message });
  };

  const start = async (
    resume: boolean,
    cols = 120,
    rows = 36,
    workflow?: Pick<CodexWorkflowLaunchOptions, "model" | "reasoningEffort" | "sessionRef">,
    requestedComposerMode: DedicatedComposerMode = null,
    requestedComposerSubmit: {
      binding: string;
      keySequence: string;
      keySequenceDisplay: string;
    } | null = null
  ) => {
    if (terminal) {
      if (requestedComposerMode === "transport_debug") {
        if (dedicatedComposerMode !== "transport_debug") {
          throw new Error("当前 Codex 会话不是 Transport Debug 会话，请先结束后再启动测试会话。");
        }
        return { status, runtimeId, sessionRef };
      }
      if (!workflow || workflow.sessionRef === sessionRef) {
        if (workflow && dedicatedComposerMode !== "workflow") {
          throw new Error("当前 Codex 会话不是 Workflow 会话，结束后再切换。");
        }
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
        workflowComposerSubmitBinding: requestedComposerSubmit?.binding,
        managedComposerSubmitBinding: requestedComposerMode === null ? "enter" : undefined,
        keymapOverrides: requestedComposerMode === "transport_debug"
          ? undefined
          : { "editor.insert_newline": ["shift-enter"] },
        unbindKeymapActions: requestedComposerMode === "transport_debug"
          ? ["editor.insert_newline", "editor.yank"]
          : undefined
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
        outputSequence += 1;
        resolveOutputWaiters();
        options.emit("codex:output", { runtimeId, sessionRef, data });
      });
      terminal.onExit(({ exitCode }) => {
        resolveFirstOutput?.();
        resolveFirstOutput = null;
        firstOutputPromise = null;
        resolveOutputWaiters();
        terminal = null;
        dedicatedComposerSubmit = null;
        dedicatedComposerMode = null;
        transportDebugPromptOutputSequence = null;
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
      dedicatedComposerSubmit = requestedComposerSubmit;
      dedicatedComposerMode = requestedComposerMode;
      transportDebugPromptOutputSequence = null;
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
      dedicatedComposerSubmit = null;
      dedicatedComposerMode = null;
      transportDebugPromptOutputSequence = null;
      resolveFirstOutput?.();
      resolveFirstOutput = null;
      firstOutputPromise = null;
      resolveOutputWaiters();
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
      start(false, launch.cols, launch.rows, launch, "workflow", FINEJOB_WORKFLOW_COMPOSER_SUBMIT),
    startTransportDebug: (cols?: number, rows?: number, candidateId?: string) => {
      const candidate = FINEJOB_TRANSPORT_DEBUG_SUBMIT_CANDIDATES.find((item) => item.id === candidateId)
        ?? FINEJOB_TRANSPORT_DEBUG_SUBMIT_CANDIDATES[0];
      return start(false, cols, rows, undefined, "transport_debug", candidate);
    },
    write(data: string) {
      if (terminal && data.length <= 16_384) {
        terminal.write(data);
      }
    },
    async submitPrompt(prompt: string) {
      return submitPromptWithKey(prompt, "\r");
    },
    async submitWorkflowPrompt(prompt: string) {
      if (!dedicatedComposerSubmit || dedicatedComposerMode !== "workflow") return false;
      return submitPromptWithKey(prompt, dedicatedComposerSubmit.keySequence);
    },
    async submitWorkflowKey() {
      if (!terminal || status !== "running" || dedicatedComposerMode !== "workflow" || !dedicatedComposerSubmit) return false;
      terminal.write(dedicatedComposerSubmit.keySequence);
      return true;
    },
    async writeTransportDebugPrompt(prompt: string) {
      const text = prompt.trim();
      if (!terminal || status !== "running" || dedicatedComposerMode !== "transport_debug" || !text || text.length > 16_380) {
        return false;
      }
      // 记录写入前的输出序号，提交动作会等待后续回显，避免输入和提交键同时进入 PTY。
      transportDebugPromptOutputSequence = outputSequence;
      terminal.write(text);
      return true;
    },
    async submitTransportDebugPrompt(prompt: string) {
      if (!dedicatedComposerSubmit || dedicatedComposerMode !== "transport_debug") return false;
      // 一次完成 Prompt 写入、回显等待和提交，确保按钮行为与人工输入后按提交键一致。
      return submitPromptWithKey(prompt, dedicatedComposerSubmit.keySequence);
    },
    async submitTransportDebugKey() {
      if (!terminal || status !== "running" || dedicatedComposerMode !== "transport_debug" || !dedicatedComposerSubmit) return false;
      const currentTerminal = terminal;
      const promptOutputSequence = transportDebugPromptOutputSequence;
      if (promptOutputSequence !== null) {
        await waitForOutputAfter(promptOutputSequence);
      }
      if (terminal !== currentTerminal) return false;
      currentTerminal.write(dedicatedComposerSubmit.keySequence);
      transportDebugPromptOutputSequence = null;
      return true;
    },
    async submitEnter() {
      if (!terminal || status !== "running") return false;
      terminal.write("\r");
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
      dedicatedComposerSubmit = null;
      dedicatedComposerMode = null;
      setStatus("idle");
    },
    state: () => ({ status, runtimeId, sessionRef }),
    transportDebugInfo: () => ({
      binding: dedicatedComposerSubmit?.binding ?? FINEJOB_TRANSPORT_DEBUG_SUBMIT_CANDIDATES[0].binding,
      keySequence: dedicatedComposerSubmit?.keySequenceDisplay ?? FINEJOB_TRANSPORT_DEBUG_SUBMIT_CANDIDATES[0].keySequenceDisplay,
      sessionMode: dedicatedComposerMode,
      candidates: FINEJOB_TRANSPORT_DEBUG_SUBMIT_CANDIDATES.map(({ id, binding, keySequenceDisplay }) => ({
        id,
        binding,
        keySequence: keySequenceDisplay
      }))
    })
  };

  async function submitPromptWithKey(prompt: string, submitKey: string) {
    const currentTerminal = terminal;
    const text = prompt.trim();
    if (!currentTerminal || !text || text.length > 16_380) return false;
    const pendingFirstOutput = firstOutputPromise;
    if (pendingFirstOutput) {
      // 没有首屏时最多等待两秒，避免任务入口被终端初始化卡住。
      await Promise.race([
        pendingFirstOutput,
        new Promise<void>((resolve) => setTimeout(resolve, INITIAL_TUI_OUTPUT_TIMEOUT_MS))
      ]);
    }
    if (terminal !== currentTerminal) return false;
    // Prompt 与提交键分开发送，等待写入后的新输出或保守回退后再提交。
    const outputBeforePrompt = outputSequence;
    currentTerminal.write(text);
    await waitForOutputAfter(outputBeforePrompt);
    // 即使首屏输出恰好晚到，也给 composer 一个独立的处理窗口，避免提交键和文本进入同一批输入。
    await new Promise<void>((resolve) => setTimeout(resolve, PROMPT_SUBMIT_SETTLE_MS));
    if (terminal !== currentTerminal) return false;
    currentTerminal.write(submitKey);
    return true;
  }
};
