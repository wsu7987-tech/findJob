import type {
  ApiErrorShape,
  ApiPoolItem,
  ApiRunSnapshot,
  PollFallbackInput,
  UiRunSnapshot
} from "../types";
import { formatDateTime } from "./format";

const poolStatusMap: Record<string, string> = {
  pending: "待总结",
  running: "总结中",
  succeeded: "已完成",
  failed: "失败"
};

const sourceTypeMap: Record<string, string> = {
  url: "网页链接",
  pdf: "PDF 文件",
  markdown: "Markdown 文档",
  text: "纯文本"
};

const runTaskTypeMap: Record<string, string> = {
  summary: "总结",
  report: "周报"
};

const runStageMap: Record<string, string> = {
  queued: "排队中",
  pending: "待启动",
  running: "进行中",
  summarizing: "总结中",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消"
};

const errorCategoryMap: Record<string, string> = {
  VALIDATION_FAILED: "输入校验失败",
  CONFIG_INVALID: "配置无效",
  FETCH_FAILED: "抓取失败",
  PARSE_FAILED: "解析失败",
  LLM_FAILED: "LLM 调用失败",
  EMBEDDING_FAILED: "Embedding 生成失败",
  RELATE_FAILED: "关联分析失败",
  OUTPUT_FAILED: "导出失败",
  DB_FAILED: "数据写入失败",
  CANCELLED: "已取消",
  UNKNOWN: "未知错误"
};

const apiErrorMap: Record<string, string> = {
  VALIDATION_FAILED: "输入内容未通过校验，请检查来源和内容格式。",
  CONFIG_INVALID: "当前配置不完整，请先补齐模型与输出目录。",
  FETCH_FAILED: "内容抓取失败，请稍后重试或更换来源。",
  PARSE_FAILED: "内容解析失败，请检查文件或文本格式。",
  LLM_FAILED: "总结生成失败，模型调用没有完成。",
  EMBEDDING_FAILED: "向量生成失败，暂时无法完成关联计算。",
  RELATE_FAILED: "关联分析失败，但原始记录已保留。",
  OUTPUT_FAILED: "导出文档失败，请检查输出目录权限。",
  DB_FAILED: "数据写入失败，请检查本地数据库状态。",
  CANCELLED: "任务已取消。",
  UNKNOWN: "发生了未知错误，请查看详情页。"
};

export const mapPoolStatus = (status: string) => poolStatusMap[status] ?? "未知状态";

export const mapSourceTypeLabel = (sourceType: string) => sourceTypeMap[sourceType] ?? sourceType;

export const mapRunTaskTypeLabel = (taskType: string) => runTaskTypeMap[taskType] ?? taskType;

export const mapRunStageLabel = (stage: string) => runStageMap[stage] ?? stage;

export const mapErrorCategoryLabel = (category: string | null | undefined) =>
  (category && errorCategoryMap[category]) ?? category ?? "无";

export const mapApiError = (
  payload?: ApiErrorShape & { statusCode?: number | null; fallbackMessage?: string | null }
) => {
  if (!payload) {
    return "请求失败，请稍后再试。";
  }

  if (payload.error_category && apiErrorMap[payload.error_category]) {
    return apiErrorMap[payload.error_category];
  }

  if (payload.statusCode === 404 || payload.statusCode === 501) {
    return "当前后端暂未提供这个接口，请确认后端版本是否已更新。";
  }

  if (typeof payload.detail === "string" && payload.detail.length > 0) {
    return payload.detail;
  }

  if (payload.fallbackMessage) {
    return payload.fallbackMessage;
  }

  return "请求失败，请稍后再试。";
};

export const normalizeRunSnapshot = (payload: ApiRunSnapshot): UiRunSnapshot => {
  const totalProcessed = payload.succeeded_items + payload.failed_items + payload.skipped_items;
  const progressPercent =
    payload.total_items > 0
      ? Math.min(100, Math.round((totalProcessed / payload.total_items) * 100))
      : payload.status === "completed"
        ? 100
        : 0;

  return {
    runId: payload.run_id,
    taskType: payload.task_type,
    status: payload.status,
    stage: payload.stage,
    totalItems: payload.total_items,
    succeededItems: payload.succeeded_items,
    failedItems: payload.failed_items,
    skippedItems: payload.skipped_items,
    totalProcessed,
    progressPercent,
    currentItemId: payload.current_item_id,
    currentItemLabel: payload.current_item_label,
    errorCategory: payload.error_category,
    errorMessage: payload.error_message,
    updatedAt: formatDateTime(payload.updated_at),
    finishedAt: payload.finished_at ? formatDateTime(payload.finished_at) : null,
    reportWeekKey: payload.report_week_key ?? null,
    linkedReportVersionId: payload.linked_report_version_id ?? null,
    resultSnapshots: payload.result_snapshots ?? []
  };
};

export const shouldFallbackToPolling = ({
  lastEventAt,
  now,
  thresholdMs,
  currentMode
}: PollFallbackInput) => currentMode === "sse" && now - lastEventAt > thresholdMs;

export const deriveSourceLabel = (
  item: Pick<ApiPoolItem, "source_type" | "source_value" | "title" | "source_name">
) => {
  if (item.source_name) {
    return item.source_name;
  }

  if (item.title) {
    return item.title;
  }

  if (item.source_type === "url") {
    try {
      return new URL(item.source_value).hostname;
    } catch {
      return item.source_value;
    }
  }

  if (item.source_type === "pdf" || item.source_type === "markdown") {
    return item.source_value.split(/[\\/]/).pop() ?? item.source_value;
  }

  return item.source_value.length > 36
    ? `${item.source_value.slice(0, 36)}...`
    : item.source_value;
};

export const isRunTerminalStatus = (status: string) =>
  ["completed", "failed", "cancelled"].includes(status);

export const formatWeekLabel = (weekKey: string) => {
  const [, week] = weekKey.split("-W");
  return `第 ${week} 周 · ${weekKey}`;
};
