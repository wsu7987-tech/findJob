import { describe, expect, it, vi } from "vitest";

import { ensureCodexTerminalType, resolveCodexLaunch } from "./codex-command";

const codexArgs = ["--sandbox", "read-only", "-C", "C:\\Users\\su\\AppData\\codex-workspace"];

describe("resolveCodexLaunch", () => {
  it("将缺失或 dumb 终端转换为 Codex 支持的终端类型", () => {
    expect(ensureCodexTerminalType({ TERM: "dumb", FINE_JOB_TEST: "1" })).toMatchObject({
      TERM: "xterm-256color",
      FINE_JOB_TEST: "1"
    });
    expect(ensureCodexTerminalType({})).toMatchObject({ TERM: "xterm-256color" });
  });

  it("保留已有的有效终端类型", () => {
    expect(ensureCodexTerminalType({ TERM: "screen-256color" }).TERM).toBe("screen-256color");
  });

  it("在 Windows 下按 PATH 顺序选择第一个可启动入口", () => {
    const findOnPath = vi.fn(() => [
      "D:\\software\\nodejs\\codex",
      "D:\\software\\nodejs\\codex.cmd",
      "C:\\Tools\\codex.exe"
    ]);

    const launch = resolveCodexLaunch("codex", codexArgs, {
      platform: "win32",
      env: { ComSpec: "C:\\Windows\\System32\\cmd.exe" },
      findOnPath
    });

    expect(launch.file).toBe("C:\\Windows\\System32\\cmd.exe");
    expect(launch.args).toEqual([
      "/d",
      "/s",
      "/c",
      "D:\\software\\nodejs\\codex.cmd",
      ...codexArgs
    ]);
    expect(findOnPath).toHaveBeenCalledWith("codex", { ComSpec: "C:\\Windows\\System32\\cmd.exe" });
  });

  it("PATH 中 exe 位于脚本之前时直接启动 exe", () => {
    const launch = resolveCodexLaunch("codex", codexArgs, {
      platform: "win32",
      env: { ComSpec: "C:\\Windows\\System32\\cmd.exe" },
      findOnPath: () => ["C:\\Tools\\codex.exe", "D:\\software\\nodejs\\codex.cmd"]
    });

    expect(launch).toEqual({ file: "C:\\Tools\\codex.exe", args: codexArgs });
  });

  it("通过 cmd.exe 启动显式配置的 cmd 文件", () => {
    const launch = resolveCodexLaunch("D:\\software\\nodejs\\codex.cmd", codexArgs, {
      platform: "win32",
      env: { ComSpec: "C:\\Windows\\System32\\cmd.exe" }
    });

    expect(launch.file).toBe("C:\\Windows\\System32\\cmd.exe");
    expect(launch.args).toEqual([
      "/d",
      "/s",
      "/c",
      "D:\\software\\nodejs\\codex.cmd",
      ...codexArgs
    ]);
  });

  it("在非 Windows 平台保留原始命令和参数", () => {
    const launch = resolveCodexLaunch("codex", codexArgs, { platform: "linux" });

    expect(launch).toEqual({ file: "codex", args: codexArgs });
  });
});
