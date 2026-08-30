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
}): boolean => fineJobChatConfirmBlocker(input) === "";

export const fineJobChatConfirmBlocker = (input: {
  runtime: FineJobChatRuntime | null;
  session: FineJobChatSession | null;
  task: FineJobChatReplyTask | null;
  finalText: string;
  leaderAvailable: boolean;
}): string => {
  if (!input.session) return "请先选择聊天会话。";
  if (input.session.status === "unsupported") return "聊天对象身份不完整，请先在 BOSS 打开对应会话。";
  if (input.session.status === "paused") return "当前会话已暂停。";
  if (input.session.status === "human_takeover") return "当前会话已由人工接管。";
  if (!input.session.encrypt_peer_uid || !input.session.security_id || !input.session.encrypt_job_id) {
    return "聊天对象身份不完整，暂时不能发送。";
  }
  if (!input.task) return "当前没有可确认的回复草稿。";
  if (input.task.status !== "awaiting_review") return "回复草稿尚未进入待确认状态。";
  if (!input.finalText.trim()) return "回复正文不能为空。";
  if (!input.runtime?.send_enabled) return "发送权限尚未开启。";
  if (!input.leaderAvailable) return "等待对应 BOSS 账号的插件领导标签页上线。";
  return "";
};

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
