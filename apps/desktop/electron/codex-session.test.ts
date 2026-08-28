import { describe, expect, it } from "vitest";

import { buildCodexExitMessage } from "./codex-session";

describe("buildCodexExitMessage", () => {
  it("在非零退出状态中保留去除终端控制符后的错误摘要", () => {
    const message = buildCodexExitMessage(
      1,
      '\u001b[31mRefusing to start the interactive TUI because TERM is set to "dumb".\u001b[0m\r\n'
    );

    expect(message).toBe(
      'Codex 已退出（1）：Refusing to start the interactive TUI because TERM is set to "dumb".'
    );
  });

  it("没有输出时只显示退出码", () => {
    expect(buildCodexExitMessage(1, "")).toBe("Codex 已退出（1）");
  });
});
