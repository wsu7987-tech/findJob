import fs from "node:fs";
import path from "node:path";

const MANAGED_SKILLS = ["finejob", "finejob-profile"] as const;

export interface ExternalCodexIntegrationOptions {
  appDataDir: string;
  backendOrigin: string;
  homeDir: string;
  pythonPath: string;
  workspaceRoot: string;
}

export interface ExternalCodexIntegrationStatus {
  mcp: {
    configPath: string;
    installed: boolean;
    launcherPath: string;
  };
  skills: Array<{
    installed: boolean;
    name: (typeof MANAGED_SKILLS)[number];
    path: string;
  }>;
}

const toTomlString = (value: string) => JSON.stringify(value);

const quoteBatchValue = (value: string) => `"${value.replace(/"/g, '""')}"`;

const quoteShellValue = (value: string) => `'${value.replace(/'/g, "'\\\"'\\\"'")}'`;

const replaceFineJobMcpSection = (config: string, section: string) => {
  const normalized = config.replace(/\r\n/g, "\n");
  const pattern = /^\[mcp_servers\.finejob\]\n[\s\S]*?(?=^\[[^\n]+\]\n|\s*$)/m;
  if (pattern.test(normalized)) {
    return normalized.replace(pattern, section);
  }
  const separator = normalized.trim() ? "\n\n" : "";
  return `${normalized.trimEnd()}${separator}${section}`;
};

export const createExternalCodexIntegration = (options: ExternalCodexIntegrationOptions) => {
  const codexHome = path.resolve(options.homeDir, ".codex");
  const configPath = path.resolve(codexHome, "config.toml");
  const skillRoot = path.resolve(codexHome, "skills");
  const integrationRoot = path.resolve(options.appDataDir, "external-codex");
  const launcherPath = path.resolve(
    integrationRoot,
    process.platform === "win32" ? "finejob-mcp.cmd" : "finejob-mcp"
  );
  const skillSourceRoot = path.resolve(
    options.workspaceRoot,
    "apps",
    "desktop",
    "resources",
    "codex",
    "skills"
  );

  const status = (): ExternalCodexIntegrationStatus => ({
    mcp: {
      configPath,
      installed: fs.existsSync(launcherPath) && fs.existsSync(configPath),
      launcherPath
    },
    skills: MANAGED_SKILLS.map((name) => {
      const targetPath = path.resolve(skillRoot, name);
      return {
        name,
        path: targetPath,
        installed: fs.existsSync(path.resolve(targetPath, "SKILL.md"))
      };
    })
  });

  const writeLauncher = () => {
    fs.mkdirSync(integrationRoot, { recursive: true });
    if (process.platform === "win32") {
      // 启动器固定使用当前开发工作区，外部 Codex 新会话会加载最新 MCP 源码。
      fs.writeFileSync(
        launcherPath,
        [
          "@echo off",
          "setlocal",
          `cd /d ${quoteBatchValue(options.workspaceRoot)}`,
          `set \"FINE_JOB_BACKEND_ORIGIN=${options.backendOrigin}\"`,
          'set "FINE_JOB_MCP_LOCAL_EXTERNAL=1"',
          `${quoteBatchValue(options.pythonPath)} -m backend.app.mcp.fine_job_server`,
          ""
        ].join("\r\n"),
        "utf8"
      );
      return;
    }
    fs.writeFileSync(
      launcherPath,
      [
        "#!/usr/bin/env sh",
        `cd ${quoteShellValue(options.workspaceRoot)}`,
        `export FINE_JOB_BACKEND_ORIGIN=${quoteShellValue(options.backendOrigin)}`,
        "export FINE_JOB_MCP_LOCAL_EXTERNAL=1",
        `exec ${quoteShellValue(options.pythonPath)} -m backend.app.mcp.fine_job_server`,
        ""
      ].join("\n"),
      "utf8"
    );
    fs.chmodSync(launcherPath, 0o755);
  };

  return {
    installMcp() {
      writeLauncher();
      fs.mkdirSync(codexHome, { recursive: true });
      const existingConfig = fs.existsSync(configPath) ? fs.readFileSync(configPath, "utf8") : "";
      const section = [
        "[mcp_servers.finejob]",
        `command = ${toTomlString(launcherPath)}`,
        "args = []",
        ""
      ].join("\n");
      // 仅替换 FineJob 自己的配置段，保留用户已有的 Codex 配置与其他 MCP。
      fs.writeFileSync(configPath, replaceFineJobMcpSection(existingConfig, section), "utf8");
      return status();
    },
    installSkills() {
      fs.mkdirSync(skillRoot, { recursive: true });
      for (const name of MANAGED_SKILLS) {
        const sourcePath = path.resolve(skillSourceRoot, name);
        const targetPath = path.resolve(skillRoot, name);
        if (!fs.existsSync(sourcePath)) {
          throw new Error(`缺少 FineJob Skill 资源：${sourcePath}`);
        }
        // 每次刷新都以项目中的当前 Skill 为准，便于开发期间独立更新提示词和流程。
        fs.rmSync(targetPath, { recursive: true, force: true });
        fs.cpSync(sourcePath, targetPath, { recursive: true });
      }
      return status();
    },
    status
  };
};
