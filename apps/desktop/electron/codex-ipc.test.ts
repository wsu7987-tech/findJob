import { describe, expect, it, vi } from "vitest";

import { registerCodexIpc } from "./codex-ipc";

describe("registerCodexIpc", () => {
  it("注册受控的会话调用并转发终端输入", async () => {
    const handlers = new Map<string, (...args: any[]) => unknown>();
    const listeners = new Map<string, (...args: any[]) => unknown>();
    const ipcMain = {
      handle: vi.fn((channel: string, handler: (...args: any[]) => unknown) => handlers.set(channel, handler)),
      on: vi.fn((channel: string, handler: (...args: any[]) => unknown) => listeners.set(channel, handler))
    };
    const controller = {
      start: vi.fn(async () => ({ status: "running", runtimeId: "run-1", sessionRef: "runtime:run-1" })),
      resume: vi.fn(async () => ({ status: "running", runtimeId: "run-1", sessionRef: "runtime:run-1" })),
      startWorkflow: vi.fn(async () => ({ status: "running", runtimeId: "run-1", sessionRef: "runtime:run-1" })),
      startTransportDebug: vi.fn(async () => ({ status: "running", runtimeId: "debug-1", sessionRef: "runtime:debug-1" })),
      write: vi.fn(),
      submitWorkflowPrompt: vi.fn(async () => true),
      submitWorkflowKey: vi.fn(async () => true),
      writeTransportDebugPrompt: vi.fn(async () => true),
      submitTransportDebugKey: vi.fn(async () => true),
      submitEnter: vi.fn(async () => true),
      resize: vi.fn(),
      interrupt: vi.fn(),
      stop: vi.fn(),
      state: vi.fn(() => ({ status: "idle", runtimeId: null, sessionRef: null })),
      transportDebugInfo: vi.fn(() => ({ binding: "ctrl-y", keySequence: "\\x19", sessionMode: null, candidates: [] }))
    };

    registerCodexIpc(ipcMain as never, controller, () => null);
    await handlers.get("codex:start")?.({}, { cols: 100, rows: 30 });
    listeners.get("codex:input")?.({}, "你好");
    listeners.get("codex:resize")?.({}, { cols: 120, rows: 40 });

    expect(controller.start).toHaveBeenCalledWith(100, 30);
    expect(controller.write).toHaveBeenCalledWith("你好");
    expect(controller.resize).toHaveBeenCalledWith(120, 40);
    await handlers.get("codex:start-workflow")?.({}, {
      model: "gpt-5.6-luna", reasoningEffort: "high", sessionRef: "session-1"
    });
    expect(controller.startWorkflow).toHaveBeenCalledWith({
      model: "gpt-5.6-luna", reasoningEffort: "high", sessionRef: "session-1"
    });
    expect(handlers.get("codex:state")?.({})).toEqual({
      status: "idle",
      runtimeId: null,
      sessionRef: null
    });
    await expect(handlers.get("codex:submit-enter")?.({})).resolves.toBe(true);
    expect(controller.submitEnter).toHaveBeenCalledTimes(1);
    await expect(handlers.get("codex:submit-workflow-prompt")?.({}, "workflow prompt")).resolves.toBe(true);
    await expect(handlers.get("codex:submit-workflow-key")?.({})).resolves.toBe(true);
    expect(controller.submitWorkflowPrompt).toHaveBeenCalledWith("workflow prompt");
    expect(controller.submitWorkflowKey).toHaveBeenCalledTimes(1);
    await expect(handlers.get("codex:start-transport-debug")?.({}, { cols: 100, rows: 30, candidateId: "ctrl-y" })).resolves.toEqual({
      status: "running", runtimeId: "debug-1", sessionRef: "runtime:debug-1"
    });
    await expect(handlers.get("codex:write-transport-debug-prompt")?.({}, "debug prompt")).resolves.toBe(true);
    await expect(handlers.get("codex:submit-transport-debug-key")?.({})).resolves.toBe(true);
    expect(controller.startTransportDebug).toHaveBeenCalledWith(100, 30, "ctrl-y");
    expect(controller.writeTransportDebugPrompt).toHaveBeenCalledWith("debug prompt");
    expect(controller.submitTransportDebugKey).toHaveBeenCalledTimes(1);
  });

  it("限制超出边界的 IPC 输入", () => {
    const listeners = new Map<string, (...args: any[]) => unknown>();
    const ipcMain = {
      handle: vi.fn(),
      on: vi.fn((channel: string, handler: (...args: any[]) => unknown) => listeners.set(channel, handler))
    };
    const controller = {
      start: vi.fn(), resume: vi.fn(), startWorkflow: vi.fn(), startTransportDebug: vi.fn(), write: vi.fn(), submitWorkflowPrompt: vi.fn(),
      submitWorkflowKey: vi.fn(), writeTransportDebugPrompt: vi.fn(), submitTransportDebugKey: vi.fn(), submitEnter: vi.fn(), resize: vi.fn(),
      interrupt: vi.fn(), stop: vi.fn(), state: vi.fn(), transportDebugInfo: vi.fn()
    };
    registerCodexIpc(ipcMain as never, controller, () => null);
    listeners.get("codex:input")?.({}, { unexpected: true });
    listeners.get("codex:resize")?.({}, { cols: 10.5, rows: 30 });
    expect(controller.write).not.toHaveBeenCalled();
    expect(controller.resize).not.toHaveBeenCalled();
  });
});
