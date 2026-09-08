import type { ResumeSnapshotCommand, ResumeSnapshotResult } from "../../../finejob/types";
import type { BossChatSender } from "./sender";

/** 只读附件列表并收敛为 Preflight 使用的最小 snapshot 结果。 */
export const readResumeAttachmentSnapshot = async (
  command: ResumeSnapshotCommand,
  sender: Pick<BossChatSender, "listResumeAttachments">
): Promise<ResumeSnapshotResult> => {
  try {
    const attachments = await sender.listResumeAttachments();
    return {
      requestId: command.requestId,
      actionId: command.actionId,
      attachments: attachments.map((item) => ({
        encryptResumeId: item.encryptResumeId,
        filename: item.showName
      })),
      observedAt: new Date().toISOString(),
      error: ""
    };
  } catch (error) {
    return {
      requestId: command.requestId,
      actionId: command.actionId,
      attachments: [],
      observedAt: new Date().toISOString(),
      error: (error as Error).message || "resume_snapshot_failed"
    };
  }
};
