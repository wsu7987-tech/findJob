import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it, vi } from "vitest";

const ptyMocks = vi.hoisted(() => ({ spawn: vi.fn() }));

vi.mock("node-pty", () => ({ spawn: ptyMocks.spawn }));

import {
  buildCodexExitMessage,
  buildCodexInteractiveArgs,
  createCodexSessionController,
  FINEJOB_TRANSPORT_DEBUG_SUBMIT_CANDIDATES,
  FINEJOB_WORKFLOW_COMPOSER_SUBMIT_BINDING,
  FINEJOB_WORKFLOW_COMPOSER_SUBMIT_SEQUENCE,
  isResumableCodexSessionId,
  writeManagedWorkspace
} from "./codex-session";

let cleanupAppDataDir = "";

afterEach(() => {
  if (cleanupAppDataDir) fs.rmSync(cleanupAppDataDir, { recursive: true, force: true });
  cleanupAppDataDir = "";
  vi.useRealTimers();
  ptyMocks.spawn.mockReset();
});

const createSessionController = () => {
  const handlers: { data?: (data: string) => void; exit?: (value: { exitCode: number }) => void } = {};
  const terminal = {
    write: vi.fn(),
    resize: vi.fn(),
    kill: vi.fn(),
    onData: vi.fn((handler) => { handlers.data = handler; }),
    onExit: vi.fn((handler) => { handlers.exit = handler; })
  };
  ptyMocks.spawn.mockReturnValue(terminal);
  cleanupAppDataDir = fs.mkdtempSync(path.join(os.tmpdir(), "finejob-codex-submit-"));
  const workspaceRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
  const controller = createCodexSessionController({
    appDataDir: cleanupAppDataDir,
    workspaceRoot,
    backendOrigin: "http://127.0.0.1:8000",
    pythonPath: "python",
    getCodexPath: async () => "codex",
    createRuntime: async () => ({ run_id: "runtime-1", token: "token-1", expires_at: "" }),
    completeRuntime: async () => {},
    emit: () => {},
    debugLog: () => {}
  });
  return { controller, handlers, terminal };
};

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
      const fineJobNodes = fs.readFileSync(
        path.resolve(skillsDir, "finejob", "references", "job-task-nodes.md"),
        "utf8"
      );
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
      expect(fineJobSkill).toContain("## deep_job_search Workflow");
      expect(fineJobNodes).toContain("# 岗位任务节点");
      expect(profileSkill).toContain("name: finejob-profile");
      expect(profileRules).toContain("# 分析规则");
    } finally {
      fs.rmSync(appDataDir, { recursive: true, force: true });
    }
  });
});

describe("buildCodexInteractiveArgs", () => {
  it("Workflow 恢复只使用明确的 Session Ref，并传递模型与推理配置", () => {
    const args = buildCodexInteractiveArgs({
      tuiWorkspace: "D:/workflow-workspace",
      resumeSessionRef: "6bbf9b35-d4d4-45a5-bfb4-8f8f1ed544f0",
      model: "gpt-5.6-luna",
      reasoningEffort: "high",
    });

    expect(args).toContain("6bbf9b35-d4d4-45a5-bfb4-8f8f1ed544f0");
    expect(args).not.toContain("--last");
    expect(args).toContain("gpt-5.6-luna");
    expect(args).toContain('model_reasoning_effort="high"');
    expect(isResumableCodexSessionId("runtime:workflow-runtime-123")).toBe(false);
    expect(isResumableCodexSessionId("workflow-session-123")).toBe(false);
    expect(isResumableCodexSessionId("6bbf9b35-d4d4-45a5-bfb4-8f8f1ed544 f0")).toBe(false);
    expect(isResumableCodexSessionId("6bbf9b35-d4d4-45a5-bfb4-8f8f1ed544f0")).toBe(true);
  });

  it("只有 Workflow 启动参数注入专用 composer submit 绑定", () => {
    const workflowArgs = buildCodexInteractiveArgs({
      tuiWorkspace: "D:/workflow-workspace",
      workflowComposerSubmitBinding: FINEJOB_WORKFLOW_COMPOSER_SUBMIT_BINDING
    });
    const standardArgs = buildCodexInteractiveArgs({ tuiWorkspace: "D:/standard-workspace" });

    expect(workflowArgs).toContain(`tui.keymap.composer.submit=\"${FINEJOB_WORKFLOW_COMPOSER_SUBMIT_BINDING}\"`);
    expect(standardArgs.join(" ")).not.toContain("tui.keymap.composer.submit");
  });
});

