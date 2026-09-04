import type { BossProbeState } from "../platform/boss/types";
import type { ExecutorRuntimeState } from "../finejob/types";

export const FRAMEWORK_MODE = Object.freeze({
  name: "FineJob任务执行器",
  realActionsEnabled: true,
  fineJobConnected: true
});

export type FrameworkMode = typeof FRAMEWORK_MODE;

export type ComponentState = "checking" | "ready" | "error";

export type MainWorldStatus = {
  component: "main-world";
  frameworkMode: true;
  hostname: string;
  pathname: string;
  readyState: DocumentReadyState;
};

export type FrameworkStatus = {
  background: ComponentState;
  content: ComponentState;
  mainWorld: ComponentState;
  bossProbe: BossProbeState;
  page: string;
  detail: string;
  executor: ExecutorRuntimeState;
};

export const createFrameworkStatus = (pathname: string): FrameworkStatus => ({
  background: "checking",
  content: "ready",
  mainWorld: "checking",
  bossProbe: "waiting",
  page: pathname,
  detail: `正在检查三层扩展上下文；模式：${FRAMEWORK_MODE.name}`,
  executor: {
    connected: false,
    paired: false,
    detail: "尚未与FineJob配对",
    executor: null,
    queue: [],
    lastResult: ""
  }
});

export const isMainWorldStatus = (value: unknown): value is MainWorldStatus => {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Partial<MainWorldStatus>;
  return (
    candidate.component === "main-world" &&
    candidate.frameworkMode === true &&
    typeof candidate.hostname === "string" &&
    typeof candidate.pathname === "string" &&
    ["loading", "interactive", "complete"].includes(candidate.readyState ?? "")
  );
};

export const refreshFrameworkDetail = (status: FrameworkStatus): void => {
  if (status.background === "error") {
    status.detail = "Background 健康检查失败；真实动作已阻断";
    return;
  }
  if (status.mainWorld === "error") {
    status.detail = "Main World 健康检查失败；真实动作已阻断";
    return;
  }
  if (status.background === "ready" && status.mainWorld === "ready") {
    status.detail = status.executor.connected
      ? "三层扩展与FineJob通信正常"
      : "三层扩展正常，正在等待FineJob通信";
  }
};
