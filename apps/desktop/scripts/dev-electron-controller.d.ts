export interface ElectronChildProcessLike {
  kill: () => void;
  once: (event: "exit", callback: () => void) => void;
}

export interface ElectronDevControllerOptions {
  spawnElectron: () => ElectronChildProcessLike;
  onElectronExit?: () => void;
  setTimeoutFn?: (callback: () => void, delay?: number) => unknown;
  clearTimeoutFn?: (timer: unknown) => void;
}

export interface ElectronDevController {
  start: () => ElectronChildProcessLike;
  scheduleRestart: (reason: string) => void;
  shutdown: () => void;
}

export function createElectronDevController(
  options: ElectronDevControllerOptions
): ElectronDevController;
