import type {
  FineJobChatReplyTask,
  FineJobChatRuntime,
  FineJobChatSession
} from "@/types";


export const canConfirmFineJobChatReply = (input: {
  runtime: FineJobChatRuntime | null;
  session: FineJobChatSession | null;
  task: FineJobChatReplyTask | null;
  finalText: string;
  leaderAvailable: boolean;
}): boolean => Boolean(
  input.runtime?.send_enabled
    && input.leaderAvailable
    && input.session?.status === "active"
    && input.task?.status === "awaiting_review"
    && input.finalText.trim()
);

export const fineJobChatSendStatusLabel = (status: string): string => ({
  queued: "等待插件领取",
  leased: "插件已领取",
  dispatching: "正在提交",
  accepted: "已提交发送",
  failed: "发送失败",
  unknown: "结果未知",
  cancelled: "已取消"
})[status] ?? status;

// 页面轮询只有在回复任务切换时才重置编辑器，避免覆盖同一草稿上的人工修改。
export const shouldSyncFineJobChatEditor = (
  currentTaskId: string | null | undefined,
  nextTaskId: string | null | undefined
): boolean => currentTaskId !== nextTaskId;
