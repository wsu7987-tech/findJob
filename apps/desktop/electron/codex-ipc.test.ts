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
      start: vi.fn(async () => ({ status: "running", runId: "run-1" })),
      resume: vi.fn(async () => ({ status: "running", runId: "run-1" })),
      write: vi.fn(),
      resize: vi.fn(),
      interrupt: vi.fn(),
      stop: vi.fn(),
      state: vi.fn(() => ({ status: "idle", runId: null }))
    };

    registerCodexIpc(ipcMain as never, controller, () => null);
    await handlers.get("codex:start")?.({}, { cols: 100, rows: 30 });
    listeners.get("codex:input")?.({}, "你好");
    listeners.get("codex:resize")?.({}, { cols: 120, rows: 40 });

    expect(controller.start).toHaveBeenCalledWith(100, 30);
    expect(controller.write).toHaveBeenCalledWith("你好");
    expect(controller.resize).toHaveBeenCalledWith(120, 40);
  });

  it("限制超出边界的 IPC 输入", () => {
    const listeners = new Map<string, (...args: any[]) => unknown>();
    const ipcMain = {
      handle: vi.fn(),
      on: vi.fn((channel: string, handler: (...args: any[]) => unknown) => listeners.set(channel, handler))
    };
    const controller = {
      start: vi.fn(), resume: vi.fn(), write: vi.fn(), resize: vi.fn(),
      interrupt: vi.fn(), stop: vi.fn(), state: vi.fn()
    };
    registerCodexIpc(ipcMain as never, controller, () => null);
    listeners.get("codex:input")?.({}, { unexpected: true });
    listeners.get("codex:resize")?.({}, { cols: 10.5, rows: 30 });
    expect(controller.write).not.toHaveBeenCalled();
    expect(controller.resize).not.toHaveBeenCalled();
  });
});
