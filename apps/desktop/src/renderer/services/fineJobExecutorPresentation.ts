import type { FineJobBossExecutorInstance } from "@/types";

/** 运行状态只读取执行器和当前任务的真实字段。 */
export const fineJobExecutorStatusLabel = (
  executor: FineJobBossExecutorInstance | null,
  hasCurrentTask: boolean
): string => {
  if (!executor) return "未配对";
  if (executor.pairing_state === "revoked") return "已断开，需重新配对";
  if (!executor.browser_connected) return "连接已中断";
  if (executor.risk_state !== "none") return "风险暂停";
  if (executor.runtime_phase === "task_cooldown") return "冷却中";
  if (hasCurrentTask) return "执行中";
  return executor.queue_state === "running" ? "运行中" : "已暂停";
};
