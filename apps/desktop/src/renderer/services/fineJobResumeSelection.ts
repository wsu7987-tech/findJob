import type { FineJobBossResumeAttachment } from "@/types";

/** 保留用户选择，没有选择时默认使用附件列表第一项。 */
export const resolveFineJobResumeSelection = (
  attachments: FineJobBossResumeAttachment[],
  selectedId: string
): string => {
  if (!attachments.length) return "";
  return attachments.some((item) => item.resumeId === selectedId)
    ? selectedId
    : attachments[0].resumeId;
};
