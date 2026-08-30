import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import { buildCodexExitMessage, writeManagedWorkspace } from "./codex-session";

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

  it("创建托管工作区时同步两个 Skill 和 MCP 配置", () => {
    const appDataDir = fs.mkdtempSync(path.join(os.tmpdir(), "finejob-codex-workspace-"));
    const workspaceRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");

    try {
      const tuiWorkspace = writeManagedWorkspace({
        appDataDir,
        workspaceRoot,
        pythonPath: "C:\\Python\\python.exe"
      });
      const skillsDir = path.resolve(tuiWorkspace, ".agents", "skills");
      const config = fs.readFileSync(path.resolve(tuiWorkspace, ".codex", "config.toml"), "utf8");
      const fineJobSkill = fs.readFileSync(path.resolve(skillsDir, "finejob", "SKILL.md"), "utf8");
      const profileSkill = fs.readFileSync(
        path.resolve(skillsDir, "finejob-profile", "SKILL.md"),
        "utf8"
      );
      const profileRules = fs.readFileSync(
        path.resolve(skillsDir, "finejob-profile", "references", "analysis-rules.md"),
        "utf8"
      );

      expect(config).toContain("[mcp_servers.finejob]");
      expect(config).toContain('args = ["-m", "backend.app.mcp.fine_job_server"]');
      expect(fineJobSkill).toContain("name: finejob");
      expect(profileSkill).toContain("name: finejob-profile");
      expect(profileRules).toContain("# 分析规则");
    } finally {
      fs.rmSync(appDataDir, { recursive: true, force: true });
    }
  });
});
