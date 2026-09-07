import type { FineJobBossResumeAttachment } from "@/types";

/** 根据附件数量保留明确选择，唯一附件由界面自动选中。 */
export const resolveFineJobResumeSelection = (
  attachments: FineJobBossResumeAttachment[],
  selectedId: string
): string => {
  if (attachments.length === 1) return attachments[0].encryptResumeId;
  return attachments.some((item) => item.encryptResumeId === selectedId) ? selectedId : "";
};
