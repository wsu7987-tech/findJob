/** 统一 Action 的展示只转换后端状态和原因，不参与业务判断。 */
export const fineJobActionStatusLabel = (status?: string | null): string => ({
  waiting: "等待条件满足",
  awaiting_confirmation: "等待确认",
  queued: "待执行",
  claimed: "已领取",
  preflighting: "执行前检查",
  dispatching: "正在执行",
  accepted: "已发出，等待平台确认",
  unknown: "结果无法确认",
  succeeded: "已确认完成",
  blocked: "已阻止",
  cancelled: "已取消",
  superseded: "已被新版本替代",
  stale: "已失效",
  failed: "执行失败"
} as Record<string, string>)[status ?? ""] ?? (status || "状态待确认");

export const fineJobActionTypeLabel = (type?: string | null): string => ({
  greeting: "打招呼",
  chat_message: "聊天回复",
  resume_send: "发送简历"
} as Record<string, string>)[type ?? ""] ?? (type || "动作");

export const fineJobActionReasonLabel = (reason?: string | null, detail?: string | null): string => {
  const label = ({
    classification_uncertain: "无法确认新消息与当前回复的关系，自动发送已暂停",
    resume_attachment_invalid: "已选简历已失效，请重新选择",
    snapshot_timeout: "无法读取最新附件列表",
    account_identity_required: "账号身份信息不足",
    identity_mismatch: "当前页面与目标任务不匹配",
    unknown: "已尝试执行，但平台结果无法确认，请人工核对",
    replacement_reply_pending: "等待新版本回复草稿准备完成",
    independent_followup_pending: "等待后续回复准备完成",
    send_disabled: "发送开关已关闭",
    session_sequence_required: "等待会话顺序确定"
  } as Record<string, string>)[reason ?? ""];
  return label || detail || reason || "";
};

export const fineJobActionStatusType = (status?: string | null): "success" | "warning" | "danger" | "info" => {
  if (status === "succeeded") return "success";
  if (["failed", "blocked", "unknown"].includes(status ?? "")) return "danger";
  if (["waiting", "awaiting_confirmation", "claimed", "preflighting", "dispatching", "accepted"].includes(status ?? "")) return "warning";
  return "info";
};
