import { execFileSync } from "node:child_process";
import path from "node:path";

export interface CodexLaunchSpec {
  file: string;
  args: string[];
}

export const ensureCodexTerminalType = (env: NodeJS.ProcessEnv): NodeJS.ProcessEnv => ({
  ...env,
  // Codex TUI 不支持 dumb 终端，node-pty 已提供 xterm 兼容终端。
  TERM: env.TERM?.trim() && env.TERM.toLowerCase() !== "dumb" ? env.TERM : "xterm-256color",
  // MCP Python 子进程和终端命令统一使用 UTF-8，避免中文输出按本地代码页解码。
  PYTHONIOENCODING: env.PYTHONIOENCODING ?? "utf-8",
  PYTHONUTF8: env.PYTHONUTF8 ?? "1"
});

interface CodexLaunchOptions {
  platform?: NodeJS.Platform;
  env?: NodeJS.ProcessEnv;
  findOnPath?: (command: string, env: NodeJS.ProcessEnv) => string[];
}

const WINDOWS_DIRECT_EXTENSIONS = new Set([".com", ".exe"]);
const WINDOWS_SCRIPT_EXTENSIONS = new Set([".bat", ".cmd"]);
const WINDOWS_SUPPORTED_EXTENSIONS = new Set([
  ...WINDOWS_DIRECT_EXTENSIONS,
  ...WINDOWS_SCRIPT_EXTENSIONS,
  ".ps1"
]);

const findWindowsCommandsOnPath = (command: string, env: NodeJS.ProcessEnv) => {
  try {
    const output = execFileSync("where.exe", [command], {
      encoding: "utf8",
      env,
      windowsHide: true
    });
    return output
      .split(/\r?\n/)
      .map((value) => value.trim())
      .filter(Boolean);
  } catch {
    return [];
  }
};

const hasPathSeparator = (value: string) => value.includes("\\") || value.includes("/");

const resolveWindowsCommandPath = (
  candidate: string,
  options: Required<Pick<CodexLaunchOptions, "env" | "findOnPath">>
) => {
  if (path.isAbsolute(candidate) || hasPathSeparator(candidate)) {
    return candidate;
  }

  const paths = options.findOnPath(candidate, options.env);
  // 遵循 PATH 的解析顺序，跳过 Windows 无法由 PTY 直接启动的无扩展名脚本。
  const launchablePath = paths.find((value) =>
    WINDOWS_SUPPORTED_EXTENSIONS.has(path.extname(value).toLowerCase())
  );
  return launchablePath ?? candidate;
};

const buildBatchLaunchArgs = (scriptPath: string, args: string[]) => {
  // 参数保持独立，由 node-pty 统一生成 Windows 命令行，避免嵌套引号变成字面字符。
  return ["/d", "/s", "/c", scriptPath, ...args];
};

export const resolveCodexLaunch = (
  codexPath: string,
  args: string[],
  options: CodexLaunchOptions = {}
): CodexLaunchSpec => {
  const candidate = codexPath.trim() || "codex";
  const platform = options.platform ?? process.platform;
  if (platform !== "win32") {
    return { file: candidate, args: [...args] };
  }

  const env = options.env ?? process.env;
  const resolvedPath = resolveWindowsCommandPath(candidate, {
    env,
    findOnPath: options.findOnPath ?? findWindowsCommandsOnPath
  });
  const extension = path.extname(resolvedPath).toLowerCase();

  if (WINDOWS_SCRIPT_EXTENSIONS.has(extension)) {
    return {
      file: env.ComSpec || "cmd.exe",
      args: buildBatchLaunchArgs(resolvedPath, args)
    };
  }

  if (extension === ".ps1") {
    return {
      file: env.SystemRoot
        ? path.join(env.SystemRoot, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
        : "powershell.exe",
      args: ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", resolvedPath, ...args]
    };
  }

  return { file: resolvedPath, args: [...args] };
};