describe("Workflow Prompt transport", () => {
  it("Prompt 写入后的新 PTY 输出触发 Workflow 专用提交键", async () => {
    const { controller, handlers, terminal } = createSessionController();
    await controller.startWorkflow({ model: "gpt-5.6-luna", reasoningEffort: "high" });
    expect(ptyMocks.spawn.mock.calls[0]?.[1]).toContain(
      `tui.keymap.composer.submit=\"${FINEJOB_WORKFLOW_COMPOSER_SUBMIT_BINDING}\"`
    );
    handlers.data?.("Codex ready");

    const submitted = controller.submitWorkflowPrompt("workflow prompt");
    expect(terminal.write).toHaveBeenCalledWith("workflow prompt");
    expect(terminal.write).toHaveBeenCalledTimes(1);
    handlers.data?.("workflow prompt echo");

    await expect(submitted).resolves.toBe(true);
    expect(terminal.write).toHaveBeenNthCalledWith(1, "workflow prompt");
    expect(terminal.write).toHaveBeenNthCalledWith(2, FINEJOB_WORKFLOW_COMPOSER_SUBMIT_SEQUENCE);
    expect(terminal.write).toHaveBeenCalledWith("\r");
  });

  it("没有新输出时在 750ms 回退后发送 Workflow 专用提交键", async () => {
    vi.useFakeTimers();
    const { controller, handlers, terminal } = createSessionController();
    await controller.startWorkflow({ model: "gpt-5.6-luna", reasoningEffort: "high" });
    handlers.data?.("Codex ready");

    const submitted = controller.submitWorkflowPrompt("workflow prompt");
    expect(terminal.write).toHaveBeenCalledWith("workflow prompt");
    await vi.advanceTimersByTimeAsync(749);
    expect(terminal.write).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    await vi.advanceTimersByTimeAsync(300);

    await expect(submitted).resolves.toBe(true);
    expect(terminal.write).toHaveBeenNthCalledWith(2, FINEJOB_WORKFLOW_COMPOSER_SUBMIT_SEQUENCE);
    expect(terminal.write).toHaveBeenCalledWith("\r");
  });

  it("再次提交只发送 Workflow 专用提交键", async () => {
    const { controller, terminal } = createSessionController();
    await controller.startWorkflow({ model: "gpt-5.6-luna", reasoningEffort: "high" });
    await controller.startWorkflow({
      model: "gpt-5.6-luna",
      reasoningEffort: "high",
      sessionRef: "runtime:runtime-1"
    });

    await expect(controller.submitWorkflowKey()).resolves.toBe(true);

    expect(ptyMocks.spawn).toHaveBeenCalledTimes(1);
    expect(terminal.write).toHaveBeenCalledTimes(1);
    expect(terminal.write).toHaveBeenCalledWith(FINEJOB_WORKFLOW_COMPOSER_SUBMIT_SEQUENCE);
  });

  it("普通 Codex 会话固定 Enter 提交并保留 Shift+Enter 换行", async () => {
    const { controller, terminal } = createSessionController();
    await controller.start();

    expect(ptyMocks.spawn.mock.calls[0]?.[1]).toContain('tui.keymap.composer.submit="enter"');
    expect(ptyMocks.spawn.mock.calls[0]?.[1]).toContain('tui.keymap.editor.insert_newline=["shift-enter"]');
    await expect(controller.submitWorkflowKey()).resolves.toBe(false);
    expect(terminal.write).not.toHaveBeenCalled();
  });

  it("Transport Debug 会话按选定候选注入 keymap，写入 Prompt 与提交键保持分离", async () => {
    const { controller, terminal } = createSessionController();
    const candidate = FINEJOB_TRANSPORT_DEBUG_SUBMIT_CANDIDATES[1];
    await controller.startTransportDebug(undefined, undefined, candidate.id);

    expect(ptyMocks.spawn.mock.calls[0]?.[1]).toContain(
      `tui.keymap.composer.submit=\"${candidate.binding}\"`
    );
    await expect(controller.writeTransportDebugPrompt("请只回复：FINEJOB_SUBMIT_OK")).resolves.toBe(true);
    expect(terminal.write).toHaveBeenCalledWith("请只回复：FINEJOB_SUBMIT_OK");
    expect(terminal.write).not.toHaveBeenCalledWith(candidate.keySequence);

    await expect(controller.submitTransportDebugKey()).resolves.toBe(true);
    expect(terminal.write).toHaveBeenNthCalledWith(2, candidate.keySequence);
    expect(terminal.write).not.toHaveBeenCalledWith("\r");
  });

  it("Transport Debug 信息提供可比较的候选键", () => {
    const { controller } = createSessionController();

    expect(controller.transportDebugInfo().candidates).toEqual(
      FINEJOB_TRANSPORT_DEBUG_SUBMIT_CANDIDATES.map(({ id, binding, keySequenceDisplay }) => ({
        id,
        binding,
        keySequence: keySequenceDisplay
      }))
    );
  });
});
