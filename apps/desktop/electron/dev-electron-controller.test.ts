import { describe, expect, it, vi } from "vitest";

import { createElectronDevController } from "../scripts/dev-electron-controller.js";

describe("createElectronDevController", () => {
  it("restarts Electron after an output file changes", () => {
    const kill = vi.fn();
    const once = vi.fn();
    const processA = { kill, once };
    const processB = { kill: vi.fn(), once: vi.fn() };
    const spawnElectron = vi.fn().mockReturnValueOnce(processA).mockReturnValueOnce(processB);
    const scheduled: Array<() => void> = [];
    const setTimeoutFn = vi.fn((callback: () => void) => {
      scheduled.push(callback);
      return scheduled.length;
    });
    const clearTimeoutFn = vi.fn();

    const controller = createElectronDevController({
      spawnElectron,
      setTimeoutFn,
      clearTimeoutFn
    });

    controller.start();
    controller.scheduleRestart("main.cjs");

    expect(spawnElectron).toHaveBeenCalledTimes(1);
    expect(kill).not.toHaveBeenCalled();
    expect(scheduled).toHaveLength(1);

    scheduled[0]?.();

    expect(kill).toHaveBeenCalledTimes(1);
    expect(spawnElectron).toHaveBeenCalledTimes(2);
  });

  it("coalesces repeated output changes into a single restart", () => {
    const processA = { kill: vi.fn(), once: vi.fn() };
    const processB = { kill: vi.fn(), once: vi.fn() };
    const spawnElectron = vi.fn().mockReturnValueOnce(processA).mockReturnValueOnce(processB);
    const scheduled: Array<() => void> = [];
    const setTimeoutFn = vi.fn((callback: () => void) => {
      scheduled.push(callback);
      return scheduled.length;
    });
    const clearTimeoutFn = vi.fn();

    const controller = createElectronDevController({
      spawnElectron,
      setTimeoutFn,
      clearTimeoutFn
    });

    controller.start();
    controller.scheduleRestart("main.cjs");
    controller.scheduleRestart("preload.cjs");

    expect(setTimeoutFn).toHaveBeenCalledTimes(2);
    expect(clearTimeoutFn).toHaveBeenCalledTimes(1);

    scheduled.at(-1)?.();

    expect(processA.kill).toHaveBeenCalledTimes(1);
    expect(spawnElectron).toHaveBeenCalledTimes(2);
  });
});
